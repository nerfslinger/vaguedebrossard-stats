#!/usr/bin/env python3
"""
parse_swimrankings.py
=====================
Phase 1 harvester for swimrankings.net Lenex (.lxf) meet result files.

Approach
--------
swimrankings.net hosts each currently-live (or very recently completed) meet's
results as a Lenex 3.0 file at:

    https://live.swimrankings.net/{meetId}/results.lxf

These are ZIP-wrapped XML files served without authentication. Once a meet
is archived (a few months after it completes) the file is removed from this
endpoint — historical recovery requires the browser-rendered meetDetail page
(see Phase 2). This script handles the active window only.

For each meetId in a configurable range, the script:
  1. Probes the URL with a HEAD-equivalent GET (range 0-0) for speed.
  2. If 200, downloads the .lxf and caches it under _cache/swimrankings/.
  3. Unzips and parses the inner .lef XML.
  4. Keeps the meet if nation="CAN".
  5. Extracts every result row for swimmers whose club name matches
     a Brossard pattern, normalising swimtime to seconds.
  6. Writes brossard_swimrankings.json + brossard_swimrankings_meta.json.

Usage
-----
    python3 parse_swimrankings.py                  # default range
    python3 parse_swimrankings.py 47500 50000      # custom range
    python3 parse_swimrankings.py --rescan         # ignore cache, re-fetch all

Output schema (one record per swim)
-----------------------------------
{
    "meet_id":      48382,
    "meet_name":    "II Trofeo Ovimaster Seronda 2025",
    "city":         "Oviedo",
    "nation":       "CAN",
    "course":       "SCM" | "LCM" | "SCY",
    "masters":      true | false,
    "date":         "2025-11-16",          # session date
    "athlete": {
        "first":      "Christian",
        "last":       "Berger",
        "birthdate":  "1962-04-23",        # may be partial
        "gender":     "M" | "F"
    },
    "club": {"name": "La Vague de Brossard", "code": "..."},
    "event": {
        "stroke":     "FREE" | "BACK" | "BREAST" | "FLY" | "MEDLEY",
        "distance":   50,
        "age_group":  "60-64"              # may be "" if event has no AGEGROUP
    },
    "swimtime":      "00:01:05.02",
    "swimtime_sec":  65.02,
    "status":        null                  # "DSQ", "DNS", "DNF" if present
}
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_ID_MIN = 47500
DEFAULT_ID_MAX = 50000

USER_AGENT = (
    "vaguestats-records-archiver/0.1 "
    "(La Vague de Brossard club records project; contact: club records keeper)"
)

# Match any club name containing one of these tokens (case-insensitive).
# Kept loose because Lenex club names from different federations vary.
BROSSARD_PATTERNS = [
    re.compile(r"\bbrossard\b", re.IGNORECASE),
    re.compile(r"\bvague\b", re.IGNORECASE),
]

ROOT = Path(__file__).parent
CACHE_DIR = ROOT / "_cache" / "swimrankings"
OUTPUT_FILE = ROOT / "brossard_swimrankings.json"
META_FILE = ROOT / "brossard_swimrankings_meta.json"

REQUEST_DELAY_SEC = 0.25     # polite throttle
REQUEST_TIMEOUT_SEC = 15

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_lxf(meet_id: int) -> bytes | None:
    """Fetch the .lxf file for one meet. Returns bytes or None on 404/error."""
    url = f"https://live.swimrankings.net/{meet_id}/results.lxf"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read()
            # Reject anything that didn't actually come back as binary
            if "html" in ctype.lower():
                return None
            return data
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [{meet_id}] HTTP {e.code}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  [{meet_id}] network error: {e}", file=sys.stderr)
        return None


def load_cached_or_fetch(meet_id: int, rescan: bool) -> bytes | None:
    """Return raw .lxf bytes from disk cache or network, or None if absent."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{meet_id}.lxf"
    missing_marker = CACHE_DIR / f"{meet_id}.404"

    if not rescan and cached.exists():
        return cached.read_bytes()
    if not rescan and missing_marker.exists():
        return None

    data = fetch_lxf(meet_id)
    time.sleep(REQUEST_DELAY_SEC)

    if data is None:
        missing_marker.write_text("")
        return None
    cached.write_bytes(data)
    if missing_marker.exists():
        missing_marker.unlink()
    return data


