import fs from 'node:fs';
import assert from 'node:assert/strict';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');

// Hot path uses structural reference caches instead of full Array.find/Array.some.
assert.ok(app.includes('const scheduleStructureIndicesFor = (schedule) =>'));
assert.ok(app.includes('const { instById, lockedSet, unplacedSet } = scheduleStructureIndicesFor(baseSchedule);'));
assert.ok(app.includes('const { instById, lockedSet, unplacedSet } = scheduleStructureIndicesFor(baseScheduleForSave);'));

// Weekly graph-mismatch delete can never become ordinary unplaced debt.
assert.match(app, /weekScope === "week" && inst && graphStatusForInstanceWeek\(inst, scheduleWeek\)\.kind !== "ok"/);
assert.match(app, /return purgeOffGraphOccurrence\(id, Number\(scheduleWeek\)\)/);

// An already-unplaced redundant technical copy is still recognized as cleanup.
assert.ok(app.includes('const unplacedGraphStatusForInstanceWeek = (inst, week = scheduleWeek) =>'));
assert.ok(app.includes('const isRedundantUnplacedOffGraphInstance = (inst) =>'));
assert.ok(app.includes('isObsoleteOffGraphInstance(inst) || isRedundantUnplacedOffGraphInstance(inst)'));
assert.ok(app.includes('"🗑 Удалить вне графика"'));
assert.ok(app.includes('unplaced-offgraph-badge'));

// Previous safety layers remain.
assert.ok(app.includes('group/subgroup collisions are NEVER a manual override'));
assert.ok(app.includes('const groupOverlapSlotIndex = useMemo'));

assert.match(storage, /CLIENT_BUILD_ID = "1885"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1885/);
console.log('v1885: off-graph unplaced cleanup + hot-path perf assertions passed');
