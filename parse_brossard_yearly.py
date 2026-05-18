#!/usr/bin/env python3
"""
parse_brossard_yearly.py
========================
Parses the year-by-year chronology files from statsman.ca into structured JSON,
producing the data needed for the Year-in-Review and longitudinal storytelling
views of the Brossard records site.

Two source families are ingested:

1. ``rmbb<YY>.htm``  — "Records des Maitres de Brossard battus en <year>"
   A complete log of every record-breaking swim in a given year.
   Available 1997–current year.
   The file has two parts:
     a) a chronological log (one line per swim) with event, age group, name,
        time, meet code, month, year
     b) a per-gender / per-age-group summary section with FINA % scores and
        a relay subsection (multi-line entries with swimmer rosters)

2. ``rmb<YY>sta.htm`` — Year-end snapshot of records *held* on Dec 31 of that year.
   Available 2002–most recent year-end.
   Contains:
     a) a leaderboard (women + men, ranked by number of records currently held)
     b) a "Plus vieux records" block — the oldest records still standing as of
        that snapshot date, both individual and relay

Outputs:
    brossard_yearly.json            — every record-breaking swim + per-year roll-ups
    brossard_snapshots.json         — year-end leaderboards + oldest-standing records
    brossard_chronology_data.js     — slim, minified payload for brossard_chronology.html
                                      (window.YEARLY = {...}; loaded via <script src>)

Run with:
    python3 parse_brossard_yearly.py

Cached HTM files land in ./_cache/ to avoid hammering the source on every run.
"""

import re
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlretrieve
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.statsman.ca/rec/"
CACHE_DIR = Path("_cache")
CACHE_DIR.mkdir(exist_ok=True)

# rmbb files: 1997 → current year (the index goes through 2026)
YEARLY_LOG_YEARS = list(range(1997, 2027))

# rmb<YY>sta.htm files: index lists 2002 through 2025
SNAPSHOT_YEARS = list(range(2002, 2026))

# Year cutoff for two-digit years: anything >= this is 19XX, otherwise 20XX
# (Brossard's oldest records are from 1980; the parser may also see "66"
# entered retroactively — treat anything <= 30 as 20XX.)
Y2K_CUTOFF = 30

STROKE_EN = {
    "lib": "freestyle",
    "dos": "backstroke",
    "bra": "breaststroke",
    "pap": "butterfly",
    "qni": "individual medley",
    "rqn": "medley",  # relay medley
}

