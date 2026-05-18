#!/usr/bin/env python3
"""
Generate brossard_inline_data.js — pre-bundled JSON payloads for offline (file://)
loading. Reads:

  - brossard_combined.json                   (statsman records)
  - brossard_swimrankings_athletes.json      (swimrankings bios)
  - brossard_swimrankings_swims.json         (swimrankings flat swims)
  - data/swims.jsonl                         (swimming.ca Lenex swims — JSONL)
  - data/swimmers_index.json                 (Lenex swimmer bios)

Emits brossard_inline_data.js with:

  window._brossardInline = {
    records, relays, athletes, swims, _built
  };

Swims from both sources live in the same flat list with `source` distinguishing
provenance. On overlap (same athlete/date/event/course), the Lenex row wins
since it carries place/points/splits and is the project's primary post-Feb-2025
source. Five Lenex-only swimmers (no swimrankings profile) are synthesized as
athlete entries keyed by their Lenex swimmer_id (always >100M — won't collide
with swimrankings aids, which sit in the 4–6M range).

Run: python3 build_inline_data.py
"""
import json, pathlib, re, sys, unicodedata

ROOT = pathlib.Path(__file__).resolve().parent


def load(name):
    p = ROOT / name
    if not p.exists():
        sys.stderr.write(f"Missing {name}\n")
        return None
    with p.open() as f:
        return json.load(f)


def norm(s: str) -> str:
    s = ''.join(c for c in unicodedata.normalize('NFD', s or '') if not unicodedata.combining(c))
    s = re.sub(r'[-]', ' ', s.lower())
    return re.sub(r'[^a-z0-9 ]', '', s).strip()


def canon_tokens(s: str) -> str:
    """Order-independent canonical form: sort the normalized tokens."""
    return ' '.join(sorted(norm(s).split()))


