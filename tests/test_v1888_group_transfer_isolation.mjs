import fs from 'node:fs';
import assert from 'node:assert/strict';
import { expandTransferLoadIds, resolveTransferParticipantLoadId } from '../frontend/src/loadTransfer.js';

// Regression: 2КДД43 p/g 1 and 3КДД40 p/g 1 may share the same technical
// stream/DO occurrence, teacher and subgroup number.  Ownership transfer of
// 2КДД43 must NOT transfer 3КДД40 unless its row was explicitly selected.
const loads = [
  {id:'g43-sg1',groupId:'g43',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'shared',coScheduleId:'co',remoteSubgroupLoadIds:['g40-sg1'],weeklyPairs:{5:1,6:1}},
  {id:'g40-sg1',groupId:'g40',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'shared',coScheduleId:'co',remoteSubgroupLoadIds:['g43-sg1'],weeklyPairs:{5:1,6:1}},
  // Exact technical duplicate of the same ownership row is allowed to follow.
  {id:'g43-sg1-dup',groupId:'g43',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'shared',weeklyPairs:{5:1,6:1}},
  // Same group but another subgroup must never follow.
  {id:'g43-sg2',groupId:'g43',subgroup:2,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'shared',weeklyPairs:{5:1,6:1}},
];
const affected = new Set(expandTransferLoadIds(loads,['g43-sg1']));
assert.ok(affected.has('g43-sg1'));
assert.ok(affected.has('g43-sg1-dup'), 'same ownership technical duplicate should follow');
assert.ok(!affected.has('g40-sg1'), 'another group in the same stream must not follow ownership transfer');
assert.ok(!affected.has('g43-sg2'), 'another subgroup must not follow ownership transfer');

// Legacy stream participant may have no loadId at all. It still must resolve to
// the selected group's own load rather than being ignored or mapped to the primary group.
const legacyResolved = resolveTransferParticipantLoadId(loads, {groupId:'g43',subgroup:1,loadId:''}, {groupId:'g40',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr'}, new Set(['g43-sg1']));
assert.equal(legacyResolved,'g43-sg1');
const legacyWrongSubgroup = resolveTransferParticipantLoadId(loads, {groupId:'g43',subgroup:0,loadId:''}, {groupId:'g40',subgroup:0,teacherId:'ogiy',subjectId:'s',typeId:'pr'}, new Set(['g43-sg1']));
assert.equal(legacyWrongSubgroup,'g43-sg1', 'explicitly selected row wins over stale subgroup inherited from legacy primary group');

const explicitlySelected = new Set(expandTransferLoadIds(loads,['g43-sg1','g40-sg1']));
assert.ok(explicitlySelected.has('g43-sg1'));
assert.ok(explicitlySelected.has('g40-sg1'), 'another group can transfer only when explicitly selected');

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
assert.match(app, /requestedOwnershipKeys/);
assert.match(app, /transferParticipantLoadId/);
assert.match(app, /const resolvedParts = transferParticipants\(src\)/);
assert.match(app, /Do NOT expand `affected` through schedule streamParticipants/);
assert.doesNotMatch(app, /const sourceOwnershipScopes = new Set/);
assert.match(app, /соседние группы из потока\/ДО-связки не меняют преподавателя/);

const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
assert.match(storage, /CLIENT_BUILD_ID = "1888"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1888/);
console.log('v1888: cross-group transfer isolation assertions passed');
