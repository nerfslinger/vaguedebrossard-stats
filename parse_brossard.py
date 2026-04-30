#!/usr/bin/env python3
"""
parse_brossard.py
=================
Parses the three Maîtres de Brossard chronology HTM files into structured JSON.

Usage:
    python parse_brossard.py

Input files (fetched from statsman.ca, or place locally):
    krobf.htm   – women's individual records
    krobm.htm   – men's individual records
    krobr.htm   – relay records

Output files:
    brossard_individual.json   – all individual records, one object per swim
    brossard_relays.json       – all relay records, one object per swim
    brossard_combined.json     – both merged into one file

Record object schema (individual):
{
  "category":   "F" | "M",
  "stroke":     "lib" | "dos" | "bra" | "pap" | "qni",
  "stroke_en":  "freestyle" | "backstroke" | ...,
  "distance":   50 | 100 | 200 | 400 | 800 | 1500,
  "pool":       25 | 50,
  "age_group":  "20-24" | "25-29" | ... | "80-84",
  "name":       "Lucie Rochon",
  "time":       "32.48",           # as written in the source
  "time_sec":   32.48,             # always in seconds for sorting/charting
  "meet":       "Bro",
  "month":      "Nov",
  "year":       1981,
  "pct":        83,                # FINA/Masters points (0 if not present)
  "note":       ""                 # e.g. "?" for uncertain marks
}

Relay object schema:
{
  "category":    "hom" | "fem" | "mix",
  "min_age":     80 | 100 | 120 | 160 | 200 | 240 | 280,
  "relay_type":  "4x50lib" | "4x100lib" | "4x50rqn" | "4x100rqn" | "4x200lib",
  "pool":        25 | 50,
  "time":        "2:25.90",
  "time_sec":    145.90,
  "meet":        "Ste",
  "month":       "Mar",
  "year":        1981,
  "pct":         65,
  "swimmers":    ["C.Ducharme", "Jacquelin", "R.Houde", "J.Fortier"]
}
"""

import re
import json
from pathlib import Path
from urllib.request import urlretrieve
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://statsman.ca/rec/"
FILES = {
    "F": "krobf.htm",
    "M": "krobm.htm",
    "R": "krobr.htm",
}

STROKE_EN = {
    "lib": "freestyle",
    "dos": "backstroke",
    "bra": "breaststroke",
    "pap": "butterfly",
    "qni": "individual medley",
}

MONTH_MAP = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "avr": 4, "apr": 4,
    "mai": 5, "may": 5, "jun": 6, "jul": 7, "aou": 8, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_or_local(filename: str) -> list[str]:
    """Return lines from a local file if present, otherwise download it."""
    local = Path(filename)
    if local.exists():
        print(f"  Using local file: {filename}")
        return local.read_text(encoding="utf-8", errors="replace").splitlines()
    url = BASE_URL + filename
    print(f"  Downloading: {url}")
    try:
        tmp, _ = urlretrieve(url)
        text = Path(tmp).read_text(encoding="utf-8", errors="replace")
        local.write_text(text, encoding="utf-8")   # cache locally
        return text.splitlines()
    except URLError as e:
        raise SystemExit(f"Could not fetch {url}: {e}")


def parse_year(raw: str) -> int:
    y = int(raw.strip())
    return (2000 + y) if y < 30 else (1900 + y)


def parse_time_sec(t: str) -> float:
    """Convert 'M:SS.ss' or 'SS.ss' to float seconds."""
    t = t.strip()
    if ":" in t:
        parts = t.split(":")
        return int(parts[0]) * 60 + float(parts[1])
    return float(t)


def normalize_age(raw: str) -> str:
    """'20-24', '25-29' … already correct; just strip."""
    return raw.strip()


# ---------------------------------------------------------------------------
# Individual record parser  (krobf.htm / krobm.htm)
# ---------------------------------------------------------------------------
#
# Section header examples (the line that starts a new event block):
#   " 50 lib 20-24 25m"
#   "100 lib 20-24 50m"
#   "1500 lib 20-24 25m"
#
# Data line examples:
#   "Lucie Rochon          32.48 Bro Nov 81   83"
#   "Josee Grondin       1:07.55 PC  Avr 91   87"
#   "Lucie Rochon        ? 36.70 Cow Avr 81   84"   ← uncertain mark
#   "Natasha Cassivi    22:00.34 PC  APR 24   84"
#
# The time field can be preceded by "? " for uncertain marks.
# pct (the trailing integer) is sometimes absent.