MONTH_MAP = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "avr": 4, "apr": 4,
    "mai": 5, "may": 5, "jun": 6, "jul": 7, "aou": 8, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Friendly names for the most common meet codes (best-effort; codes seen in the
# yearly log files). Unknown codes are left as the raw 2–4 char abbreviation.
MEET_NAMES = {
    "BRO": "Brossard",
    "POM": "Pointe-Claire",
    "MTL": "Montréal",
    "QUE": "Championnats québécois",
    "REP": "Repentigny",
    "LSL": "Laval/Laval-sur-le-Lac",
    "DDO": "Dollard-des-Ormeaux",
    "MN":  "Mont-Tremblant / Maisonneuve",
    "PC":  "Pointe-Claire",
    "OLY": "Stade olympique",
    "CSL": "Côte-Saint-Luc",
    "DRU": "Drummondville",
    "VIC": "Victoriaville",
    "SHY": "Shawinigan",
    "BEY": "Beyrouth",
    "NEP": "Nepean",
    "COW": "Cowansville",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_or_local(filename: str) -> list[str]:
    """Cache HTM files in ./_cache/ and return them as a list of lines."""
    cached = CACHE_DIR / filename
    if cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace").splitlines()
    url = BASE_URL + filename
    print(f"  Downloading: {url}")
    try:
        tmp, _ = urlretrieve(url)
        text = Path(tmp).read_text(encoding="utf-8", errors="replace")
        cached.write_text(text, encoding="utf-8")
        return text.splitlines()
    except URLError as e:
        print(f"  ! Could not fetch {url}: {e}")
        return []


def parse_yy(raw: str) -> int:
    """Convert a 2-digit year string to a 4-digit year using Y2K_CUTOFF."""
    y = int(raw.strip())
    return (2000 + y) if y < Y2K_CUTOFF else (1900 + y)


def parse_time_sec(t: str) -> float:
    """Convert 'M:SS.ss' or 'SS.ss' (with possibly stray spaces) to seconds."""
    t = t.strip().replace(" ", "")
    if ":" in t:
        m, s = t.split(":", 1)
        return int(m) * 60 + float(s)
    return float(t)


def fmt_time(min_part: int, sec_part: float) -> str:
    """Render a (min, sec) pair as 'M:SS.ss' (or 'SS.ss' if min == 0)."""
    if min_part == 0:
        return f"{sec_part:05.2f}"
    return f"{min_part}:{sec_part:05.2f}"


def title_name(raw: str) -> str:
    """Convert ALL-CAPS source names ('GAIL DESJARDINS') to title case."""
    s = raw.strip()
    # Collapse multiple internal spaces
    s = re.sub(r"\s+", " ", s)
    # Title-case but preserve dotted initials and hyphens
    parts = []
    for tok in s.split(" "):
        if "-" in tok:
            tok = "-".join(p.capitalize() for p in tok.split("-"))
        elif "." in tok:
            tok = ".".join(p.capitalize() for p in tok.split("."))
        else:
            tok = tok.capitalize()
        parts.append(tok)
    return " ".join(parts)


def load_name_to_category() -> dict:
    """Build a name → 'F' | 'M' lookup from brossard_individual.json if present."""
    path = Path("brossard_individual.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    lookup = {}
    for r in data:
        n = r.get("name", "").strip().upper()
        c = r.get("category")
        if n and c in ("F", "M"):
            # If a name appears in both, prefer the first; in practice this
            # shouldn't happen for distinct individuals.
            lookup.setdefault(n, c)
    return lookup


# ---------------------------------------------------------------------------
# Yearly log parser (top section of rmbb<YY>.htm)
# ---------------------------------------------------------------------------
#
# Sample lines:
#   "  200lib 25 45-49 JULIE DUFOUR        BRO       2 55.39 DRU JAN 24"
#   "   50bra 25 65-69 MONIQUE LETHIECQ    BRO       0 57.02 DRU JAN 20"
#   "  200pap 50 20-24 CHRISTIAN BERGER    BRO       3 25.40 BEY OCT 66"
#
# Columns: distance+stroke | pool | age | name (~20 chars) | club | min | sec
#          | meet | month | year(2-digit)

LOG_RE = re.compile(
    r"^\s*"
    r"(?P<distance>\d+)(?P<stroke>lib|dos|bra|pap|qni)\s+"
    r"(?P<pool>\d{2})\s+"
    r"(?P<age>\d{2}-\d{2})\s+"
    r"(?P<name>[A-Z][A-Z0-9 .'\-]+?)\s+"
    r"(?P<club>BRO|[A-Z]{2,4})\s+"
    r"(?P<min>\d+)\s+"
    r"(?P<sec>\d+\.\d{2})\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Z]{3})\s+"
    r"(?P<year>\d{2})\s*$",
    re.IGNORECASE,
)

# Top of file header line we want to skip:
#   " EPR     BA AGE   NOM                 CLUB    MIN   SEC LDATE"
HEADER_SKIP_RE = re.compile(r"^\s*EPR\s+BA\s+AGE", re.IGNORECASE)


def parse_yearly_log(lines: list[str], year_full: int) -> list[dict]:
    """Parse the chronological log at the top of a rmbb<YY>.htm file.

    Stops once we hit the per-age-group summary (signalled by 'Page No.' or
    'GROUPE D''AGE').
    """
    records = []
    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if HEADER_SKIP_RE.match(line):
            continue
        # Stop at the start of the summary section
        if s.startswith("Page No.") or "GROUPE D'AGE" in s or s.startswith("EPREUVE"):
            break
        m = LOG_RE.match(line)
        if not m:
            continue
        try:
            sec_part = float(m.group("sec"))
            time_sec = int(m.group("min")) * 60 + sec_part
        except ValueError:
            continue
        records.append({
            "year":      year_full,
            "stroke":    m.group("stroke").lower(),
            "stroke_en": STROKE_EN.get(m.group("stroke").lower(), m.group("stroke")),
            "distance":  int(m.group("distance")),
            "pool":      int(m.group("pool")),
            "age_group": m.group("age"),
            "name":      title_name(m.group("name")),
            "name_raw":  m.group("name").strip().upper(),
            "time":      fmt_time(int(m.group("min")), sec_part),
            "time_sec":  round(time_sec, 2),
            "meet":      m.group("meet").upper(),
            "meet_name": MEET_NAMES.get(m.group("meet").upper(), ""),
            "month":     m.group("month").capitalize(),
            "month_num": MONTH_MAP.get(m.group("month").lower(), 0),
            "category":  "",   # filled in later from name lookup
            "pct":       0,    # filled in later from summary section
            "is_relay":  False,
        })
    return records


# ---------------------------------------------------------------------------
# Yearly summary parser (bottom section of rmbb<YY>.htm)
# ---------------------------------------------------------------------------
#
# Each summary block looks like:
#   "        Page No.     1"
#   "                                  RECORDS DE BROSSARD"
#   "                                    PAR GROUPE D'AGE"
#   "        EPREUVE  m    AGE           NOM          TEMPS  LIEU  DATE      %"
#   ""
#   "       ** GROUPE D'AGE 40-44"
#   "          50lib 25m 40-44 M-CHRISTINE AUDET     0:31.94 MN  DEC 24     84"
#   "       ** Subtotal **"
#   "                                                                       84"
#
# Page 1 is women's records, page 2 is men's, page 3 is relays. (Empty
# sub-sections still show up as a Page No. block with no entries.)

SUMMARY_LINE_RE = re.compile(
    r"^\s*"
    r"(?P<distance>\d+)(?P<stroke>lib|dos|bra|pap|qni)\s+"
    r"(?P<pool>\d{2})m\s+"
    r"(?P<age>\d{2}-\d{2})\s+"
    r"(?P<name>[A-Z][A-Z0-9 .'\-]+?)\s+"
    r"(?P<min>\d+):\s*(?P<sec>\d+\.\d{2})\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Z]{3})\s+"
    r"(?P<year>\d{2})\s+"
    r"(?P<pct>\d+)\s*$",
    re.IGNORECASE,
)

# Relay summary line — first line of a 2-3 line block:
#   "        200lib  25m 120+  B.RAYMOND             1:48.30 PC  FEB 24     88"
#   "        hom               R.BEAUDOIN "
#   "                          F.VINCENT V.LIMA"
RELAY_HEADER_RE = re.compile(
    r"^\s*"
    r"(?P<distance>\d+)(?P<stroke>lib|rqn|dos|bra|pap)\s+"
    r"(?P<pool>\d{2})m\s+"
    r"(?P<age>\d+\+)\s+"
    r"(?P<first_swimmers>[A-Z][A-Z0-9 .'\-]+?)\s+"
    r"(?P<min>\d+):\s*(?P<sec>\d+\.\d{2})\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Z]{3})\s+"
    r"(?P<year>\d{2})\s+"
    r"(?P<pct>\d+)\s*$",
    re.IGNORECASE,
)

RELAY_GENDER_RE = re.compile(r"^\s*(?P<cat>hom|fem|mix)\s+(?P<rest>.*)$", re.IGNORECASE)

PAGE_NO_RE = re.compile(r"^\s*Page No\.\s+\d+\s*$", re.IGNORECASE)


def parse_yearly_summary(lines: list[str]) -> tuple[list[dict], list[dict]]:
    """Walk the per-page summary section.

    Returns (individual_summary_entries, relay_records). The individual_summary
    list is used to back-fill ``pct`` and ``category`` on the chronological log.
    The relay list is its own thing (relays don't appear in the top log).
    """
    individuals = []
    relays = []
    page_count = 0          # how many "Page No.    1" boundaries we've crossed
    in_summary = False
    current_relay = None

    def flush_relay():
        nonlocal current_relay
        if current_relay and current_relay["swimmers"]:
            relays.append(current_relay)
        current_relay = None

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()

        # Detect entry into the summary section
        if not in_summary:
            if "GROUPE D'AGE" in s or "EPREUVE" in s and "AGE" in s:
                in_summary = True
            else:
                continue

        # New "page" boundary indicates a new gender section.
        # In rmbb files, page 1 = women, page 2 = men, page 3 = relays.
        # But pagination can break a single gender section across multiple
        # "Page No." headers, so we only bump the section index when we see
        # "Page No.    1".
        if PAGE_NO_RE.match(line) and re.search(r"Page No\.\s+1\s*$", line):
            flush_relay()
            page_count += 1
            continue

        # Section header: "** GROUPE D'AGE 40-44" — informational, skip.
        if "GROUPE D'AGE" in s:
            flush_relay()
            continue

        # Subtotal / total markers — skip.
        if "Subtotal" in s or "Total" in s:
            flush_relay()
            continue

        # Try to match an individual summary line.
        m = SUMMARY_LINE_RE.match(line)
        if m:
            flush_relay()
            sec_part = float(m.group("sec"))
            time_sec = int(m.group("min")) * 60 + sec_part
            entry = {
                "section":   page_count,   # 1=women, 2=men, 3=relays-section (but no inds there)
                "stroke":    m.group("stroke").lower(),
                "distance":  int(m.group("distance")),
                "pool":      int(m.group("pool")),
                "age_group": m.group("age"),
                "name_raw":  m.group("name").strip().upper(),
                "time_sec":  round(time_sec, 2),
                "meet":      m.group("meet").upper(),
                "month":     m.group("month").capitalize(),
                "year":      parse_yy(m.group("year")),
                "pct":       int(m.group("pct")),
            }
            individuals.append(entry)
            continue

        # Try to match a relay header line.
        rm = RELAY_HEADER_RE.match(line)
        if rm:
            flush_relay()
            sec_part = float(rm.group("sec"))
            time_sec = int(rm.group("min")) * 60 + sec_part
            first_swimmers = [
                w for w in rm.group("first_swimmers").strip().split() if w
            ]
            current_relay = {
                "year":      parse_yy(rm.group("year")),
                "stroke":    rm.group("stroke").lower(),
                "stroke_en": STROKE_EN.get(rm.group("stroke").lower(), rm.group("stroke")),
                "distance":  int(rm.group("distance")),
                "pool":      int(rm.group("pool")),
                "min_age":   int(rm.group("age").rstrip("+")),
                "category":  "",   # filled from the next line ("hom"/"fem"/"mix")
                "swimmers":  first_swimmers,
                "time":      fmt_time(int(rm.group("min")), sec_part),
                "time_sec":  round(time_sec, 2),
                "meet":      rm.group("meet").upper(),
                "meet_name": MEET_NAMES.get(rm.group("meet").upper(), ""),
                "month":     rm.group("month").capitalize(),
                "month_num": MONTH_MAP.get(rm.group("month").lower(), 0),
                "pct":       int(rm.group("pct")),
                "is_relay":  True,
            }
            continue

        # Continuation lines for the current relay
        if current_relay is not None:
            gm = RELAY_GENDER_RE.match(line)
            if gm:
                current_relay["category"] = gm.group("cat").lower()
                # The remainder of the line might contain a swimmer name
                rest_tokens = [t for t in gm.group("rest").strip().split() if t]
                current_relay["swimmers"].extend(rest_tokens)
            else:
                # Pure swimmer continuation line (just names)
                tokens = [t for t in s.split() if t and not t.startswith("*")]
                # Skip lines that are clearly numeric subtotals
                if tokens and not all(t.isdigit() for t in tokens):
                    current_relay["swimmers"].extend(tokens)

    flush_relay()
    return individuals, relays


# ---------------------------------------------------------------------------
# Cross-reference: attach pct + category to chronological log entries
# ---------------------------------------------------------------------------

def enrich_log(
    log: list[dict],
    summary: list[dict],
    name_to_cat: dict,
) -> None:
    """Mutate ``log`` in place to add pct (where available) and category."""
    # Build a lookup from summary by (event_key, age, name_raw)
    # The summary contains only the FINAL record per (event, name) for the year,
    # so we match by name only and check the time matches as well.
    summary_by_key = {}
    for e in summary:
        key = (e["distance"], e["stroke"], e["pool"], e["age_group"], e["name_raw"])
        summary_by_key[key] = e

    # Also build a name → section lookup for gender derivation:
    #   section 1 in summary = women, section 2 = men
    name_to_section = {}
    for e in summary:
        if e["section"] in (1, 2):
            name_to_section.setdefault(e["name_raw"], e["section"])

    for r in log:
        key = (r["distance"], r["stroke"], r["pool"], r["age_group"], r["name_raw"])
        sm = summary_by_key.get(key)
        # Only adopt pct if the time matches — otherwise this is an earlier
        # intermediate record for the same swimmer/event.
        if sm and abs(sm["time_sec"] - r["time_sec"]) < 0.005:
            r["pct"] = sm["pct"]

        # Category: prefer brossard_individual.json, fall back to summary section
        cat = name_to_cat.get(r["name_raw"], "")
        if not cat:
            sec = name_to_section.get(r["name_raw"])
            if sec == 1:
                cat = "F"
            elif sec == 2:
                cat = "M"
        r["category"] = cat


# ---------------------------------------------------------------------------
# Snapshot leaderboard parser  (rmb<YY>sta.htm)
# ---------------------------------------------------------------------------
#
# Two row layouts seen in the wild:
#
#   2020 style — both columns carry their own rank:
#     "   1  GAIL DESJARDINS       73       1    CHRISTIAN BERGER      97"
#     "  47  CLAUDETTE MARSAN       1"
#
#   2025 style — single shared rank, men's column has no rank:
#     " 1      GAIL DESJARDINS       64       CHRISTIAN BERGER     122"
#     "50      CHRISTINE RAYES        1       TRI NGO-MINH           1"

SNAPSHOT_2RANK_RE = re.compile(
    r"^\s*(?P<rw>\d+)\s+(?P<nw>[A-Z][A-Z .'\-]+?)\s+(?P<cw>\d+)\s+"
    r"(?P<rm>\d+)\s+(?P<nm>[A-Z][A-Z .'\-]+?)\s+(?P<cm>\d+)\s*$"
)

SNAPSHOT_1RANK_RE = re.compile(
    r"^\s*(?P<rank>\d+)\s+"
    r"(?P<nw>[A-Z][A-Z .'\-]+?)\s+(?P<cw>\d+)"
    r"(?:\s+(?P<nm>[A-Z][A-Z .'\-]+?)\s+(?P<cm>\d+))?"
    r"\s*$"
)

SNAPSHOT_LEFT_ONLY_RE = re.compile(
    r"^\s*(?P<rank>\d+)\s+(?P<name>[A-Z][A-Z .'\-]+?)\s+(?P<count>\d+)\s*$"
)

# Oldest-records section markers
OLDEST_HEADER_RE = re.compile(r"plus vieux records", re.IGNORECASE)

# Individual oldest-record line, e.g.:
#   "       50dos 50 50-54 GERTRUDE BLAIS      BRO    1:03.78 OLY JAN 82"
#   "     1500lib 25 55-59 HENRI MONGEON       BRO   26 44.56 PC  MAR 84"
OLDEST_IND_RE = re.compile(
    r"^\s*"
    r"(?P<distance>\d+)(?P<stroke>lib|dos|bra|pap|qni)\s+"
    r"(?P<pool>\d{2})\s+"
    r"(?P<age>\d{2}-\d{2})\s+"
    r"(?P<name>[A-Z][A-Z0-9 .'\-]+?)\s+"
    r"(?P<club>BRO|[A-Z]{2,4})\s+"
    r"(?:(?P<time1>\d+:\s*\d+\.\d{2})|(?P<min>\d+)\s+(?P<sec>\d+\.\d{2}))\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Z]{3})\s+"
    r"(?P<year>\d{2})\s*$",
    re.IGNORECASE,
)

# Oldest relay lines come in pairs:
#   "      200rqn hom 50                         2:28.66 BRO  OLY JAN 81"
#   "                    80+ LAMARCHE GOSSELIN MALO GRENIER"
OLDEST_REL_HEADER_RE = re.compile(
    r"^\s*"
    r"(?P<distance>\d+)(?P<stroke>lib|rqn|dos|bra|pap)\s+"
    r"(?P<cat>hom|fem|mix)\s+"
    r"(?P<pool>\d{2})\s+"
    r"(?P<time>\d+:\s*\d+\.\d{2})\s+"
    r"(?P<club>[A-Z]+)\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Z]{3})\s+"
    r"(?P<year>\d{2})\s*$",
    re.IGNORECASE,
)

OLDEST_REL_SWIMMERS_RE = re.compile(
    r"^\s*(?P<age>\d+\+)\s+(?P<swimmers>.+)$",
)


def parse_snapshot(lines: list[str], year_full: int, name_to_cat: dict | None = None) -> dict:
    """Parse a single rmb<YY>sta.htm file.

    ``name_to_cat`` is the {NAME_RAW: 'F'|'M'} lookup built from
    brossard_individual.json. The "Plus vieux records" section in the source
    files lists individual oldest records without a gender marker (only the
    relay lines say hom/fem/mix), so we tag each individual entry by name.
    """
    name_to_cat = name_to_cat or {}
    leaderboard_w = []
    leaderboard_m = []
    oldest = []

    in_oldest = False
    pending_rel = None  # for the 2-line oldest-relay format

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue

        if OLDEST_HEADER_RE.search(s):
            in_oldest = True
            continue

        if not in_oldest:
            # Try snapshot leaderboard patterns, most specific first.
            m = SNAPSHOT_2RANK_RE.match(line)
            if m:
                leaderboard_w.append({
                    "rank":          int(m.group("rw")),
                    "name":          title_name(m.group("nw")),
                    "name_raw":      m.group("nw").strip().upper(),
                    "records_held":  int(m.group("cw")),
                })
                leaderboard_m.append({
                    "rank":          int(m.group("rm")),
                    "name":          title_name(m.group("nm")),
                    "name_raw":      m.group("nm").strip().upper(),
                    "records_held":  int(m.group("cm")),
                })
                continue

            m = SNAPSHOT_1RANK_RE.match(line)
            if m:
                rank = int(m.group("rank"))
                leaderboard_w.append({
                    "rank":          rank,
                    "name":          title_name(m.group("nw")),
                    "name_raw":      m.group("nw").strip().upper(),
                    "records_held":  int(m.group("cw")),
                })
                if m.group("nm"):
                    leaderboard_m.append({
                        "rank":          rank,
                        "name":          title_name(m.group("nm")),
                        "name_raw":      m.group("nm").strip().upper(),
                        "records_held":  int(m.group("cm")),
                    })
                continue

            m = SNAPSHOT_LEFT_ONLY_RE.match(line)
            if m and "RECORDS" not in s.upper() and "DETENUS" not in s.upper():
                leaderboard_w.append({
                    "rank":          int(m.group("rank")),
                    "name":          title_name(m.group("name")),
                    "name_raw":      m.group("name").strip().upper(),
                    "records_held":  int(m.group("count")),
                })
            continue

        # in_oldest section
        # First, individual record line
        mi = OLDEST_IND_RE.match(line)
        if mi:
            if mi.group("time1"):
                time_sec = parse_time_sec(mi.group("time1"))
                t_str = mi.group("time1").replace(" ", "")
            else:
                sec_part = float(mi.group("sec"))
                time_sec = int(mi.group("min")) * 60 + sec_part
                t_str = fmt_time(int(mi.group("min")), sec_part)
            name_raw = mi.group("name").strip().upper()
            oldest.append({
                "kind":      "individual",
                "stroke":    mi.group("stroke").lower(),
                "distance":  int(mi.group("distance")),
                "pool":      int(mi.group("pool")),
                "age_group": mi.group("age"),
                "category":  name_to_cat.get(name_raw, ""),
                "name":      title_name(mi.group("name")),
                "name_raw":  name_raw,
                "time":      t_str,
                "time_sec":  round(time_sec, 2),
                "meet":      mi.group("meet").upper(),
                "month":     mi.group("month").capitalize(),
                "year":      parse_yy(mi.group("year")),
            })
            continue

        # Relay header (line 1 of 2)
        mr = OLDEST_REL_HEADER_RE.match(line)
        if mr:
            time_sec = parse_time_sec(mr.group("time"))
            pending_rel = {
                "kind":      "relay",
                "stroke":    mr.group("stroke").lower(),
                "stroke_en": STROKE_EN.get(mr.group("stroke").lower(), mr.group("stroke")),
                "distance":  int(mr.group("distance")),
                "pool":      int(mr.group("pool")),
                "category":  mr.group("cat").lower(),
                "time":      mr.group("time").replace(" ", ""),
                "time_sec":  round(time_sec, 2),
                "meet":      mr.group("meet").upper(),
                "month":     mr.group("month").capitalize(),
                "year":      parse_yy(mr.group("year")),
                "min_age":   None,
                "swimmers":  [],
            }
            continue

        # Relay swimmer line (line 2 of 2)
        ms = OLDEST_REL_SWIMMERS_RE.match(line)
        if ms and pending_rel is not None:
            pending_rel["min_age"] = int(ms.group("age").rstrip("+"))
            pending_rel["swimmers"] = [w for w in ms.group("swimmers").split() if w]
            oldest.append(pending_rel)
            pending_rel = None

    return {
        "year": year_full,
        "as_of": f"{year_full}-12-31",
        "leaderboard_women": leaderboard_w,
        "leaderboard_men": leaderboard_m,
        "oldest_records": oldest,
    }


# ---------------------------------------------------------------------------
# Aggregations for the Year-in-Review
# ---------------------------------------------------------------------------

def by_year_summary(records: list[dict], relays: list[dict]) -> dict:
    """Build per-year rollups from the full record list."""
    out = {}
    all_years = sorted({r["year"] for r in records} | {r["year"] for r in relays})
    for y in all_years:
        ind = [r for r in records if r["year"] == y]
        rel = [r for r in relays if r["year"] == y]

        gender_split = Counter(r["category"] for r in ind if r["category"])
        age_split = Counter(r["age_group"] for r in ind)
        meet_split = Counter(r["meet"] for r in ind + rel)
        month_split = Counter(r["month_num"] for r in ind + rel if r.get("month_num"))
        swimmer_counts = Counter(r["name"] for r in ind)
        top_swimmers = [
            {"name": n, "records_set": c}
            for n, c in swimmer_counts.most_common(10)
        ]

        # "Notable" records: highest FINA % first, top 5
        notable = sorted(
            [r for r in ind + rel if r.get("pct", 0) > 0],
            key=lambda r: r["pct"],
            reverse=True,
        )[:5]

        out[str(y)] = {
            "year": y,
            "total_individual": len(ind),
            "total_relay": len(rel),
            "total": len(ind) + len(rel),
            "by_gender": dict(gender_split),
            "by_age_group": dict(sorted(age_split.items())),
            "by_meet": dict(sorted(meet_split.items(), key=lambda kv: -kv[1])),
            "by_month": dict(sorted(month_split.items())),
            "top_swimmers": top_swimmers,
            "notable_records": [
                {
                    "name": r.get("name") or " / ".join(r.get("swimmers", [])),
                    "event": (
                        f"{r['distance']} {r['stroke']} {r['pool']}m "
                        + (r.get("age_group") or f"{r.get('min_age', '?')}+")
                    ),
                    "time": r["time"],
                    "pct": r.get("pct", 0),
                    "meet": r.get("meet", ""),
                    "month": r.get("month", ""),
                    "is_relay": r.get("is_relay", False),
                }
                for r in notable
            ],
        }
    return out


# ---------------------------------------------------------------------------
# Slim payload for the chronology HTML page
# ---------------------------------------------------------------------------
#
# brossard_chronology.html consumes a single global ``window.YEARLY`` populated
# by a tiny <script src="brossard_chronology_data.js"> tag. We emit a minified
# JSON payload with short field names to keep the file small (~400 KB).
#
# Field-name map (long → short):
#   year → y, stroke → k, distance → d, pool → p, age_group → a, name → n,
#   time → t, time_sec → s, meet → m, month_num → mo, category → c, pct → pct,
#   min_age → mn, swimmers → sw

def _slim_individual(r: dict) -> dict:
    o = {
        "y":  r["year"],
        "k":  r["stroke"],
        "d":  r["distance"],
        "p":  r["pool"],
        "a":  r["age_group"],
        "n":  r["name"],
        "t":  r["time"],
        "s":  r["time_sec"],
        "m":  r["meet"],
        "mo": r["month_num"],
    }
    if r.get("category"):
        o["c"] = r["category"]
    if r.get("pct"):
        o["pct"] = r["pct"]
    return o


def _slim_relay(r: dict) -> dict:
    return {
        "y":   r["year"],
        "k":   r["stroke"],
        "d":   r["distance"],
        "p":   r["pool"],
        "mn":  r["min_age"],
        "c":   r["category"],
        "sw":  r["swimmers"],
        "t":   r["time"],
        "s":   r["time_sec"],
        "m":   r["meet"],
        "mo":  r["month_num"],
        "pct": r.get("pct", 0),
    }


def _slim_by_year(by_year: dict) -> dict:
    """Keep only the fields the chronology page actually reads."""
    out = {}
    for k, v in by_year.items():
        out[k] = {
            "year":             v["year"],
            "total_individual": v["total_individual"],
            "total_relay":      v["total_relay"],
            "total":            v["total"],
            "by_gender":        v["by_gender"],
            "by_age_group":     v["by_age_group"],
            "by_meet":          v["by_meet"],
            "by_month":         v["by_month"],
            "top_swimmers":     v["top_swimmers"],
            "notable_records":  v["notable_records"],
        }
    return out


def _slim_snapshots(snapshots: list[dict]) -> list[dict]:
    """Strip name_raw and keep both top-10 + full leaderboards."""
    out = []
    for sn in snapshots:
        lw_full = [
            {"rank": r["rank"], "name": r["name"], "records_held": r["records_held"]}
            for r in sn["leaderboard_women"]
        ]
        lm_full = [
            {"rank": r["rank"], "name": r["name"], "records_held": r["records_held"]}
            for r in sn["leaderboard_men"]
        ]
        oldest = []
        for r in sn["oldest_records"]:
            entry = {k: v for k, v in r.items() if k != "name_raw"}
            oldest.append(entry)
        out.append({
            "year":              sn["year"],
            "leaderboard_women": lw_full[:10],
            "leaderboard_men":   lm_full[:10],
            "lw_full":           lw_full,
            "lm_full":           lm_full,
            "oldest_records":    oldest,
        })
    return out


def write_chronology_data_js(
    yearly_doc: dict,
    snapshots_doc: dict,
    out_path: Path | str = "brossard_chronology_data.js",
) -> Path:
    """Emit the slim payload that brossard_chronology.html loads via <script src>."""
    payload = {
        "individual":       [_slim_individual(r) for r in yearly_doc["individual"]],
        "relays":           [_slim_relay(r) for r in yearly_doc["relays"]],
        "by_year":          _slim_by_year(yearly_doc["by_year"]),
        "snapshots":        _slim_snapshots(snapshots_doc["snapshots"]),
        "records_per_year": yearly_doc["meta"]["records_per_year"],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    js = (
        "// Auto-generated by parse_brossard_yearly.py — do not edit by hand.\n"
        "// Source: statsman.ca chronology files. Re-run the parser to refresh.\n"
        f"window.YEARLY = {body};\n"
    )
    p = Path(out_path)
    p.write_text(js, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Maîtres de Brossard — yearly chronology parser")
    print("=" * 48)
    name_to_cat = load_name_to_category()
    if name_to_cat:
        print(f"  Loaded {len(name_to_cat)} names from brossard_individual.json")
    else:
        print("  (brossard_individual.json not found — gender will be derived")
        print("   from the per-page ordering of each yearly summary instead.)")

    # ---- Yearly logs ----
    print("\nFetching and parsing rmbb<YY>.htm files…")
    all_records = []
    all_relays = []
    log_counts = {}

    for yr in YEARLY_LOG_YEARS:
        yy = f"{yr % 100:02d}"
        fname = f"rmbb{yy}.htm"
        lines = fetch_or_local(fname)
        if not lines:
            print(f"  {yr}: skipped (no data)")
            continue
        log = parse_yearly_log(lines, yr)
        ind_summary, relays = parse_yearly_summary(lines)

        # Tag relays with year (the parser already did this, but be safe)
        for r in relays:
            r.setdefault("year", yr)

        # Cross-reference pct + category
        enrich_log(log, ind_summary, name_to_cat)

        all_records.extend(log)
        all_relays.extend(relays)
        log_counts[yr] = len(log) + len(relays)
        print(f"  {yr}: {len(log):3d} individual + {len(relays):2d} relay")

    # ---- Snapshots ----
    print("\nFetching and parsing rmb<YY>sta.htm files…")
    snapshots = []
    for yr in SNAPSHOT_YEARS:
        yy = f"{yr % 100:02d}"
        fname = f"rmb{yy}sta.htm"
        lines = fetch_or_local(fname)
        if not lines:
            print(f"  {yr}: skipped (no data)")
            continue
        snap = parse_snapshot(lines, yr, name_to_cat)
        snapshots.append(snap)
        print(
            f"  {yr}: {len(snap['leaderboard_women']):2d}W / "
            f"{len(snap['leaderboard_men']):2d}M / "
            f"{len(snap['oldest_records']):2d} oldest"
        )

    # ---- Compose output ----
    yearly_doc = {
        "meta": {
            "source": "statsman.ca — Maîtres de Brossard yearly chronology",
            "generated_by": "parse_brossard_yearly.py",
            "year_range": [min(YEARLY_LOG_YEARS), max(YEARLY_LOG_YEARS)],
            "total_individual": len(all_records),
            "total_relay": len(all_relays),
            "records_per_year": log_counts,
        },
        "individual": all_records,
        "relays": all_relays,
        "by_year": by_year_summary(all_records, all_relays),
    }

    snapshots_doc = {
        "meta": {
            "source": "statsman.ca — Maîtres de Brossard year-end snapshots",
            "generated_by": "parse_brossard_yearly.py",
            "year_range": [min(SNAPSHOT_YEARS), max(SNAPSHOT_YEARS)],
            "total_snapshots": len(snapshots),
        },
        "snapshots": snapshots,
    }

    out_y = Path("brossard_yearly.json")
    out_y.write_text(json.dumps(yearly_doc, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\nWrote {out_y}  ({len(all_records)} ind + {len(all_relays)} relay)")

    out_s = Path("brossard_snapshots.json")
    out_s.write_text(json.dumps(snapshots_doc, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"Wrote {out_s}  ({len(snapshots)} snapshots)")

    # ---- Write slim payload consumed by brossard_chronology.html ----
    out_js = write_chronology_data_js(yearly_doc, snapshots_doc)
    print(f"Wrote {out_js}  (inline payload for chronology page)")

    # ---- Summary ----
    print("\n── Yearly chronology summary ──")
    print(f"  Years parsed     : {min(log_counts) if log_counts else '–'}–"
          f"{max(log_counts) if log_counts else '–'}")
    print(f"  Total individual : {len(all_records)}")
    print(f"  Total relay      : {len(all_relays)}")
    busiest = sorted(log_counts.items(), key=lambda kv: -kv[1])[:5]
    print("  Busiest years    :")
    for y, c in busiest:
        print(f"    {y}: {c} records broken")
    quietest = [
        (y, c) for y, c in sorted(log_counts.items(), key=lambda kv: kv[1])[:5]
    ]
    print("  Quietest years   :")
    for y, c in quietest:
        print(f"    {y}: {c} records broken")

    # Derived stats: who set the most records over the parsed era
    name_counts = Counter()
    for r in all_records:
        name_counts[r["name"]] += 1
    print("\n  Top record-setters (by year-of-record-broken count):")
    for n, c in name_counts.most_common(10):
        print(f"    {n:<28} {c}")

    print("\nDone.")


if __name__ == "__main__":
    main()
