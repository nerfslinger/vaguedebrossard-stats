# Maîtres de Brossard — Club Records Modernization
## Project Handoff & Structure

---

## What this project is

We are modernizing the historical club records for **Maîtres de Brossard**, a Masters swimming club based in Brossard, Québec. The existing records are currently hosted at [statsman.ca](https://www.statsman.ca/index0.html) as a collection of static HTM files dating back to 1980 — functional but difficult to navigate, unsearchable, and not visually engaging.

The goal is to transform 45 years of club history into a modern, visually compelling, and easy-to-use web experience.

---

## What has been built so far

### 1. Parser script — `parse_brossard.py`
A standalone Python 3 script (no third-party dependencies) that:
- Downloads three source HTM files from statsman.ca:
  - `krobf.htm` — women's individual record chronology
  - `krobm.htm` — men's individual record chronology
  - `krobr.htm` — relay record chronology
- Parses them into structured JSON using regex, handling edge cases:
  - Times in both `SS.ss` and `M:SS.ss` format (always converted to seconds)
  - Two-digit years correctly split at 2030
  - The `?` uncertain-mark flag on some historical entries
  - Relay headers with or without spaces (e.g. `4x50lib` vs `4x50 lib`)
  - Combined age categories in relays (80+, 100+, 120+, etc.)
- Outputs three files:
  - `brossard_individual.json` — every record-setting swim, one object per row
  - `brossard_relays.json` — every relay record with full swimmer list
  - `brossard_combined.json` — both merged with a metadata/summary block

**Individual record schema:**
```json
{
  "category":   "F",
  "stroke":     "lib",
  "stroke_en":  "freestyle",
  "distance":   50,
  "pool":       25,
  "age_group":  "20-24",
  "name":       "Clarence Gagne",
  "time":       "28.91",
  "time_sec":   28.91,
  "meet":       "QUE",
  "month":      "Avr",
  "year":       2014,
  "pct":        93,
  "note":       ""
}
```

**Run with:** `python3 parse_brossard.py`

---

### 2. Records dashboard — `index.html`
A fully self-contained HTML/CSS/JS file (no build step, no framework, no server required) implementing the primary user-facing interface. Design direction: **bold and sporty** — Bebas Neue display font, dark navy background, teal accent for times, gold for exceptional performances.

**Features:**
- Stroke sidebar with live record counts
- Category (Women / Men) and pool length (25m / 50m) filter pills
- Live name/event search
- Records grouped by age band, each with a sticky header
- Record age indicator dots (green = recent, gold = mid, red = old/untouched)
- FINA/Masters % score badges (colour-coded)
- Live KPI strip in the header (total records, swimmer count, top score, oldest record year)

**Current limitation:** The data is manually embedded as a JS array (~160 representative records). Once `parse_brossard.py` is run against the full source files, the complete dataset (~3,000–5,000 rows) should replace it.

---

### 3. Chronology / Year-in-Review widget *(prototype only)*
An interactive inline widget (built in this chat, not saved as a file) demonstrating:
- Year selector (1997–2025) with clickable bar chart
- Per-year metrics (records broken, swimmers active, M/F split)
- Top record-setters per year
- Notable records broken
- Club notes (e.g. the COVID-19 dip in 2020)

This widget used mostly estimated data for most years. It needs to be rebuilt using real parsed JSON once the pipeline is complete.

---

## Source data reference

| URL | Contents |
|-----|----------|
| `statsman.ca/index0.html` | Main index — links to all record pages |
| `statsman.ca/index26.htm` | Chronology index — links to krobf/krobm/krobr + all-time leaderboard |
| `statsman.ca/rec/krobf.htm` | Women's individual record chronology (full history per event) |
| `statsman.ca/rec/krobm.htm` | Men's individual record chronology |
| `statsman.ca/rec/krobr.htm` | Relay record chronology |
| `statsman.ca/rec/rmb25f7a.htm` | Current women's records (most recent season) |
| `statsman.ca/rec/rmbb25.htm` | Records broken in 2025 |
| `statsman.ca/rec/rmbb97.htm` … `rmbb25.htm` | Records broken per year, 1997–2025 |

**Key data facts:**
- Records go back to 1980
- ~45 years of history across women's, men's, and relay events
- Events span: 50/100/200/400/800/1500m × freestyle/backstroke/breaststroke/butterfly/IM × 25m/50m pool × 13 age groups (20-24 through 80-84)
- All-time leaders: Gail Desjardins (329 records, women), Christian Berger (307, men)
- The `%` field is a FINA/Masters points score — very useful for cross-age-group comparison
- Some historical records carry a `?` flag indicating uncertainty

---

## Proposed project structure

Given the scope, this is best split into three parallel workstreams:

---

### Workstream A — Data pipeline
**Goal:** Produce clean, complete JSON from all source HTM files.

Tasks:
1. Run `parse_brossard.py` against the live statsman.ca files
2. Review and validate output — spot-check counts against the all-time leaderboard on `index26.htm`
3. Fix any parsing edge cases found in the full dataset (the test sample covered ~160 records; the full set will surface new patterns)
4. Add a yearly records pipeline: parse `rmbb97.htm` through `rmbb25.htm` to produce a `brossard_yearly.json` with records-broken-per-year data (needed for the Year-in-Review view)
5. Optionally: write a `validate.py` script that cross-checks the JSON against the current records pages to flag inconsistencies

**Output:** `brossard_combined.json`, `brossard_yearly.json`

---

### Workstream B — Records dashboard (primary UI)
**Goal:** Complete and polish `index.html` with real data.

Tasks:
1. Replace the manually embedded JS data array with the full parsed dataset from Workstream A
2. Add a "Swimmer profile" mode — click any name to see all records they hold
3. Add deep-linking / URL parameters so a filtered view can be bookmarked or shared (e.g. `?cat=F&stroke=lib&age=60-64`)
4. Accessibility pass — keyboard navigation, ARIA labels, sufficient colour contrast
5. Mobile layout refinement
6. Consider whether to add a "records broken this year" highlight section at the top

**Output:** Final `index.html` (self-contained, hostable on statsman.ca)

---

### Workstream C — History & storytelling views
**Goal:** Build the Year-in-Review and swimmer career timeline views (prototyped in this chat, needs real data and proper implementation).

Tasks:
1. **Year-in-Review page** — rebuild the year selector widget using `brossard_yearly.json`, with real per-year swimmer leaderboards and notable records
2. **Event chronology viewer** — for any event (e.g. Women 50-54 100m freestyle 25m), show the full progression of every swimmer who has ever held that record, with a line chart of time improvement and a timeline list
3. **Swimmer career page** — given a name, show every record they've ever set (not just current), the years they were most active, and which records still stand
4. **All-time leaderboard page** — ranked by total records set (career) and by current records held, with a visual bar chart
5. Optionally: "Records in danger" — flag current records within 5% of provincial/national Masters standards

**Output:** Additional HTML pages or sections that link from the main dashboard

---

## Suggested starting point for a new Cowork session

> "We are building a modern club records site for Maîtres de Brossard, a Masters swimming club. The parser script `parse_brossard.py` and the primary dashboard `index.html` are already built (see attached files). The dashboard currently uses a manually embedded subset of ~160 records. 
>
> **First task:** Run the parser to produce the full JSON dataset, validate it, then update the dashboard's data array with the complete records. Then begin Workstream B task 2: add a swimmer profile mode so clicking any name shows all records that swimmer holds."

---

## Files to attach when starting Cowork

- `parse_brossard.py` — the parser
- `index.html` — the current dashboard

Both are in the outputs from this conversation.

---

## Swimrankings harvest layer (added 2026-05-15)

The project now has a second data source layered on top of statsman records: a one-time historical backfill from swimrankings.net covering 24 years (2002-2026) of swim-level data for active and historical Brossard members.

### Output files
- `brossard_swimrankings_athletes.json` — 67 athletes with bio + per-style event list
- `brossard_swimrankings_swims.json` — 4,387 flat swim records (~1.4 MB)
- `brossard_swimrankings_meta.json` — harvest run metadata, including the 38-athlete resume list

### Status as of 2026-05-15
Pass 0 (roster discovery), Pass 1 (BEST page parse), and Pass 2 (per-event swim harvest) are all started; Pass 2 is 53% complete. The remaining 38 athletes hit swimrankings' daily page-view quota mid-run and need a resume in a future session (~24h after the cap was triggered). 21 athleteIds are gated by per-profile NO_ACCESS restrictions and may need a different access method (e.g. logged-in Manager account).

Notable catches in the existing data: Christian Berger (583 swims), Kyra Lalonde (384), Matei Petrescu (342), **Gail Desjardins (332 swims — the all-time #1 women's records leader, captured via Pass 0a intra-club meet roster despite no swimrankings name search match)**, Carlos Aviles (204 — project owner, statsman record-setter).

### How the new data integrates
The unified swim schema is designed so that swimrankings rows and swimming.ca Lenex rows live in the same flat list (`source: "swimrankings"` vs `"lenex"`), enabling:
- Swimmer-profile pages showing full career PB progression, not just records
- Year-in-review storytelling (most active swimmers, swim counts, debuts)
- Per-event time progression charts with record-setting points highlighted

Going forward, new swims (post-Feb-2025) come from swimming.ca Lenex downloads via the ingest pipeline (`parse_meet_results.py` → `data/swims.jsonl`); swimrankings is purely a historical backfill source and the `parse_swimrankings.py` live-window script is superseded.

### Resuming Pass 2 (next session)
1. Re-inject the scraper module (`window.brossardScraper` is wiped between sessions).
2. Read `brossard_swimrankings_athletes.json` for the 38 `athletesAwaitingPass2` aids + their styleIds.
3. **Also harvest the 5 Lenex-only athletes** listed under "Two-source dataset merge" below — they're known active members with swimrankings profiles we just haven't fetched yet.
4. Fire per-event fetches at conservative throttle (pauseEvery=15, pauseMs=30s).
5. Append new swims to `brossard_swimrankings_swims.json` (dedupe key: `athleteId|date|distance|stroke|course|time`).
6. **Always re-run `python3 build_inline_data.py`** afterward so the front-end picks up the new data.

---

## Two-source dataset merge (added 2026-05-16)

The site's front-end now reads from a *merged* dataset produced by `build_inline_data.py`. Two source pipelines feed into one inline payload that `brossard_data.js` queries.

### The pipelines

| Source | Files | Role | ID space |
|---|---|---|---|
| **swimrankings.net** | `brossard_swimrankings_athletes.json` (67 bios), `brossard_swimrankings_swims.json` (4,387 swims) | Historical backfill 2002 → mid-2026 | 4M–6M (e.g. `4204240` = Berger) |
| **swimming.ca Lenex** | `data/swimmers_index.json` (33 bios), `data/swims.jsonl` (361 swims), `data/swimmers/*.json` (per-swimmer detail), `data/meets_index.json` | Primary ongoing source from Oct 2025 forward (richer: meet name, place, points, splits) | 127M–140M (no collision with swimrankings) |

The two ID spaces are disjoint by orders of magnitude, so they can coexist in the same `athleteId` column without collision.

### `build_inline_data.py` — merge logic

The build script:

1. Loads all five inputs (records + both source datasets).
2. **Filters swimrankings rows to Masters-only.** swimrankings publishes every result an athlete ever swam, including junior/age-group races from before they entered Masters category. The Masters category is marked by a "M" suffix on the time string, parsed into the `timeSuffix` field. Build script drops any swimrankings row where `timeSuffix !== "M"`. Lenex rows skip this filter — every Lenex meet is a Masters meet by definition. (Filter added 2026-05-16 after Kyra Lalonde's profile showed 360+ junior races from 2013–2021 alongside her 20 actual Masters swims.)
3. Joins Lenex swimmers → swimrankings athletes by **canonical-tokens(first+last) + birth-year** match. Canonical form sorts the normalized tokens, which handles the awkward cases ("LE SIEGE" / "Le Siege", "JAIMES Blas Eduardo" / "Blas Eduardo Jaimes", "Anne-Marie" / "anne marie").
4. For Lenex swimmers with a swimrankings match (28 of 33), all their Lenex swims are tagged with the swimrankings `athleteId`. Lenex-side fields like `meetName`, `place`, and `lenexSwimmerId` ride along on each row.
5. For Lenex swimmers with **no** swimrankings match (5 of 33 — see list below), the script synthesizes a stand-in athlete entry using the Lenex `swimmer_id` directly as the `athleteId`. The synth entry carries `source: "lenex"` so the data layer can distinguish if needed.
6. Dedupes overlapping swims on `(athleteId, date, distance, stroke, course)`. **Lenex wins on overlap** since it carries the richer fields.
7. Writes the combined payload to `brossard_inline_data.js` for the front-end.

Final tallies after merge: **72 athletes** (67 swimrankings + 5 synthesized), **3,182 swims** (2,821 swimrankings Masters-only + 361 Lenex). The Masters filter drops 1,293 junior rows; the Lenex merge replaces 273 swimrankings rows with their richer Lenex equivalents.

### Synthesized athletes — known gap to close

These five are confirmed-active Brossard members in the Lenex feed who do have swimrankings profiles; we just haven't harvested them yet. Next swimrankings pass should resolve them, after which they'll auto-merge with their Lenex data via the canonical-tokens join (no manual re-mapping needed; the build script is idempotent).

| Name | YOB | Lenex ID | Notes |
|---|---|---|---|
| Jennifer Coronel | 1977 | 127061734 | |
| Marie-Pier Daigle | 2006 | 129106326 | Junior; check for hyphen variants in swimrankings spelling |
| Ki-Hyang Lee | 1973 | 129157159 | |
| Patrick Janukavicius | 1986 | 140240990 | High Lenex ID — newest registration |
| Marie-Hélène Leduc | 1987 | 129053121 | |

### Workflow rule

**Any time swim data changes — new swimrankings harvest, new Lenex meet drop, manual edit — re-run `python3 build_inline_data.py` to regenerate `brossard_inline_data.js`.** The three HTML pages (`index.html`, `swimmer.html`, `brossard_chronology.html`) all load that single inline file and pick up new data on next page load. The build is idempotent and fast (~1 second).