HEADER_RE = re.compile(
    r"^\s*(\d+)\s+(lib|dos|bra|pap|qni)\s+(\d{2}-\d{2})\s+(\d{2})m\s*$",
    re.IGNORECASE,
)

# Flexible data line: name (at least 2 words), optional "?", time, meet, month, 2-digit year, optional pct
DATA_RE = re.compile(
    r"^(?P<name>[A-Za-zÀ-ÿ'\-][A-Za-zÀ-ÿ'\- .]+?)\s+"
    r"(?P<note>\?\s*)?"
    r"(?P<time>\d{1,2}(?::\d{2}\.\d{2}|\.\d{2}))\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Za-z]{3})\s+"
    r"(?P<year>\d{2})"
    r"(?:\s+(?P<pct>\d+))?\s*$"
)


def parse_individual(lines: list[str], category: str) -> list[dict]:
    records = []
    current = {}   # current event context

    for raw_line in lines:
        line = raw_line.rstrip()

        # Skip blank lines and markdown fences
        if not line.strip() or line.strip().startswith("```"):
            continue

        # Try section header
        hm = HEADER_RE.match(line)
        if hm:
            current = {
                "distance": int(hm.group(1)),
                "stroke":   hm.group(2).lower(),
                "age_group": normalize_age(hm.group(3)),
                "pool":     int(hm.group(4)),
            }
            continue

        if not current:
            continue

        # Try data line
        dm = DATA_RE.match(line)
        if dm:
            name = dm.group("name").strip()
            # Skip lines that look like subtotals or page markers
            if re.match(r"^\d", name) or "subtotal" in name.lower():
                continue
            note = "?" if dm.group("note") else ""
            time_str = dm.group("time")
            try:
                time_sec = parse_time_sec(time_str)
            except ValueError:
                continue

            records.append({
                "category":  category,
                "stroke":    current["stroke"],
                "stroke_en": STROKE_EN.get(current["stroke"], current["stroke"]),
                "distance":  current["distance"],
                "pool":      current["pool"],
                "age_group": current["age_group"],
                "name":      name,
                "time":      time_str,
                "time_sec":  round(time_sec, 2),
                "meet":      dm.group("meet"),
                "month":     dm.group("month").capitalize(),
                "year":      parse_year(dm.group("year")),
                "pct":       int(dm.group("pct")) if dm.group("pct") else 0,
                "note":      note,
            })

    return records


# ---------------------------------------------------------------------------
# Relay parser  (krobr.htm)
# ---------------------------------------------------------------------------
#
# Relay section header examples:
#   " 80+ 4x50 lib 25m hom"
#   " 80+ 4x50lib 50m fem"          ← no space between 4x50 and lib
#   " 80+ 4x50 rqn 25m mix"
#   " 80+ 4x100 lib 25m hom"
#   "100+ 4x50 lib 25m fem"         ← 100+ combinated age
#   "120+ 4x50lib 50m mix"
#
# Data lines (time first):
#   " 2:25.90 Ste Mar 81 65   C.Ducharme Jacquelin R.Houde J.Fortier"
#   " 1:59.33 PC  Mar 84 80   F.Bedard M.Filteau Lei Lim A.Nolet"
#
# The swimmers are space-separated after the pct column.

RELAY_HEADER_RE = re.compile(
    r"^\s*(?P<minage>\d+)\+\s+"
    r"4x(?P<legs>\d+)\s*(?P<stroke>lib|rqn|dos|bra|pap)\s+"
    r"(?P<pool>\d{2})m\s+"
    r"(?P<cat>hom|fem|mix)\s*$",
    re.IGNORECASE,
)

RELAY_DATA_RE = re.compile(
    r"^\s*(?P<time>\d{1,2}:\d{2}\.\d{2})\s+"
    r"(?P<meet>\S+)\s+"
    r"(?P<month>[A-Za-z]{3})\s+"
    r"(?P<year>\d{2})\s+"
    r"(?P<pct>\d+)\s+"
    r"(?P<swimmers>.+)$"
)

RELAY_STROKE_EN = {
    "lib": "freestyle",
    "rqn": "medley",
    "dos": "backstroke",
    "bra": "breaststroke",
    "pap": "butterfly",
}


