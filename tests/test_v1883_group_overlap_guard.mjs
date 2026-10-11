import fs from 'node:fs';
import assert from 'node:assert/strict';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');

assert.match(app, /const hardParticipantConflict = rejectHardParticipantOverlap\(inst, day, period, sched\.assignment, placeInstances\)/);
assert.doesNotMatch(app, /const hardParticipantConflict = forceManualOverlap \? null : rejectHardParticipantOverlap/);

const manualFlagPos = server.indexOf('manual_force_overlap = bool((a or {}).get("manualForceOverlap"))');
const groupCheckPos = server.indexOf('if group_conflict:', manualFlagPos);
const manualContinuePos = server.indexOf('if manual_force_overlap:', groupCheckPos);
assert.ok(manualFlagPos >= 0, 'server must compute manual_force_overlap');
assert.ok(groupCheckPos > manualFlagPos, 'group conflict must be evaluated after reading manual flag');
assert.ok(manualContinuePos > groupCheckPos, 'manual force may only skip after group conflict validation');

assert.doesNotMatch(server, /overlap_pairs = \[\] if fast_manual_force else _schedule_group_overlap_pairs/);
assert.match(server, /overlap_pairs = _schedule_group_overlap_pairs\(project, next_schedule, touched\)/);

// v1882 source-week copy protection remains present.
assert.match(app, /reason:"week-copy-exact-snapshot-v1882"/);
assert.match(server, /future_only_trim = \(/);

assert.match(storage, /CLIENT_BUILD_ID = "1883"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1883/);

console.log('v1883: group/subgroup overlap guard assertions passed');
