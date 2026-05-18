# Meet Results — Storage Spec

This document defines how parsed meet results are stored on disk, the canonical
swim schema, swimmer identity rules, record linkage, and the parser pipeline.

It is the contract between the parser (`parse_meet_results.py`) and any
consumer — the records dashboard, the swimmer profile page, the event
progression view, etc.

---

## Goals

1. Store every Brossard ("La Vague de Brossard") swim from every meet we have
   results for, going forward.
2. Make two access patterns cheap:
   - **All swims by one swimmer** → one fetch.
   - **All Brossard swims in one event-bucket across time** → one fetch.
3. Be hostable as static files on GitHub Pages (no server, no DB).
4. Keep the existing chronology JSON (`brossard_individual.json`,
   `brossard_relays.json`) as the source of truth for which swims set club
   records.

---

## Source data

Meet results live in `meet_results/{year}/` as one of:

- `*.zip` — a Splash export containing a `SplashResults.lef` (Lenex 3.0 XML)
  plus an `.sd3` (Hy-Tek SD3 fixed-width). **Lenex is the preferred parse
  path** — it is richer (full DOB, license/swrid, clean splits, named
  strokes).
- `*.sd3` — a bare SD3 file. Used as fallback when no lenex is shipped.

Each archive contains every swim by every club at that meet. The parser
filters to Brossard at parse time and discards the rest.

### Brossard club identifiers (lenex)

| Field   | Value                  |
|---------|------------------------|
| code    | `BRO`                  |
| clubid  | `10064`                |
| swrid   | `73992`                |
| name    | `La vague de Brossard` |

Match on `code == "BRO" AND nation == "CAN"`. `name` casing varies in
practice and is unreliable.

---

## Canonical swim schema

One row per individual swim. Stored line-delimited in
`data/swims.jsonl` (one JSON object per line — append-friendly, streamable).

```jsonc
{
  // identity
  "swim_id":      "20251018-140229258-50-FREE-25",  // deterministic, see below
  "swimmer_id":   "140229258",                       // CAN-FED license, primary key
  "swimmer_slug": "cynthia-brosseau",                // canonical slug fallback
  "swimmer_name": "Cynthia Brosseau",                // display form
  "firstname":    "Cynthia",
  "lastname":     "Brosseau",

  // demographics
  "gender":       "F",                               // F | M
  "dob":          "1970-12-06",                      // ISO date, may be null pre-FED-ID
  "age_at_meet":  54,                                // computed from dob + meet_date
  "age_group":    "55-59",                           // Masters 5-year band

  // event
  "stroke":       "FREE",                            // FREE | BACK | BREAST | FLY | MEDLEY
  "stroke_fr":    "lib",                             // lib | dos | bra | pap | qni
  "distance":     50,                                // metres
  "pool":         25,                                // 25 | 50 (SCM | LCM)
  "relay":        false,                             // individual only in v1
  "event_key":    "F-FREE-50-25",                    // cat-stroke-dist-pool (no age)
  "bucket_key":   "F-55-59-FREE-50-25",              // event_key with age_group

  // result
  "time_sec":     42.72,                             // seconds, 2 decimals
  "splits_sec":   [42.72],                           // cumulative split seconds, may be empty
  "status":       "OK",                              // OK | DNS | DSQ | WDR
  "place":        1,                                 // null if not computed
  "points":       7,                                 // FINA points, null if absent
  "entrytime_sec": 45.12,                            // null if not provided

  // meet
  "meet_id":      "2025-10-18-coupe-des-maitres-manche-1",
  "meet_name":    "Coupe des maîtres manche 1",
  "meet_date":    "2025-10-18",                      // primary session date
  "meet_city":    "St-Eustache",
  "meet_course":  "SCM",                             // SCM | LCM
  "meet_source":  "lenex",                           // lenex | sd3

  // record linkage (populated by a second pass)
  "set_record":   false,
  "record_key":   null                               // bucket_key if set_record
}
```

### swim_id construction

```
{meet_date YYYYMMDD}-{swimmer_id}-{distance}-{stroke}-{pool}
e.g. 20251018-140229258-50-FREE-25
```

For relay legs (later) we'll suffix `-relay-{leg}`. For swimmers without a
license, use `slug` in place of `swimmer_id` and prefix `slug-`.

### Stroke vocabulary

| Lenex   | French (existing dataset) | English   |
|---------|---------------------------|-----------|
| FREE    | lib                       | freestyle |
| BACK    | dos                       | backstroke|
| BREAST  | bra                       | breast    |
| FLY     | pap                       | butterfly |
| MEDLEY  | qni                       | IM        |

The existing chronology uses the French codes; we keep both on each swim
to avoid translation logic in the consumer.

### Age group derivation

Masters bands are 5-year, starting at 20. `age_at_meet` is computed from
`dob` and `meet_date` (whichever session the swim was in, falling back to
meet primary date).

```
20-24, 25-29, 30-34, 35-39, 40-44, 45-49, 50-54,
55-59, 60-64, 65-69, 70-74, 75-79, 80-84, 85-89, 90+
```

---

## File layout

```
data/
├── swims.jsonl                          # canonical, one swim per line
├── swimmers_index.json                  # {license: summary, ...}
├── meets_index.json                     # {meet_id: meta, ...}
├── swimmers/
│   └── {license}.json                   # all swims for that swimmer
└── events/
    └── {gender}-{stroke}-{distance}-{pool}.json
                                         # all Brossard swims in event, all ages
```

