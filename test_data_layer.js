/* Headless test of brossard_data.js using Node.
 * Loads the inline data and exercises key queries.
 * Run: node test_data_layer.js
 */
const fs = require('fs');
const path = require('path');

// Stub browser globals
global.window = global;
global.globalThis = global;

// Load the inline data (sets window._brossardInline)
require('./brossard_inline_data.js');

// Load the data layer module
require('./brossard_data.js');

(async () => {
  await BrossardData.load();

  console.log('==== ATHLETES ====');
  console.log('Total athletes:', BrossardData.athletes.length);
  for (const aid of [4204240, 4204245, 4209372, 5481573]) {
    const a = BrossardData.getAthleteById(aid);
    console.log(`  aid=${aid} → ${a ? `${a.first} ${a.last} (${a.yob})` : 'NOT FOUND'}`);
  }

  console.log('\n==== NAME LOOKUP (statsman → swimrankings aid) ====');
  for (const n of ['Christian Berger','Gail Desjardins','Carlos Aviles','Michele Lemay','M-Josee St-Charles','Genev De Repentigny','Anne-Cath Savaria']) {
    const a = BrossardData.getAthleteByName(n);
    console.log(`  ${n.padEnd(30)} → ${a ? `aid=${a.aid} (${a.first} ${a.last})` : 'MISS'}`);
  }

  console.log('\n==== RECORDS BY NAME ====');
  for (const n of ['Christian Berger','Gail Desjardins','Carlos Aviles']) {
    const r = BrossardData.getRecordsByName(n);
    console.log(`  ${n.padEnd(20)} → ${r.length} records`);
  }

  console.log('\n==== BINGO CARD (Berger) ====');
  const grid = BrossardData.getBingoCard(4204240);
  console.log(`  Total cells: ${grid.length}`);
  console.log(`  Swam: ${grid.filter(c => c.swam).length}`);
  console.log(`  Untried: ${grid.filter(c => !c.swam).length}`);
  console.log('  Sample swam:');
  for (const c of grid.filter(c => c.swam).slice(0, 5)) {
    console.log(`    ${c.distance}m ${c.stroke} ${c.course}: PB ${c.pb.time} (${c.swimCount} swims, last ${c.lastSwim.date})`);
  }
  console.log('  Sample untried:');
  for (const c of grid.filter(c => !c.swam).slice(0, 5)) {
    console.log(`    ${c.distance}m ${c.stroke} ${c.course}: never`);
  }

  console.log('\n==== EVENT HISTORY (Berger 50 FREE SCM) ====');
  const hist = BrossardData.getEventHistory(4204240, 50, 'FREE', 'SCM');
  console.log(`  Total swims: ${hist.length}`);
  console.log(`  Date range: ${hist[0]?.date} to ${hist[hist.length-1]?.date}`);
  console.log(`  Times (best/worst): ${Math.min(...hist.map(s => s.timeSec))} / ${Math.max(...hist.map(s => s.timeSec))}`);

  console.log('\n==== YEAR ACTIVITY (2025) ====');
  const top = BrossardData.getYearActivity(2025).slice(0, 8);
  for (const t of top) {
    console.log(`  ${t.first.padEnd(16)} ${t.last.padEnd(18)} meets=${t.meets} events=${t.distinctEvents} dist=${t.totalDistance}m swims=${t.swims}`);
  }

  console.log('\n==== BINGO CARD (Carlos Aviles) ====');
  const cw = BrossardData.getAthleteByName('Carlos Aviles');
  if (cw) {
    const cg = BrossardData.getBingoCard(cw.aid);
    console.log(`  Swam: ${cg.filter(c => c.swam).length}/${cg.length}`);
    console.log('  Untried events:');
    for (const c of cg.filter(c => !c.swam).slice(0, 10)) {
      console.log(`    ${c.distance}m ${c.stroke} ${c.course}`);
    }
  }

  console.log('\n==== DATA PENDING — historical record-setter without swimrankings ====');
  for (const n of ['Sandra Larouche','Nadine Rolland','Joachim Lippinghof']) {
    const a = BrossardData.getAthleteByName(n);
    const r = BrossardData.getRecordsByName(n);
    console.log(`  ${n.padEnd(22)} athlete=${a?'yes':'no'} records=${r.length}`);
  }
})().catch(e => { console.error(e); process.exit(1); });
