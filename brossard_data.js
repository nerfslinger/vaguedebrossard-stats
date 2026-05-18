/* ════════════════════════════════════════════════════════════════════════
 * brossard_data.js — shared data layer for the records + profile pages.
 *
 * Loads three JSON files asynchronously:
 *   - brossard_combined.json           (statsman records, ~1.4 MB)
 *   - brossard_swimrankings_athletes.json (67 athlete bios)
 *   - brossard_swimrankings_swims.json    (4,387 swims, ~1.4 MB)
 *
 * Exposes a `BrossardData` global with:
 *   - load()                            → Promise<void>, idempotent
 *   - getRecordsByName(name)            → Array<Record>
 *   - getAthleteByName(name)            → Athlete | null
 *   - getAthleteById(aid)               → Athlete | null
 *   - getSwimsByAthleteId(aid)          → Array<Swim>
 *   - getBingoCard(aid)                 → BingoCell[][]
 *   - getEventHistory(aid, distance, stroke, course) → Array<Swim>
 *   - getYearActivity(year)             → Array<{name, aid, meets, events, totalDistance, swims}>
 *   - nameToAid (Map)                   → exposed for debug
 *
 * Loading strategy: data is fetched lazily on first call to `load()` and
 * cached. Callers should `await BrossardData.load()` before any query.
 * ═══════════════════════════════════════════════════════════════════════ */