def format_time_string(sec):
    """Render time_sec back to a swimrankings-style "MM:SS.HH" or "SS.HH" string."""
    if sec is None:
        return None
    sec = float(sec)
    if sec >= 60:
        m = int(sec // 60)
        remainder = sec - m * 60
        return f"{m}:{remainder:05.2f}"
    return f"{sec:.2f}"


# ── Load sources ─────────────────────────────────────────────────────────
records_full = load("brossard_combined.json")
athletes_full = load("brossard_swimrankings_athletes.json")
sr_swims_full = load("brossard_swimrankings_swims.json")
lenex_idx = load("data/swimmers_index.json") or {}

records = records_full["individual"] if records_full else []
relays = records_full.get("relays", []) if records_full else []
sr_athletes = athletes_full["athletes"] if athletes_full else []
sr_swims_raw = sr_swims_full if isinstance(sr_swims_full, list) else []

# ── Masters-only filter on swimrankings rows ─────────────────────────────
# swimrankings encodes the Masters category in the time-suffix field: rows
# with timeSuffix == "M" are Masters-category swims; rows with no suffix are
# junior/age-group races visible on the same athleteDetail page (e.g. Kyra
# Lalonde swam ~360 junior races 2013–2021 before her first Masters meet in
# Oct 2022). The club site is exclusively about Masters records and stats, so
# we drop the non-M rows here at build time. The Lenex feed needs no filter:
# every Lenex swim comes from a swimming.ca Masters meet by definition.
sr_swims = [s for s in sr_swims_raw if s.get('timeSuffix') == 'M']
sr_filtered_count = len(sr_swims_raw) - len(sr_swims)

# Read JSONL swims line-by-line
lenex_swims = []
jsonl_path = ROOT / "data" / "swims.jsonl"
if jsonl_path.exists():
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                lenex_swims.append(json.loads(line))
else:
    sys.stderr.write("Missing data/swims.jsonl — Lenex swims will be skipped.\n")


# ── Build swimrankings athlete index for the join ───────────────────────
sw_idx = {}  # (canon_tokens, yob) → aid
for a in sr_athletes:
    full = (a.get('first') or '') + ' ' + (a.get('last') or '')
    key = (canon_tokens(full), a.get('yob'))
    sw_idx[key] = a['aid']

# Map Lenex swimmer_id → swimrankings aid (or None when unmatched)
lenex_to_aid = {}
synthesized = []  # bios we'll add to the athletes array for Lenex-only swimmers
for sid, s in lenex_idx.items():
    yob_str = (s.get('dob') or '')[:4]
    yob = int(yob_str) if yob_str.isdigit() else None
    ck = (canon_tokens(s['name']), yob)
    aid = sw_idx.get(ck)
    if aid:
        lenex_to_aid[sid] = aid
    else:
        # Synthesize an athlete entry. The Lenex IDs are in the hundreds of
        # millions — far from the 4-6M swimrankings range, so there's no
        # collision risk when we use the Lenex ID directly as athleteId.
        synth_aid = int(sid)
        lenex_to_aid[sid] = synth_aid
        parts = (s.get('name') or '').rsplit(' ', 1)
        first = parts[0] if len(parts) == 2 else ''
        last = parts[-1].upper() if parts else ''
        synthesized.append({
            'aid': synth_aid,
            'last': last,
            'first': first,
            'yob': yob,
            'club': 'La Vague de Brossard',
            'styleIds': [],          # Bingo doesn't depend on this; events are derived from swims
            'source': 'lenex',       # flag so the UI can distinguish
        })


# ── Convert Lenex swims to the unified schema ──────────────────────────
STROKE_FR = {'FREE': 'libre', 'BACK': 'dos', 'BREAST': 'bra', 'FLY': 'pap', 'MEDLEY': 'qni'}

converted_lenex = []
unmatched_swim_count = 0
for ls in lenex_swims:
    if ls.get('relay'):
        # Skip relay legs for now — bingo card filters out isLap swims anyway.
        continue
    sid = ls.get('swimmer_id')
    aid = lenex_to_aid.get(sid)
    if not aid:
        unmatched_swim_count += 1
        continue
    # Look up the swimmer's name from our resolved athletes
    bio = next((a for a in sr_athletes + synthesized if a.get('aid') == aid), None)
    last = bio.get('last') if bio else (ls.get('lastname') or '').upper()
    first = bio.get('first') if bio else ls.get('firstname') or ''
    course = ls.get('meet_course')
    if not course:
        course = 'SCM' if ls.get('pool') == 25 else 'LCM'
    converted_lenex.append({
        'athleteId': aid,
        'lastName': last,
        'firstName': first,
        'distance': ls.get('distance'),
        'stroke': ls.get('stroke'),
        'stroke_fr': STROKE_FR.get(ls.get('stroke')),
        'course': course,
        'isLap': False,
        'time': format_time_string(ls.get('time_sec')),
        'timeSec': ls.get('time_sec'),
        'timeSuffix': None,
        'finaPoints': None,   # Lenex 'points' is QC ranking, not FINA-comparable
        'date': ls.get('meet_date'),
        'city': ls.get('meet_city'),
        'meetName': ls.get('meet_name'),
        'place': ls.get('place'),
        'source': 'lenex',
        'lenexSwimmerId': sid,
    })


# ── Merge & dedupe ─────────────────────────────────────────────────────
# Key on (athleteId, date, distance, stroke, course). Lenex wins on overlap.
def keyf(s):
    return (s.get('athleteId'), s.get('date'), s.get('distance'), s.get('stroke'), s.get('course'))

lenex_keys = {keyf(s) for s in converted_lenex}

SWIM_KEEP = {
    "athleteId", "lastName", "firstName", "styleId", "distance", "stroke", "stroke_fr",
    "course", "isLap", "time", "timeSec", "timeSuffix", "finaPoints", "date", "city",
    "meetName", "place", "source", "lenexSwimmerId"
}
trimmed_sr = []
duplicates_replaced = 0
for s in sr_swims:
    if keyf(s) in lenex_keys:
        duplicates_replaced += 1
        continue
    # Tag swimrankings rows explicitly so the UI can distinguish provenance
    trimmed = {k: s[k] for k in SWIM_KEEP if k in s}
    trimmed.setdefault('source', 'swimrankings')
    trimmed_sr.append(trimmed)

trimmed_lenex = [{k: s[k] for k in SWIM_KEEP if k in s} for s in converted_lenex]
all_swims = trimmed_sr + trimmed_lenex


# ── Output ──────────────────────────────────────────────────────────────
athletes_out = list(sr_athletes) + synthesized

payload = {
    "records": records,
    "relays": relays,
    "athletes": athletes_out,
    "swims": all_swims,
    "_built": "build_inline_data.py — regenerate when source JSON or Lenex data changes",
}

out_path = ROOT / "brossard_inline_data.js"
with out_path.open("w") as f:
    f.write("/* AUTO-GENERATED — regenerate with: python3 build_inline_data.py */\n")
    f.write("window._brossardInline = ")
    json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
    f.write(";\n")

size_kb = out_path.stat().st_size / 1024
print(f"Wrote {out_path.name}: {size_kb:.0f} KB")
print(f"  records:                {len(records)}")
print(f"  relays:                 {len(relays)}")
print(f"  athletes (swimrankings):{len(sr_athletes)}")
print(f"  athletes (lenex-synth): {len(synthesized)}  ({', '.join(a['first']+' '+a['last'] for a in synthesized[:5])}{'…' if len(synthesized) > 5 else ''})")
print(f"  swims (swimrankings):   {len(trimmed_sr)}  (Masters only; dropped {sr_filtered_count} non-M junior rows; {duplicates_replaced} more replaced by Lenex)")
print(f"  swims (lenex):          {len(trimmed_lenex)}")
print(f"  swims total:            {len(all_swims)}")
if unmatched_swim_count:
    print(f"  ⚠ {unmatched_swim_count} Lenex swims skipped (no swimmer match)")
