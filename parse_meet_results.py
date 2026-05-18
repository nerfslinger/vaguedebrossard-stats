#!/usr/bin/env python3
"""
parse_meet_results.py — parse Brossard meet results into canonical JSON.

Iterates meet_results/{year}/*.{zip,sd3}, filters each file to swims by
La Vague de Brossard (club code "BRO"), and emits:

    data/swims.jsonl                          canonical, one swim per line
    data/swimmers_index.json                  swimmer summary
    data/meets_index.json                     meet summary
    data/swimmers/{license}.json              one file per swimmer
    data/events/{cat}-{stroke}-{dist}-{pool}.json  one file per event-bucket

See meet_results_storage.md for the full schema and contract.

Usage:
    python3 parse_meet_results.py             # parse everything, write outputs
    python3 parse_meet_results.py --dry-run   # parse + print stats only
    python3 parse_meet_results.py --meet PATH # parse a single file (debug)
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
MEETS_DIR = ROOT / "meet_results"
OUT_DIR = ROOT / "data"

BROSSARD_CLUB_CODE = "BRO"
BROSSARD_NATION = "CAN"

# lenex stroke → french code used by the existing chronology
STROKE_FR = {
    "FREE": "lib",
    "BACK": "dos",
    "BREAST": "bra",
    "FLY": "pap",
    "MEDLEY": "qni",
}

# course → pool length in metres
COURSE_TO_POOL = {"SCM": 25, "LCM": 50}

AGE_BANDS = [
    (20, 24), (25, 29), (30, 34), (35, 39), (40, 44),
    (45, 49), (50, 54), (55, 59), (60, 64), (65, 69),
    (70, 74), (75, 79), (80, 84), (85, 89),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def time_to_seconds(t: str | None) -> float | None:
    """Convert 'HH:MM:SS.ss' or 'MM:SS.ss' or 'SS.ss' → seconds (float).

    Returns None if t is None, empty, or all-zeros.
    """
    if not t or t in ("00:00:00.00", "00:00:00"):
        return None
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])
    except ValueError:
        return None


def age_at(dob_iso: str | None, meet_date_iso: str) -> int | None:
    if not dob_iso:
        return None
    try:
        dob = date.fromisoformat(dob_iso)
        md = date.fromisoformat(meet_date_iso)
    except ValueError:
        return None
    years = md.year - dob.year - ((md.month, md.day) < (dob.month, dob.day))
    return years


def age_group(age: int | None) -> str | None:
    if age is None:
        return None
    if age < 20:
        # Masters cross-over rule: swimmers competing the year they turn 20
        # are placed in 20-24. The chronology dataset has no U20 band, so we
        # collapse here for consistency.
        return "20-24"
    for lo, hi in AGE_BANDS:
        if lo <= age <= hi:
            return f"{lo}-{hi}"
    return "90+"


def unaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def slugify(s: str) -> str:
    s = unaccent(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def display_name(firstname: str, lastname: str) -> str:
    f = (firstname or "").strip()
    l = (lastname or "").strip()
    return f"{f} {l}".strip()


def meet_id_from_filename(path: Path) -> str:
    """meet_results/2025/2025-10-18_Coupe-des-maitres-Manche-1.zip
       → 2025-10-18-coupe-des-maitres-manche-1
    """
    stem = path.stem  # drops .zip / .sd3
    # if filename starts with [12345] (Splash meet number), drop it
    stem = re.sub(r"^\[\d+\]\s*", "", stem)
    # convert first '_' (between date and name) to '-'
    stem = stem.replace("_", "-")
    return slugify(stem)


# ---------------------------------------------------------------------------
# Lenex parsing
# ---------------------------------------------------------------------------

def parse_lenex(xml_bytes: bytes, meet_id: str, source_path: Path) -> tuple[dict, list[dict]]:
    """Parse a lenex XML document and return (meet_meta, [swim, ...]).

    Only Brossard individual swims (relaycount=1) are returned.
    """
    root = ET.fromstring(xml_bytes)

    meet = root.find(".//MEET")
    if meet is None:
        raise ValueError(f"{source_path}: no MEET element")

    meet_meta = {
        "meet_id":     meet_id,
        "name":        meet.get("name", "").strip(),
        "date":        None,   # filled below from primary session
        "city":        meet.get("city", "").strip(),
        "course":      meet.get("course", ""),
        "nation":      meet.get("nation", ""),
        "state":       meet.get("state", ""),
        "source":      "lenex",
        "source_file": source_path.name,
    }

    # Build event lookup: eventid → {date, stroke, distance, pool, relaycount}
    events: dict[str, dict] = {}
    session_dates: list[str] = []
    for session in meet.iter("SESSION"):
        sdate = session.get("date") or ""
        if sdate:
            session_dates.append(sdate)
        for evt in session.iter("EVENT"):
            eid = evt.get("eventid")
            sw = evt.find("SWIMSTYLE")
            if eid is None or sw is None:
                continue
            try:
                distance = int(sw.get("distance") or 0)
            except ValueError:
                distance = 0
            try:
                relaycount = int(sw.get("relaycount") or 1)
            except ValueError:
                relaycount = 1
            events[eid] = {
                "date":       sdate,
                "stroke":     sw.get("stroke") or "",
                "distance":   distance,
                "relaycount": relaycount,
            }

    # Primary meet date = earliest session date
    meet_meta["date"] = min(session_dates) if session_dates else ""

    pool = COURSE_TO_POOL.get(meet_meta["course"])
    if pool is None:
        # fall back: try to infer from any session/event; otherwise leave as None
        pool = 25  # most meets are short-course

    swims: list[dict] = []

    for club in meet.iter("CLUB"):
        if club.get("code") != BROSSARD_CLUB_CODE:
            continue
        if club.get("nation") and club.get("nation") != BROSSARD_NATION:
            continue

        for ath in club.iter("ATHLETE"):
            firstname = ath.get("firstname", "")
            lastname  = ath.get("lastname", "")
            license_  = ath.get("license", "")
            dob       = ath.get("birthdate") or None
            gender    = ath.get("gender", "")

            name = display_name(firstname, lastname)
            slug = slugify(name)

            for result in ath.iter("RESULT"):
                eid = result.get("eventid")
                event_meta = events.get(eid)
                if event_meta is None:
                    continue  # orphan result, skip
                if event_meta["relaycount"] != 1:
                    continue  # relay leg handled separately (v2)

                stroke = event_meta["stroke"]
                distance = event_meta["distance"]
                meet_date_for_swim = event_meta["date"] or meet_meta["date"]

                time_sec = time_to_seconds(result.get("swimtime"))
                entrytime_sec = time_to_seconds(result.get("entrytime"))

                status = (result.get("status") or "").strip() or "OK"

                # splits
                splits_sec: list[float] = []
                splits_node = result.find("SPLITS")
                if splits_node is not None:
                    for sp in splits_node.findall("SPLIT"):
                        sec = time_to_seconds(sp.get("swimtime"))
                        if sec is not None:
                            splits_sec.append(round(sec, 2))

                age = age_at(dob, meet_date_for_swim) if meet_date_for_swim else None
                ag = age_group(age)

                event_key = f"{gender}-{stroke}-{distance}-{pool}"
                bucket_key = f"{gender}-{ag}-{stroke}-{distance}-{pool}" if ag else event_key

                swimmer_id = license_ or f"slug-{slug}"

                swim_id_date = (meet_date_for_swim or meet_meta["date"] or "").replace("-", "")
                swim_id = f"{swim_id_date}-{swimmer_id}-{distance}-{stroke}-{pool}"

                swim = {
                    "swim_id":       swim_id,
                    "swimmer_id":    swimmer_id,
                    "swimmer_slug":  slug,
                    "swimmer_name":  name,
                    "firstname":     firstname,
                    "lastname":      lastname,

                    "gender":        gender,
                    "dob":           dob,
                    "age_at_meet":   age,
                    "age_group":     ag,

                    "stroke":        stroke,
                    "stroke_fr":     STROKE_FR.get(stroke, stroke.lower()),
                    "distance":      distance,
                    "pool":          pool,
                    "relay":         False,
                    "event_key":     event_key,
                    "bucket_key":    bucket_key,

                    "time_sec":      round(time_sec, 2) if time_sec is not None else None,
                    "splits_sec":    splits_sec,
                    "status":        status,
                    "place":         None,  # not in lenex spec attrs Splash emits
                    "points":        _int_or_none(result.get("points")),
                    "entrytime_sec": round(entrytime_sec, 2) if entrytime_sec is not None else None,

                    "meet_id":       meet_meta["meet_id"],
                    "meet_name":     meet_meta["name"],
                    "meet_date":     meet_date_for_swim or meet_meta["date"],
                    "meet_city":     meet_meta["city"],
                    "meet_course":   meet_meta["course"],
                    "meet_source":   "lenex",

                    "set_record":    False,
                    "record_key":    None,
                }
                swims.append(swim)

    return meet_meta, swims


def _int_or_none(s: str | None) -> int | None:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-file dispatch
# ---------------------------------------------------------------------------

def parse_meet_file(path: Path) -> tuple[dict | None, list[dict]]:
    meet_id = meet_id_from_filename(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            lef_names = [n for n in zf.namelist() if n.lower().endswith(".lef")]
            if not lef_names:
                # zip with only .sd3 — handle in sd3 fallback
                sd3_names = [n for n in zf.namelist() if n.lower().endswith(".sd3")]
                if not sd3_names:
                    print(f"  ! {path.name}: no .lef or .sd3 in zip", file=sys.stderr)
                    return None, []
                sd3_bytes = zf.read(sd3_names[0])
                return parse_sd3_bytes(sd3_bytes, meet_id, path)
            xml_bytes = zf.read(lef_names[0])
            return parse_lenex(xml_bytes, meet_id, path)
    elif path.suffix.lower() == ".sd3":
        return parse_sd3_bytes(path.read_bytes(), meet_id, path)
    else:
        return None, []


# ---------------------------------------------------------------------------
# SD3 parsing (fallback)
# ---------------------------------------------------------------------------
#
# SD3 is a fixed-width Hy-Tek format. Each line is 160 chars, prefixed by a
# 3-char record type:
#
#   A0x  file header
#   B1x  meet info
#   C1x  club section header — chars [13:17] is the 4-char team code
#   D0x  swimmer event entry (one per swim)
#   D3x  swimmer registration metadata (skip)
#   G0x  splits for the most recent matching D0x
#   E0x  relay event header (skipped in v1)
#   F0x  relay leg (skipped in v1)
#   Z0x  trailer
#
# A D02 line is partitioned roughly as:
#
#   [ 0: 3]  "D02"
#   [11:39]  swimmer name "Lastname, Firstname" (28-char field)
#   [39:51]  license / CAN-FED id (12-char field)
#   [55:63]  birthdate MMDDYYYY
#   [63:65]  age at meet
#   [65:66]  gender (M/F)
#   [67:72]  event code: distance*10 + stroke (5-char right-just: "  501", " 1001", "15001")
#   [76:80]  age-group low(2) + high(2) (e.g. "5559" → 55-59)
#   [80:88]  meet date MMDDYYYY
#   [89:97]  entry/seed time + 1-char course (e.g. "0:30.00S")
#  [116:124] finals time + course, or "NS      S" / "SCR     S" / "DQ      S"
#  [124:130] place (right-justified)
#  [134:139] points (FINA)
#
# Course codes inside the time field: S = short course (25m), L = long course (50m).
# Stroke codes (Hy-Tek): 1=Free, 2=Back, 3=Breast, 4=Fly, 5=IM.

SD3_STROKE = {1: "FREE", 2: "BACK", 3: "BREAST", 4: "FLY", 5: "MEDLEY"}
COURSE_CHAR_TO_POOL = {"S": 25, "L": 50}


def _sd3_time_to_seconds(token: str) -> tuple[float | None, str]:
    """Parse an SD3 time field like '0:39.00S' / 'NS      S' / 'SCR     S'.

    Returns (seconds, status). status is 'OK' for a valid time, 'NS', 'SCR',
    'DQ', 'NT' otherwise.
    """
    if not token or token.strip() == "":
        return None, "OK"
    t = token.rstrip()
    # strip trailing course char (S/L)
    if t and t[-1] in "SL":
        t = t[:-1]
    t = t.strip()
    if t in ("", "NS", "NT", "DQ", "SCR"):
        return None, t or "OK"
    sec = time_to_seconds(t)
    return sec, ("OK" if sec is not None else t)


def _sd3_iso_date(mmddyyyy: str) -> str | None:
    s = (mmddyyyy or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    return f"{s[4:8]}-{s[0:2]}-{s[2:4]}"


def parse_sd3_bytes(data: bytes, meet_id: str, source_path: Path) -> tuple[dict | None, list[dict]]:
    try:
        text = data.decode("cp1252")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    lines = text.splitlines()

    meet_name = ""
    meet_city = ""
    meet_state = ""
    meet_dates: list[str] = []

    # SD3 record-type matching: the third char (last digit of the record
    # type) varies by Splash dialect — most files use "02" but some emit
    # "08". Match by the first letter and accept either suffix.
    def rt(line: str) -> str:
        if len(line) < 3:
            return ""
        return line[0] if line[1:3] in ("02", "08", "12", "18", "31") else ""

    # meet-level B1x: name [11:71], city [82:102], state [102:117]
    for l in lines:
        if rt(l) == "B":
            meet_name = l[11:71].strip()
            meet_city = l[82:102].strip()
            meet_state = l[102:117].strip()
            break

    swims: list[dict] = []
    current_team: str | None = None
    pending_swim: dict | None = None
    pool_observed: int | None = None

    for l in lines:
        rec = rt(l)
        if rec == "C":
            current_team = l[13:17].rstrip() or None
            continue
        if current_team != BROSSARD_CLUB_CODE:
            continue
        if rec == "D" and l[1] == "0":  # D02 / D08 — swimmer event; ignore D31
            name_raw = l[11:39].strip()
            # SD3 names are "Lastname, Firstname [middle...]" — flip to display form
            if "," in name_raw:
                last, _, first = name_raw.partition(",")
                firstname = first.strip()
                lastname = last.strip()
            else:
                firstname, lastname = "", name_raw
            name = display_name(firstname, lastname)
            slug = slugify(name)

            license_ = l[39:51].strip()
            dob_iso = _sd3_iso_date(l[55:63])
            gender = l[65:66].strip()

            try:
                event_code = int(l[67:72].strip())
            except ValueError:
                continue  # malformed event code, skip
            distance = event_code // 10
            stroke_n = event_code % 10
            stroke = SD3_STROKE.get(stroke_n)
            if stroke is None:
                continue  # unknown stroke

            ag_low = l[76:78].strip()
            ag_high = l[78:80].strip()
            if ag_low and ag_high:
                ag = ag_low if ag_low == ag_high else f"{ag_low}-{ag_high}"
            else:
                ag = ag_low or None

            meet_date_for_swim = _sd3_iso_date(l[80:88]) or ""
            if meet_date_for_swim:
                meet_dates.append(meet_date_for_swim)

            entry_field = l[89:97]
            finals_field = l[116:124]
            entry_sec, _ = _sd3_time_to_seconds(entry_field)
            time_sec, status = _sd3_time_to_seconds(finals_field)

            # course from finals time field
            course_char = finals_field.rstrip()[-1:] if finals_field.strip() else ""
            if course_char in COURSE_CHAR_TO_POOL:
                pool = COURSE_CHAR_TO_POOL[course_char]
                pool_observed = pool
            else:
                pool = pool_observed or 25

            place_raw = l[124:130].strip()
            place = _int_or_none(place_raw)
            points_raw = l[134:139].strip()
            points = _int_or_none(points_raw)

            age = age_at(dob_iso, meet_date_for_swim) if meet_date_for_swim else None
            ag_band = age_group(age) or ag  # prefer derived, fall back to file's

            event_key = f"{gender}-{stroke}-{distance}-{pool}"
            bucket_key = f"{gender}-{ag_band}-{stroke}-{distance}-{pool}" if ag_band else event_key

            swimmer_id = license_ or f"slug-{slug}"
            swim_id_date = (meet_date_for_swim or "").replace("-", "")
            swim_id = f"{swim_id_date}-{swimmer_id}-{distance}-{stroke}-{pool}"

            swim = {
                "swim_id":       swim_id,
                "swimmer_id":    swimmer_id,
                "swimmer_slug":  slug,
                "swimmer_name":  name,
                "firstname":     firstname,
                "lastname":      lastname,

                "gender":        gender,
                "dob":           dob_iso,
                "age_at_meet":   age,
                "age_group":     ag_band,

                "stroke":        stroke,
                "stroke_fr":     STROKE_FR.get(stroke, stroke.lower()),
                "distance":      distance,
                "pool":          pool,
                "relay":         False,
                "event_key":     event_key,
                "bucket_key":    bucket_key,

                "time_sec":      round(time_sec, 2) if time_sec is not None else None,
                "splits_sec":    [],
                "status":        status if status in ("OK", "DNS", "DSQ", "WDR", "NS", "SCR", "DQ", "NT") else "OK",
                "place":         place,
                "points":        points,
                "entrytime_sec": round(entry_sec, 2) if entry_sec is not None else None,

                "meet_id":       meet_id,
                "meet_name":     meet_name,
                "meet_date":     meet_date_for_swim,
                "meet_city":     meet_city,
                "meet_course":   "SCM" if pool == 25 else "LCM",
                "meet_source":   "sd3",

                "set_record":    False,
                "record_key":    None,
            }
            swims.append(swim)
            pending_swim = swim
            continue

        if rec == "G" and pending_swim is not None:
            # G02 lines have 4 extra leading-space chars vs D02, so the license
            # field is shifted right by 4. Find the license by regex instead of
            # by fixed position, to be robust to small variations.
            m = re.search(r"\b(\d{8,12})\b", l[15:55])
            license_in_g = m.group(1) if m else ""
            if license_in_g and license_in_g == pending_swim["swimmer_id"]:
                # extract all M:SS.ss or SS.ss tokens from the time portion.
                # G02 layout puts split times after a "<dist>C/L " token, e.g.
                # "  50C 0:37.83 1:21.50". Grab everything after that marker.
                tail = l[55:]
                # drop everything up to and including the first "C " or "L "
                marker = re.search(r"\d+[CL]\s+", tail)
                if marker:
                    tail = tail[marker.end():]
                tokens = re.findall(r"\d{1,2}:\d{2}\.\d{2}|\d+\.\d{2}", tail)
                vals: list[float] = []
                for sp in tokens:
                    sec = time_to_seconds(sp)
                    if sec is not None:
                        vals.append(round(sec, 2))
                # The last G02 token is typically the final touch, which equals
                # time_sec — drop it for consistency with lenex's convention
                # (intermediate splits only).
                final = pending_swim.get("time_sec")
                if final is not None and vals and abs(vals[-1] - final) < 0.02:
                    vals = vals[:-1]
                if vals:
                    pending_swim["splits_sec"] = vals
            continue

        # E0x / F0x (relays) — reset pending so stray G0x doesn't get attached
        if rec in ("E", "F"):
            pending_swim = None

    # meet metadata
    primary_date = min(meet_dates) if meet_dates else ""
    # if any swim observed long course, default meet course to LCM; else SCM
    meet_course = "LCM" if any(s.get("pool") == 50 for s in swims) else "SCM"
    meet_meta = {
        "meet_id":     meet_id,
        "name":        meet_name,
        "date":        primary_date,
        "city":        meet_city,
        "course":      meet_course,
        "nation":      "CAN",
        "state":       meet_state,
        "source":      "sd3",
        "source_file": source_path.name,
    }
    return meet_meta, swims


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_meets() -> list[Path]:
    if not MEETS_DIR.exists():
        return []
    files: list[Path] = []
    for year_dir in sorted(MEETS_DIR.iterdir()):
        if not year_dir.is_dir():
            continue
        for f in sorted(year_dir.iterdir()):
            if f.suffix.lower() in (".zip", ".sd3"):
                files.append(f)
    return files


# ---------------------------------------------------------------------------
# Derived outputs
# ---------------------------------------------------------------------------

def build_swimmers_index(swims: list[dict]) -> dict:
    by_id: dict[str, dict] = {}
    for s in swims:
        sid = s["swimmer_id"]
        entry = by_id.setdefault(sid, {
            "id":           sid,
            "name":         s["swimmer_name"],
            "slug":         s["swimmer_slug"],
            "gender":       s["gender"],
            "dob":          s["dob"],
            "first_seen":   s["meet_date"],
            "last_seen":    s["meet_date"],
            "swim_count":   0,
            "records_held": 0,
            "year_range":   [None, None],
        })
        entry["swim_count"] += 1
        if s["meet_date"] < entry["first_seen"]:
            entry["first_seen"] = s["meet_date"]
        if s["meet_date"] > entry["last_seen"]:
            entry["last_seen"] = s["meet_date"]
        if s["set_record"]:
            entry["records_held"] += 1
        try:
            yr = int(s["meet_date"][:4])
            yr_min, yr_max = entry["year_range"]
            entry["year_range"] = [
                yr if yr_min is None else min(yr_min, yr),
                yr if yr_max is None else max(yr_max, yr),
            ]
        except (ValueError, TypeError):
            pass
    return by_id


def build_meets_index(meets: list[dict], swims: list[dict]) -> dict:
    swim_counts: dict[str, int] = defaultdict(int)
    for s in swims:
        swim_counts[s["meet_id"]] += 1
    out: dict[str, dict] = {}
    for m in meets:
        out[m["meet_id"]] = {
            "meet_id":    m["meet_id"],
            "name":       m["name"],
            "date":       m["date"],
            "city":       m["city"],
            "course":     m["course"],
            "swim_count": swim_counts.get(m["meet_id"], 0),
            "source":     m["source"],
        }
    return out


def write_per_swimmer(swims: list[dict], index: dict, out_dir: Path) -> None:
    by_id: dict[str, list[dict]] = defaultdict(list)
    for s in swims:
        by_id[s["swimmer_id"]].append(s)
    swimmers_dir = out_dir / "swimmers"
    swimmers_dir.mkdir(parents=True, exist_ok=True)
    for sid, sw_list in by_id.items():
        sw_list.sort(key=lambda x: (x["meet_date"], x["distance"], x["stroke"]))
        payload = {
            "swimmer": index.get(sid, {}),
            "swims":   sw_list,
        }
        # filename uses license id when numeric, otherwise slug
        fname = sid if sid.isdigit() else sid
        (swimmers_dir / f"{fname}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def write_per_event(swims: list[dict], out_dir: Path) -> None:
    by_event: dict[str, list[dict]] = defaultdict(list)
    for s in swims:
        by_event[s["event_key"]].append(s)
    events_dir = out_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    for ek, sw_list in by_event.items():
        sw_list.sort(key=lambda x: (x["meet_date"], x.get("time_sec") or 9e9))
        label_parts = ek.split("-")
        gender, stroke, dist, pool = label_parts
        stroke_fr = STROKE_FR.get(stroke, stroke.lower())
        label_fr = f"{gender} {dist} {stroke_fr} ({pool}m)"
        payload = {
            "event_key":  ek,
            "label_fr":   label_fr,
            "swim_count": len(sw_list),
            "swims":      sw_list,
        }
        (events_dir / f"{ek}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Record linkage (stub — will be filled in once lenex pass is verified)
# ---------------------------------------------------------------------------

def link_records(swims: list[dict]) -> None:
    """In-place: mark swims that set a club record per the chronology JSON.

    Stub for now — leaves all swims set_record=False. Implemented in the next
    iteration once the canonical swim shape is verified against real data.
    """
    pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't write files, print stats only")
    ap.add_argument("--meet", type=Path, help="parse a single meet file (debug)")
    args = ap.parse_args()

    if args.meet:
        files = [args.meet]
    else:
        files = discover_meets()

    if not files:
        print("No meet files found.", file=sys.stderr)
        return 1

    all_swims: list[dict] = []
    all_meets: list[dict] = []
    parsed_count = 0
    skipped_count = 0

    for path in files:
        print(f"Parsing {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}", file=sys.stderr)
        try:
            meet_meta, swims = parse_meet_file(path)
        except Exception as e:
            print(f"  ! {path.name}: {e}", file=sys.stderr)
            skipped_count += 1
            continue
        if meet_meta is None and not swims:
            skipped_count += 1
            continue
        if meet_meta is not None:
            all_meets.append(meet_meta)
        all_swims.extend(swims)
        parsed_count += 1
        print(f"  → {len(swims)} Brossard swims", file=sys.stderr)

    link_records(all_swims)

    print(file=sys.stderr)
    print(f"Meets parsed:   {parsed_count}", file=sys.stderr)
    print(f"Meets skipped:  {skipped_count}", file=sys.stderr)
    print(f"Total swims:    {len(all_swims)}", file=sys.stderr)
    print(f"Swimmers:       {len(set(s['swimmer_id'] for s in all_swims))}", file=sys.stderr)

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # swims.jsonl
    with (OUT_DIR / "swims.jsonl").open("w", encoding="utf-8") as f:
        for s in all_swims:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # indexes
    swimmers_index = build_swimmers_index(all_swims)
    meets_index = build_meets_index(all_meets, all_swims)

    (OUT_DIR / "swimmers_index.json").write_text(
        json.dumps(swimmers_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT_DIR / "meets_index.json").write_text(
        json.dumps(meets_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # per-swimmer + per-event
    write_per_swimmer(all_swims, swimmers_index, OUT_DIR)
    write_per_event(all_swims, OUT_DIR)

    print(f"Wrote → {OUT_DIR.relative_to(ROOT)}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