(function (root) {
  'use strict';

  // ── Constants ────────────────────────────────────────────────────────
  const STROKE_FR = { FREE: 'Libre', BACK: 'Dos', BREAST: 'Brasse', FLY: 'Papillon', MEDLEY: 'QNI' };
  const STROKE_FR_SHORT = { FREE: 'lib', BACK: 'dos', BREAST: 'bra', FLY: 'pap', MEDLEY: 'qni' };
  // statsman uses short codes; swimrankings uses Lenex codes. Bridge:
  const STATSMAN_TO_LENEX = { lib: 'FREE', dos: 'BACK', bra: 'BREAST', pap: 'FLY', qni: 'MEDLEY' };
  const LENEX_TO_STATSMAN = { FREE: 'lib', BACK: 'dos', BREAST: 'bra', FLY: 'pap', MEDLEY: 'qni' };

  // Masters event matrix: each cell is { distance, stroke, courses: ['LCM','SCM'] | ['SCM'] }
  // Built so the bingo card always shows the same shape per swimmer.
  const EVENT_MATRIX = (function buildMatrix () {
    const m = [];
    // 25m sprints — SCM only
    for (const s of ['FREE', 'BACK', 'BREAST', 'FLY']) m.push({ distance: 25, stroke: s, courses: ['SCM'] });
    // 50/100/200 of every stroke — both courses
    for (const d of [50, 100, 200]) for (const s of ['FREE', 'BACK', 'BREAST', 'FLY']) m.push({ distance: d, stroke: s, courses: ['LCM', 'SCM'] });
    // 400/800/1500 Free — both courses (1500 is rare LCM but exists)
    for (const d of [400, 800, 1500]) m.push({ distance: d, stroke: 'FREE', courses: ['LCM', 'SCM'] });
    // IM: 100 SCM-only, 200 + 400 both
    m.push({ distance: 100, stroke: 'MEDLEY', courses: ['SCM'] });
    m.push({ distance: 200, stroke: 'MEDLEY', courses: ['LCM', 'SCM'] });
    m.push({ distance: 400, stroke: 'MEDLEY', courses: ['LCM', 'SCM'] });
    return m;
  })();

  // ── Internal state ──────────────────────────────────────────────────
  let _loadPromise = null;
  const _athletes = [];            // Array<Athlete>
  const _athletesByAid = new Map();
  const _nameToAid = new Map();    // normalized "first last" or "last first" → aid
  const _swimsByAid = new Map();   // aid → Array<Swim>
  const _records = [];             // Array<Record>
  const _recordsByNormName = new Map();  // normalized name → Array<Record>

  // ── Name normalization ──────────────────────────────────────────────
  function stripAccents (s) {
    return s.normalize('NFD').replace(/[̀-ͯ]/g, '');
  }
  function norm (s) {
    return stripAccents(String(s || ''))
      .toLowerCase()
      .replace(/[-']/g, ' ')
      .replace(/[^a-z0-9 ]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }
  function splitStatsmanName (n) {
    // "Christian Berger" → { first: 'christian', last: 'berger' }
    // "M-Josee St-Charles" → { first: 'm josee', last: 'st charles' }
    const norm_full = norm(n);
    const tokens = norm_full.split(' ').filter(Boolean);
    if (tokens.length === 0) return { first: '', last: '' };
    if (tokens.length === 1) return { first: '', last: tokens[0] };
    // Heuristic: hyphenated last names (st-charles, de-repentigny) — last word always last
    // Treat last 1 token as last, the rest as first. But: "Anne-Cath Savaria" → "anne cath savaria" → last=savaria, first="anne cath"
    return { first: tokens.slice(0, -1).join(' '), last: tokens[tokens.length - 1] };
  }
  function buildCanonical (first, last) {
    // Order-independent canonical: sort tokens
    const all = (first + ' ' + last).split(' ').filter(Boolean).sort().join(' ');
    return all;
  }

  // ── Loaders ─────────────────────────────────────────────────────────
  function load () {
    if (_loadPromise) return _loadPromise;
    _loadPromise = (async () => {
      let recData, athData, swimData;
      // If brossard_inline_data.js has run before us, use the inline payload
      // (works offline / file:// origin). Otherwise fetch from siblings.
      if (root._brossardInline) {
        const p = root._brossardInline;
        recData = { individual: p.records, relays: p.relays };
        athData = { athletes: p.athletes };
        swimData = p.swims;
      } else {
        const [recRes, athRes, swimRes] = await Promise.all([
          fetch('brossard_combined.json'),
          fetch('brossard_swimrankings_athletes.json'),
          fetch('brossard_swimrankings_swims.json')
        ]);
        recData = await recRes.json();
        athData = await athRes.json();
        swimData = await swimRes.json();
      }

      // ── Index records ──
      for (const r of recData.individual) _records.push(r);
      for (const r of _records) {
        const key = norm(r.name);
        if (!_recordsByNormName.has(key)) _recordsByNormName.set(key, []);
        _recordsByNormName.get(key).push(r);
      }

      // ── Index athletes ──
      for (const a of athData.athletes) {
        _athletes.push(a);
        _athletesByAid.set(a.aid, a);
        // Add multiple matching keys: "first last", "last first", canonical
        const f = norm(a.first), l = norm(a.last);
        _nameToAid.set(f + ' ' + l, a.aid);
        _nameToAid.set(l + ' ' + f, a.aid);
        _nameToAid.set(buildCanonical(f, l), a.aid);
        // Also handle hyphenated last names — try just last
        _nameToAid.set(l, a.aid);
      }

      // ── Index swims ──
      for (const s of swimData) {
        if (!_swimsByAid.has(s.athleteId)) _swimsByAid.set(s.athleteId, []);
        _swimsByAid.get(s.athleteId).push(s);
      }
      // Sort each athlete's swims chronologically
      for (const arr of _swimsByAid.values()) {
        arr.sort((a, b) => (a.date || '').localeCompare(b.date || ''));
      }
    })();
    return _loadPromise;
  }

  // ── Query API ───────────────────────────────────────────────────────
  function getRecordsByName (name) {
    return _recordsByNormName.get(norm(name)) || [];
  }
  function getAthleteByName (name) {
    if (!name) return null;
    const n = norm(name);
    // Try direct match first
    let aid = _nameToAid.get(n);
    if (!aid) {
      // Try canonical (any order)
      const parts = n.split(' ').filter(Boolean);
      aid = _nameToAid.get(parts.sort().join(' '));
    }
    if (!aid) {
      // Try statsman split (first ... last)
      const { first, last } = splitStatsmanName(name);
      aid = _nameToAid.get(first + ' ' + last)
         || _nameToAid.get(last + ' ' + first)
         || _nameToAid.get(buildCanonical(first, last));
    }
    return aid ? _athletesByAid.get(aid) : null;
  }
  function getAthleteById (aid) {
    return _athletesByAid.get(Number(aid)) || null;
  }
  function getSwimsByAthleteId (aid) {
    return _swimsByAid.get(Number(aid)) || [];
  }

  /**
   * Returns one of:
   *   "active"          — athlete has harvested swims
   *   "awaiting_pass2"  — athlete is known (bio + events) but per-event harvest pending
   *   "unknown"         — no swimrankings athlete record at all
   * Used by UIs to show a "Données à venir" state.
   */
  function getAthleteStatus (aid) {
    if (!aid) return 'unknown';
    const a = _athletesByAid.get(Number(aid));
    if (!a) return 'unknown';
    const swims = _swimsByAid.get(Number(aid));
    if (swims && swims.length) return 'active';
    return 'awaiting_pass2';
  }

  /**
   * Build a bingo grid for the given athlete.
   * Returns an array of cells in EVENT_MATRIX order. Each cell:
   *   { distance, stroke, course, swam (bool), swimCount, pb (Swim|null), lastSwim (Swim|null) }
   * For events that don't apply (e.g. 100 IM in LCM), `course` is excluded.
   */
  function getBingoCard (aid) {
    const swims = getSwimsByAthleteId(aid);
    const grid = [];
    for (const row of EVENT_MATRIX) {
      for (const course of row.courses) {
        const matches = swims.filter(s =>
          s.distance === row.distance &&
          s.stroke === row.stroke &&
          s.course === course &&
          !s.isLap
        );
        let pb = null;
        if (matches.length) {
          pb = matches.reduce((best, s) => (best == null || (s.timeSec != null && s.timeSec < best.timeSec)) ? s : best, null);
        }
        const lastSwim = matches.length ? matches.reduce((latest, s) => (latest == null || (s.date || '') > (latest.date || '')) ? s : latest, null) : null;
        grid.push({
          distance: row.distance,
          stroke: row.stroke,
          stroke_fr: STROKE_FR[row.stroke],
          course,
          swam: matches.length > 0,
          swimCount: matches.length,
          pb,
          lastSwim
        });
      }
    }
    return grid;
  }

  function getEventHistory (aid, distance, stroke, course) {
    const swims = getSwimsByAthleteId(aid);
    return swims.filter(s =>
      s.distance === distance &&
      s.stroke === stroke &&
      s.course === course &&
      !s.isLap
    ).sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  }

  /**
   * For a given year, aggregate per-swimmer participation metrics.
   * Returns Array<{aid, name, swims, distinctEvents, meets, totalDistance}>
   * sorted by meets desc, then totalDistance desc.
   * "meets" is approximated as unique (date, city) pairs since the swim data
   * doesn't carry a meetId for swimrankings-sourced rows.
   */
  function getYearActivity (year) {
    const yearStr = String(year);
    const agg = new Map();
    for (const [aid, swims] of _swimsByAid) {
      const inYear = swims.filter(s => (s.date || '').startsWith(yearStr));
      if (!inYear.length) continue;
      const meetKeys = new Set(inYear.map(s => (s.date || '') + '|' + (s.city || '')));
      const eventKeys = new Set(inYear.map(s => s.distance + '|' + s.stroke + '|' + s.course));
      const totalDistance = inYear.reduce((sum, s) => sum + (Number(s.distance) || 0), 0);
      const bio = _athletesByAid.get(aid);
      agg.set(aid, {
        aid,
        name: bio ? bio.first + ' ' + bio.last : '?',
        last: bio ? bio.last : '?',
        first: bio ? bio.first : '?',
        swims: inYear.length,
        distinctEvents: eventKeys.size,
        meets: meetKeys.size,
        totalDistance
      });
    }
    return [...agg.values()].sort((a, b) =>
      b.meets - a.meets ||
      b.totalDistance - a.totalDistance ||
      b.swims - a.swims
    );
  }

  // ── Public ──────────────────────────────────────────────────────────
  root.BrossardData = {
    load,
    getRecordsByName,
    getAthleteByName,
    getAthleteById,
    getSwimsByAthleteId,
    getAthleteStatus,
    getBingoCard,
    getEventHistory,
    getYearActivity,
    // Read-only views
    get athletes () { return _athletes; },
    get nameToAid () { return _nameToAid; },
    // Constants
    EVENT_MATRIX,
    STROKE_FR,
    STROKE_FR_SHORT,
    STATSMAN_TO_LENEX,
    LENEX_TO_STATSMAN,
    // Helpers exposed for downstream pages
    norm,
    splitStatsmanName
  };
})(typeof window !== 'undefined' ? window : globalThis);