def extract_lenex_xml(data: bytes) -> str | None:
    """Lenex files are ZIPs containing a single .lef XML. Returns the XML text."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".lef")]
            if not members:
                return None
            with zf.open(members[0]) as inner:
                return inner.read().decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        # Some files might be served uncompressed
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def swimtime_to_seconds(swimtime: str) -> float | None:
    """Convert Lenex swimtime ('HH:MM:SS.ss' or 'MM:SS.ss') to seconds."""
    if not swimtime or swimtime == "NT":
        return None
    parts = swimtime.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])
    except (ValueError, TypeError):
        return None


def club_matches_brossard(club_name: str) -> bool:
    if not club_name:
        return False
    return any(p.search(club_name) for p in BROSSARD_PATTERNS)


def age_group_label(agegroup_el: ET.Element | None) -> str:
    if agegroup_el is None:
        return ""
    amin = agegroup_el.get("agemin", "-1")
    amax = agegroup_el.get("agemax", "-1")
    if amin == "-1" and amax == "-1":
        return ""
    if amax == "-1":
        return f"{amin}+"
    if amin == "-1":
        return f"<={amax}"
    return f"{amin}-{amax}"


# ---------------------------------------------------------------------------
# Lenex parsing
# ---------------------------------------------------------------------------

def parse_meet(xml_text: str, meet_id: int) -> tuple[dict | None, list[dict]]:
    """Return (meet_meta, list_of_brossard_swim_rows). meet_meta is None for skip."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [{meet_id}] XML parse error: {e}", file=sys.stderr)
        return None, []

    meet_el = root.find("MEETS/MEET")
    if meet_el is None:
        return None, []

    nation = meet_el.get("nation", "")
    meet_meta = {
        "meet_id":   meet_id,
        "meet_name": meet_el.get("name", ""),
        "city":      meet_el.get("city", ""),
        "nation":    nation,
        "course":    meet_el.get("course", ""),
        "masters":   meet_el.get("masters", "F") == "T",
        "result_url": meet_el.get("result.url", ""),
    }

    # Only keep Canadian meets in the final harvest. Foreign meets are noted
    # in meta (in case a Brossard swimmer crossed a border) but only Canadian
    # ones are scanned for now — non-CAN clubs won't have Brossard listed.
    if nation != "CAN":
        return meet_meta, []

    # Map eventid -> (distance, stroke, age_group_label)
    # AGEGROUP is per-event and may have multiple; pick the one referring this
    # athlete by re-walking RANKING references — too coarse, so we keep a flat
    # event->stroke/distance map and look up age group via the per-result link
    # when present.
    event_info: dict[str, dict] = {}
    age_by_resultid: dict[str, str] = {}
    for ev in meet_el.iter("EVENT"):
        eid = ev.get("eventid", "")
        swim = ev.find("SWIMSTYLE")
        event_info[eid] = {
            "stroke":   swim.get("stroke", "") if swim is not None else "",
            "distance": int(swim.get("distance", "0") or 0) if swim is not None else 0,
        }
        for ag in ev.findall("AGEGROUPS/AGEGROUP"):
            label = age_group_label(ag)
            for rk in ag.findall("RANKINGS/RANKING"):
                rid = rk.get("resultid", "")
                if rid:
                    age_by_resultid[rid] = label

    # Walk clubs -> athletes -> results
    rows: list[dict] = []
    for club in meet_el.iter("CLUB"):
        club_name = club.get("name", "")
        if not club_matches_brossard(club_name):
            continue
        club_info = {
            "name": club_name,
            "code": club.get("code", ""),
        }
        for athlete in club.findall("ATHLETES/ATHLETE"):
            athlete_info = {
                "first":     athlete.get("firstname", ""),
                "last":      athlete.get("lastname", ""),
                "birthdate": athlete.get("birthdate", ""),
                "gender":    athlete.get("gender", ""),
            }
            for result in athlete.findall("RESULTS/RESULT"):
                eid = result.get("eventid", "")
                rid = result.get("resultid", "")
                ev = event_info.get(eid, {"stroke": "", "distance": 0})
                swimtime = result.get("swimtime", "")
                rows.append({
                    "meet_id":      meet_id,
                    "meet_name":    meet_meta["meet_name"],
                    "city":         meet_meta["city"],
                    "nation":       nation,
                    "course":       meet_meta["course"],
                    "masters":      meet_meta["masters"],
                    "date":         (result.get("reactiontime") or "")[:10],
                    "athlete":      athlete_info,
                    "club":         club_info,
                    "event": {
                        "stroke":    ev["stroke"],
                        "distance":  ev["distance"],
                        "age_group": age_by_resultid.get(rid, ""),
                    },
                    "swimtime":      swimtime,
                    "swimtime_sec":  swimtime_to_seconds(swimtime),
                    "status":        result.get("status"),
                })
    return meet_meta, rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id_min", nargs="?", type=int, default=DEFAULT_ID_MIN,
                    help=f"first meet ID to probe (default {DEFAULT_ID_MIN})")
    ap.add_argument("id_max", nargs="?", type=int, default=DEFAULT_ID_MAX,
                    help=f"last meet ID to probe (default {DEFAULT_ID_MAX})")
    ap.add_argument("--rescan", action="store_true",
                    help="ignore cache, re-fetch every ID")
    args = ap.parse_args()

    print(f"Probing meet IDs {args.id_min}..{args.id_max} "
          f"({args.id_max - args.id_min + 1} total)")
    print(f"Cache: {CACHE_DIR}")
    print()

    can_meets: list[dict] = []
    can_masters_meets: list[dict] = []
    all_meets_seen: list[dict] = []
    brossard_rows: list[dict] = []
    hits = 0

    for mid in range(args.id_min, args.id_max + 1):
        data = load_cached_or_fetch(mid, args.rescan)
        if data is None:
            continue
        hits += 1

        xml_text = extract_lenex_xml(data)
        if not xml_text:
            print(f"  [{mid}] failed to extract XML")
            continue

        meta, rows = parse_meet(xml_text, mid)
        if meta:
            all_meets_seen.append(meta)
            if meta["nation"] == "CAN":
                can_meets.append(meta)
                if meta["masters"]:
                    can_masters_meets.append(meta)
                tag = "CAN-MASTERS" if meta["masters"] else "CAN-AGE"
                if rows or meta["masters"]:
                    print(f"  [{mid}] {tag}: {meta['meet_name']} ({meta['city']}) "
                          f"-> {len(rows)} Brossard swim(s)")
        brossard_rows.extend(rows)

    print()
    print(f"Probed: {args.id_max - args.id_min + 1}")
    print(f"  files found: {hits}")
    print(f"  CAN meets:   {len(can_meets)}")
    print(f"  Brossard swim rows: {len(brossard_rows)}")
    print()

    print(f"  CAN Masters meets:  {len(can_masters_meets)}")
    print()

    OUTPUT_FILE.write_text(json.dumps(brossard_rows, indent=2, ensure_ascii=False))
    META_FILE.write_text(json.dumps({
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "id_range": [args.id_min, args.id_max],
        "files_found": hits,
        "all_meets_count": len(all_meets_seen),
        "can_meets": can_meets,
        "can_masters_meets": can_masters_meets,
    }, indent=2, ensure_ascii=False))
    print(f"Wrote {OUTPUT_FILE.name} and {META_FILE.name}")


if __name__ == "__main__":
    main()
