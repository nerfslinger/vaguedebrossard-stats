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

### 2. Records dashboard — `brossard_dashboard.html`
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
**Goal:** Complete and polish `brossard_dashboard.html` with real data.

Tasks:
1. Replace the manually embedded JS data array with the full parsed dataset from Workstream A
2. Add a "Swimmer profile" mode — click any name to see all records they hold
3. Add deep-linking / URL parameters so a filtered view can be bookmarked or shared (e.g. `?cat=F&stroke=lib&age=60-64`)
4. Accessibility pass — keyboard navigation, ARIA labels, sufficient colour contrast
5. Mobile layout refinement
6. Consider whether to add a "records broken this year" highlight section at the top

**Output:** Final `brossard_dashboard.html` (self-contained, hostable on statsman.ca)

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

> "We are building a modern club records site for Maîtres de Brossard, a Masters swimming club. The parser script `parse_brossard.py` and the primary dashboard `brossard_dashboard.html` are already built (see attached files). The dashboard currently uses a manually embedded subset of ~160 records. 
>
> **First task:** Run the parser to produce the full JSON dataset, validate it, then update the dashboard's data array with the complete records. Then begin Workstream B task 2: add a swimmer profile mode so clicking any name shows all records that swimmer holds."

---

## Files to attach when starting Cowork

- `parse_brossard.py` — the parser
- `brossard_dashboard.html` — the current dashboard

Both are in the outputs from this conversation.