### swimmers_index.json

```jsonc
{
  "140229258": {
    "id":            "140229258",
    "name":          "Cynthia Brosseau",
    "slug":          "cynthia-brosseau",
    "gender":        "F",
    "dob":           "1970-12-06",
    "first_seen":    "2025-10-18",
    "last_seen":     "2026-04-26",
    "swim_count":    47,
    "records_held":  3,
    "year_range":    [2025, 2026]
  },
  ...
}
```

### meets_index.json

```jsonc
{
  "2025-10-18-coupe-des-maitres-manche-1": {
    "meet_id":     "2025-10-18-coupe-des-maitres-manche-1",
    "name":        "Coupe des maîtres manche 1",
    "date":        "2025-10-18",
    "city":        "St-Eustache",
    "course":      "SCM",
    "swim_count":  50,
    "source":      "lenex"
  },
  ...
}
```

### swimmers/{license}.json

```jsonc
{
  "swimmer": { ...summary from swimmers_index, plus aliases... },
  "swims":   [ ...all swims by this swimmer, sorted by meet_date asc... ]
}
```

### events/{gender}-{stroke}-{distance}-{pool}.json

```jsonc
{
  "event_key":   "F-FREE-50-25",
  "label_fr":    "F 50 lib (25m)",
  "swim_count":  312,
  "swims":       [ ...all Brossard swims in this event, all ages, sorted by meet_date asc... ]
}
```

For the swimmer profile chart we filter `swims` to a single `swimmer_id` and
sort by date. For the event progression chart we filter by `age_group`
(optional) and sort.

---

## Identity rules

### Primary key: `swimmer_id` = lenex `license` attribute

This is the CAN-FED registration number (also called Swimming Canada license).
It is stable across meets, federations, clubs, and years. Use it as the
canonical id whenever present.

### Fallback for older / unlicensed swimmers

The chronology dataset (1980–2025) has many swimmers without a federation ID
in our system. For those, fall back to a slug:

```
slug = normalize(firstname + "-" + lastname)
     = lowercase, strip accents, replace whitespace with "-",
       drop punctuation other than "-"
```

When a slug-only swimmer later appears in a meet results file *with* a
license, we record the merge in `swimmers_alias.json` and rewrite older
references.

### Name reconciliation

The chronology stores names as free text (`"Clarence Gagne"`). The meet
results store them split (`firstname="Clarence"`, `lastname="Gagne"`). The
parser normalizes both forms to the canonical `"Firstname Lastname"` shape
for display, while keeping the split form on swim rows for sorting.

---

## Record linkage

**Authoritative source: the existing chronology JSON.**
(`brossard_individual.json` + `brossard_relays.json`)

Algorithm:

1. Build an index of chronology entries keyed by
   `(date_match_loose, name_normalized, event_key, time_sec_rounded)`.
2. For each parsed swim, look it up:
   - `date_match_loose`: same year + month (chronology only has month
     names, not exact dates)
   - `name_normalized`: lowercased, accent-stripped, "First Last"
   - `event_key`: same stroke + distance + pool + gender
   - `time_sec_rounded`: ±0.01s tolerance
3. If a match is found: `set_record=true`, `record_key=bucket_key`.
4. Otherwise: `set_record=false`. We **do not** infer records that aren't
   in the chronology, even if the time looks like a new best — incomplete
   coverage of historical meets means we'd produce false positives.

Going forward (once meet coverage is complete from 2025 onward), we can add a
second derived-records pass that walks swims chronologically per bucket and
emits new record events. For now, chronology is truth.

---

## Parser pipeline

```
parse_meet_results.py
├── 1. discover meet files     meet_results/{year}/*.{zip,sd3}
├── 2. for each file:
│      ├── if .zip → extract .lef, parse lenex (preferred)
│      ├── if .sd3 (bare) → parse sd3 (fallback)
│      └── filter to club BRO
├── 3. normalize each result to canonical swim shape
├── 4. emit data/swims.jsonl (overwritten on each run)
└── 5. derived outputs:
       ├── data/swimmers_index.json
       ├── data/meets_index.json
       ├── data/swimmers/{license}.json
       └── data/events/{gender}-{stroke}-{dist}-{pool}.json
```

Record-linkage pass runs after step 3, before step 5, so derived files carry
correct `set_record`/`record_key` values.

The script is idempotent: re-running over the same `meet_results/` tree
should produce byte-identical output (modulo JSON key ordering, which we
fix). A `--dry-run` mode prints summary stats without writing.

---

## Out of scope (v1)

- **Relay legs.** Lenex stores relay results under `<RELAY>` with
  `<RELAYPOSITIONS>` listing each leg's swimmer. We'll add these in v2; the
  schema reserves `relay: false` and `swim_id` suffix for them.
- **Splits validation.** We store whatever splits the meet file provides
  without checking they sum to the final time.
- **Place computation.** We only emit `place` if it's in the source file.
  Lenex doesn't always include it; we'll derive at consumer-side if needed.
- **Pre-2000 historical meets.** Meet result files only exist for recent
  seasons. Older swims live in the chronology and remain there.
