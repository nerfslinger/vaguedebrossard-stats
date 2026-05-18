# Swimrankings Per-Athlete Harvest — Design Plan

Status: **approved 2026-05-14, ready to implement**
Last updated: 2026-05-14

---

## 1. Goal

Add a layer of *non-record-setting* swim data to the Brossard records site, sourced as a **one-time historical backfill** from swimrankings.net. The existing pipeline (`parse_brossard.py` against statsman.ca) already covers every record-setting swim back to 1980. This new dataset will enable swimmer-profile pages (full career times per event), year-in-review storytelling, and progression charts that go beyond record-breakers.

**Temporal scope:** Pre-February 2025 only. From February 2025 onward, swimming.ca Lenex downloads are the project's primary source of new swim data, so this harvest does not need to cover that window.

**Re-runnability:** Not needed. One bulk pass through swimrankings, output saved, done. `parse_swimrankings.py` (the prior live-window catch-up script) is **superseded** by the upcoming swimming.ca Lenex ingest and should be marked as such in the handoff.

## 2. Constraints (what we discovered in investigation)

Three access walls on swimrankings.net, each tested in this session:

1. **Cloudflare bot challenge.** Plain HTTP requests (`curl`, `urllib`, `requests`) return a "Just a moment..." JS interstitial. Bypassing requires a real browser. The `cf_clearance` cookie is HttpOnly so cannot be extracted and reused in standalone Python.

2. **Lenex / CSV / DSV / SDIF export buttons gated per-meet.** Each meet detail page has five export-format buttons, all returning *"This feature is not available for the selected meet or you reached your daily limit for meets."* Tested across three different meets in three provinces, including a Brossard intra-club meet, with a fully authenticated Manager-tier account. Wall is per-meet uploader permission, not account tier.

3. **MeetDetail page historical paywall.** Meets with `meetId` below ~600000-620000 (roughly pre-2020) return *"No access available. This page is not publicly available, or you have reached the limit for viewing different pages."* Federation-tier contract required. Affects roughly half of any career Brossard swimmer's meet history.

**Workaround that unlocks the project:** Per-event history pages at `index.php?page=athleteDetail&athleteId=X&styleId=Y` are not paywalled. They aggregate every historical swim a given athlete did at a given event/course, with time / FINA points / date / city. Confirmed working back to 2005 for Christian Berger. This is how we get full historical data without the meetDetail wall.

## 3. Data we can realistically harvest

Reachable via the per-athlete + per-event approach:

| Field                    | Source                          | Coverage |
|--------------------------|---------------------------------|----------|
| Athlete bio (name, YOB)  | BEST page                       | All known athletes |
| Gender                   | Inferred from meet swim-table   | All known athletes |
| Events the swimmer competes in | BEST page (rows)          | All known athletes |
| Personal best per event  | BEST page                       | All known athletes |
| Every swim, by event     | per-event history page          | Full history per swimmer per event |
| Date and city of each swim | per-event history page        | Full history |
| FINA points per swim     | per-event history page          | Full history |
| Meet attendance metadata | MEET-list page (Date/City/Club) | Full history |
| Place / Round / context  | meetDetail (filtered by clubId) | **Only post-2020 meets** |

Not reachable: pre-2020 meet-level place/round/round-context; bulk Lenex; splits within a race for any meet we don't own.

## 4. Roster discovery (aggressive, per 2026-05-14 decision)

The seed roster (17 athleteIds) comes from one recent Brossard intra-club meet (`meetId=658860`). We expand it through both available mechanisms:

1. **Recent meet crawl.** For meets newer than the ~620000 paywall cutoff that Brossard attended, fetch `meetDetail&meetId=X&clubId=73992` and harvest the athleteId of every Brossard swimmer listed. This catches active swimmers and recent newcomers. Budget: ~50-80 fetches.

2. **Statsman cross-reference.** Every name in statsman's `brossard_individual.json` and `brossard_relays.json` (record-setters back to 1980) is queried via swimrankings athlete search (`page=athleteSelect&search=...`) to retrieve a candidate athleteId. Match disambiguation: prefer hits whose listed club includes "Brossard"/"Vague" and whose birth year is consistent with the swimmer's record-setting age groups. Budget: ~100-200 fetches; many statsman names will resolve to the same already-known athleteId. This captures every historically significant Brossard swimmer in one pass.

Pre-2020 swimmers who never set a Brossard record and aren't visible in any post-2020 meet are effectively invisible. That edge case is accepted as a scope limit.

## 5. Harvest algorithm

```
Pass 0 (Roster expansion):
    fetch the ~50-80 accessible post-2020 Brossard meet pages
        -> collect athleteIds appearing in clubId=73992 sections
    for each statsman record-setter not yet in roster:
        query swimrankings athlete search
        disambiguate by club + birth-year heuristic
        add matched athleteId to roster

Pass 1 (Bio + event list):
    For each athleteId in expanded roster:
        fetch BEST page
        parse bio (last name, first name, birth year, current club, gender)
        parse event list -> list of (distance, stroke, course, styleId)

Pass 2 (Swim harvest):
    For each (athleteId, styleId) tuple from Pass 1:
        fetch per-event history page
        parse every swim row -> (time, FINA points, date, city)
        stamp with athleteId, event metadata
```

Pass 3 (post-2020 meetDetail enrichment) is **dropped** because the project will get full event-level data from swimming.ca Lenex for that window. No reason to scrape weaker data for meets we'll have rich Lenex for.

Throttle / batching: ~5 concurrent fetches per Promise.all batch, ~250ms inter-batch delay. Cache every raw HTML response to disk so reruns are free.

