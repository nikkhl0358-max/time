import fs from 'node:fs';
import assert from 'node:assert/strict';
const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
for (const needle of [
  'const groupOverlapSlotIndex = useMemo',
  'groupOverlapInfoForSlot',
  'group-overlap-cell',
  'group-overlap-badge',
  'group-overlap-chip',
  'НАЛОЖЕНИЕ:',
  'semesterGroupSlotConflicts',
]) assert.ok(app.includes(needle), `missing ${needle}`);
assert.match(storage, /CLIENT_BUILD_ID = "1884"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1884/);
// v1883 hard guard must remain in place.
assert.ok(app.includes('group/subgroup collisions are NEVER a manual override'));
assert.ok(server.includes('even the fast manual path must pass the slot-local'));
console.log('v1884: existing group overlap highlight assertions passed');