def parse_relays(lines: list[str]) -> list[dict]:
    records = []
    current = {}

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("```"):
            continue

        # Separator lines
        if re.match(r"^-+$", line.strip()):
            continue

        hm = RELAY_HEADER_RE.match(line)
        if hm:
            legs = int(hm.group("legs"))
            stroke = hm.group("stroke").lower()
            current = {
                "category":   hm.group("cat").lower(),
                "min_age":    int(hm.group("minage")),
                "relay_type": f"4x{legs}{stroke}",
                "relay_en":   f"4×{legs} {RELAY_STROKE_EN.get(stroke, stroke)}",
                "pool":       int(hm.group("pool")),
            }
            continue

        if not current:
            continue

        dm = RELAY_DATA_RE.match(line)
        if dm:
            time_str = dm.group("time")
            try:
                time_sec = parse_time_sec(time_str)
            except ValueError:
                continue
            swimmers_raw = dm.group("swimmers").strip()
            # Swimmers are separated by spaces; names may contain dots
            swimmers = [s.strip() for s in re.split(r"\s{2,}", swimmers_raw) if s.strip()]
            # Fallback: single-space split if we got only one token
            if len(swimmers) == 1:
                swimmers = swimmers_raw.split()

            records.append({
                **current,
                "time":     time_str,
                "time_sec": round(time_sec, 2),
                "meet":     dm.group("meet"),
                "month":    dm.group("month").capitalize(),
                "year":     parse_year(dm.group("year")),
                "pct":      int(dm.group("pct")),
                "swimmers": swimmers,
            })

    return records


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def summarize(records: list[dict]) -> dict:
    years = [r["year"] for r in records]
    names = [r.get("name") or "relay" for r in records]
    from collections import Counter
    top_swimmers = Counter(names).most_common(10)
    return {
        "total_records": len(records),
        "year_range": [min(years), max(years)] if years else [],
        "unique_swimmers": len(set(names)),
        "top_10_by_records_set": [
            {"name": n, "records_set": c} for n, c in top_swimmers
        ],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Maîtres de Brossard — HTM → JSON parser")
    print("=" * 42)

    individual = []

    for cat, filename in [("F", FILES["F"]), ("M", FILES["M"])]:
        print(f"\nParsing {filename} (category={cat})…")
        lines = fetch_or_local(filename)
        recs = parse_individual(lines, cat)
        print(f"  → {len(recs)} individual record entries")
        individual.extend(recs)

    print(f"\nParsing {FILES['R']} (relays)…")
    relay_lines = fetch_or_local(FILES["R"])
    relays = parse_relays(relay_lines)
    print(f"  → {len(relays)} relay record entries")

    # ---- Write individual JSON ----
    out_ind = Path("brossard_individual.json")
    out_ind.write_text(
        json.dumps(individual, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\nWrote {out_ind}  ({len(individual)} records)")

    # ---- Write relay JSON ----
    out_rel = Path("brossard_relays.json")
    out_rel.write_text(
        json.dumps(relays, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"Wrote {out_rel}  ({len(relays)} records)")

    # ---- Write combined JSON ----
    combined = {
        "meta": {
            "source": "statsman.ca — Maîtres de Brossard chronology",
            "generated_by": "parse_brossard.py",
            "individual_summary": summarize(individual),
            "relay_summary": summarize(relays),
        },
        "individual": individual,
        "relays": relays,
    }
    out_comb = Path("brossard_combined.json")
    out_comb.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"Wrote {out_comb}  (individual + relays + metadata)")

    # ---- Print summary ----
    print("\n── Individual records summary ──")
    s = summarize(individual)
    print(f"  Total entries   : {s['total_records']}")
    print(f"  Year range      : {s['year_range'][0]}–{s['year_range'][1]}")
    print(f"  Unique swimmers : {s['unique_swimmers']}")
    print(f"  Top record-setters (all-time):")
    for row in s["top_10_by_records_set"]:
        print(f"    {row['name']:<30} {row['records_set']}")

    print("\n── Relay records summary ──")
    s2 = summarize(relays)
    print(f"  Total entries   : {s2['total_records']}")
    print(f"  Year range      : {s2['year_range'][0]}–{s2['year_range'][1]}")

    print("\nDone.")


if __name__ == "__main__":
    main()