Estimated fetch budget:
- Pass 0: ~70 meet fetches + ~150 athlete-search fetches = ~220 fetches
- Pass 1: ~60 swimmers × 1 page = 60 fetches (roster grows from 17 → ~60 after expansion)
- Pass 2: ~60 swimmers × ~20 events per swimmer = ~1200 fetches
- **Total: ~1480 unique URLs**, all bounded by browser-side throttle.

Open question: whether per-event history pages have their own daily quota. Test in pilot phase with 2 swimmers (~50 fetches) before scaling.

## 6. Execution mechanism

**Confirmed: run in Chrome via this conversation's MCP session.** The user's already-authenticated tab cleared Cloudflare and holds the session cookies. Drive harvesting through JS scraper module installed on `window.brossardScraper`. Outputs returned to chat in batches, written to disk by Claude.

Since this is a one-time historical backfill (not an ongoing job), no Playwright install is needed — that saves a ~50MB dep on a project that's been stdlib-only. If we ever need to re-harvest (unlikely; the data is for the pre-Feb-2025 window which doesn't change), we re-run in a new chat session.

## 7. Output files

All in the project root:

| File | Contents |
|---|---|
| `brossard_swimrankings_athletes.json` | Per-athlete bio + event roster. One entry per athleteId. |
| `brossard_swimrankings_swims.json`    | Flat list of every swim, one entry per swim row. |
| `brossard_swimrankings_meets.json`    | Per-meet metadata for post-2020 meets we accessed (used for Pass 3 enrichment). |
| `brossard_swimrankings_meta.json`     | Run metadata: timestamp, athleteIds processed, fetch counts, any errors. |
| `_cache/swimrankings_pages/`          | Raw cached HTML per URL, keyed by a slug of the URL parameters. Git-ignored. |

### Athlete schema

```json
{
  "athleteId": 4204240,
  "lastName": "BERGER",
  "firstName": "Christian",
  "birthYear": 1944,
  "gender": "M",
  "currentClub": "La Vague de Brossard",
  "brossardMeetCount": 143,
  "events": [
    {"distance": 50,  "stroke": "FREE",   "course": "LCM", "styleId": 1},
    {"distance": 100, "stroke": "FREE",   "course": "LCM", "styleId": 2},
    {"distance": 50,  "stroke": "FREE",   "course": "SCM", "styleId": 6},
    ...
  ],
  "lastUpdatedUtc": "2026-05-14T..."
}
```

### Swim schema (unified — supports both swimrankings + future swimming.ca Lenex)

```json
{
  "athleteId":     4204240,
  "lastName":      "BERGER",
  "firstName":     "Christian",
  "distance":      50,
  "stroke":        "FREE",
  "stroke_fr":     "libre",
  "course":        "LCM",
  "time":          "35.05",
  "timeSuffix":    "M",
  "timeSec":       35.05,
  "finaPoints":    212,
  "date":          "2007-06-30",
  "city":          "Montreal (QC)",
  "meetId":        null,            // only set when source = "lenex"
  "meetName":      null,            // only set when source = "lenex"
  "place":         null,            // only set when source = "lenex"
  "round":         null,            // only set when source = "lenex"
  "splits":        null,            // only set when source = "lenex"
  "isPB":          false,           // computed post-harvest
  "isClubRecord":  false,           // computed by cross-referencing brossard_combined.json
  "source":        "swimrankings"   // or "lenex" or "statsman"
}
```

When the same swim is later found in swimming.ca Lenex data, the row is upgraded in place (its `source` flips to `"lenex"` and the previously-null columns are filled in). De-duplication key: `(athleteId, date, distance, stroke, course)`.

### Stroke codes

Use Lenex codes (matching `parse_brossard.py`): `FREE`, `BACK`, `BREAST`, `FLY`, `MEDLEY`. French labels in the `stroke_fr` field: `libre`, `dos`, `bra`, `pap`, `qni`.

## 8. Integration with the existing dashboard

`brossard_dashboard.html` currently reads `brossard_combined.json` (records only). After this harvest, the dashboard gains a new optional data layer:

- **Swimmer-profile mode** (Workstream B, task 2 of the handoff): clicking a name shows all records they hold PLUS all swims from `brossard_swimrankings_swims.json` filtered to that athleteId.
- **Year-in-review** (Workstream C, task 1): per-year activity stats derived from swim dates; "most active swimmer" leaderboards.
- **Event progression charts** (Workstream C, task 2): per-event time chart drawing from the swim list, with record-setting points highlighted.

The records UI itself doesn't change. The new data is additive.

## 9. Decisions (resolved 2026-05-14)

1. **Scope:** pre-Feb-2025 historical backfill only. Place/round/meetName accepted as null for swimrankings-sourced rows.
2. **Pilot:** yes, run on 2 swimmers (~50 fetches) first to characterize quota and parser edge cases.
3. **Roster:** aggressive — both post-2020 meet crawl AND statsman cross-reference.
4. **Execution:** Option A (chat session, no Playwright install).
5. **Stroke labels:** Lenex codes + French equivalents matching parse_brossard.py.
6. **No file overwrites:** all output filenames are new.

## 10. What changes after sign-off

Once this plan is approved, build order is:

1. Pilot harvest: 2 swimmers end-to-end. ~30-60 min including parser refinement.
2. Validate output schemas against approval.
3. Run roster expansion (Pass 1 + recent meet crawl).
4. Run full Pass 2 across the expanded roster.
5. Optional Pass 3 enrichment.
6. Save raw cache + JSON outputs.
7. Update `brossard_project_handoff.md` with the new data layer.

Pause-and-confirm checkpoints between each step.
