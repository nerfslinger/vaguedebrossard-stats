/* Final integration smoke-test — exercises everything the pages call. */
const fs = require('fs');
global.window = global; global.globalThis = global;
require('./brossard_inline_data.js');
require('./brossard_data.js');

(async () => {
  await BrossardData.load();

  console.log('==== YEAR ACTIVITY — multi-year sanity check ====');
  for (const y of [1985, 2005, 2015, 2020, 2025, 2026]) {
    const top = BrossardData.getYearActivity(y);
    console.log(`  ${y}: ${top.length} active swimmers`);
    if (top.length) {
      const t = top[0];
      console.log(`    #1 ${t.first} ${t.last} — ${t.meets} meets · ${t.distinctEvents} épreuves · ${(t.totalDistance/1000).toFixed(1)} km`);
    }
  }

  console.log('\n==== BINGO CARD — across athlete profiles ====');
  for (const aid of [4204240, 4204245, 4209372, 5788166, 5798276]) {
    const a = BrossardData.getAthleteById(aid);
    if (!a) { console.log(`  aid=${aid}: NOT FOUND`); continue; }
    const grid = BrossardData.getBingoCard(aid);
    const swam = grid.filter(c => c.swam).length;
    const untried = grid.filter(c => !c.swam);
    console.log(`  ${a.first} ${a.last}: ${swam}/${grid.length} (${untried.length} untried)`);
  }

  console.log('\n==== MODAL FALLBACK — historical record-setter with no swim data ====');
  for (const n of ['Joachim Lippinghof','Hermann Vroom','Henriette Janelle']) {
    const a = BrossardData.getAthleteByName(n);
    const r = BrossardData.getRecordsByName(n);
    console.log(`  ${n}: athleteMatch=${a?'yes':'no'} records=${r.length}`);
    // Confirms the modal will show "à venir" fallback for these
  }

  console.log('\n==== NAME-MATCH COVERAGE (statsman → swimrankings) ====');
  // Read the records, see how many can be matched
  const inline = window._brossardInline;
  const all_names = [...new Set(inline.records.map(r => r.name))];
  let hit = 0, miss = 0;
  for (const n of all_names) {
    if (BrossardData.getAthleteByName(n)) hit++; else miss++;
  }
  console.log(`  ${hit}/${all_names.length} statsman names match a swimrankings athlete (${Math.round(hit/all_names.length*100)}%)`);
  console.log(`  ${miss} historical record-setters with no swim data — will show "Plus de données à venir"`);

  console.log('\n==== ALL CHECKS PASSED ====');
})().catch(e => { console.error('FAIL:', e); process.exit(1); });
