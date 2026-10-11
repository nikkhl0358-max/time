import fs from 'node:fs';
import assert from 'node:assert/strict';
import { expandTransferLoadIds, splitRemainingLoads } from '../frontend/src/loadTransfer.js';

// p/g 1 and p/g 2 may be linked for ДО / a shared technical stream, but ownership
// transfer of one subgroup must never pull the other subgroup with it.
const loads = [
  {id:'g43-sg1',groupId:'g43',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'mix',remoteSubgroupLoadIds:['g43-sg2','g45-sg1'],weeklyPairs:{4:1,5:1,6:1}},
  {id:'g43-sg2',groupId:'g43',subgroup:2,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'mix',remoteSubgroupLoadIds:['g43-sg1'],weeklyPairs:{4:1,5:1,6:1}},
  {id:'g45-sg1',groupId:'g45',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr',streamId:'mix',remoteSubgroupLoadIds:['g43-sg1'],weeklyPairs:{4:1,5:1,6:1}},
];
const affected = new Set(expandTransferLoadIds(loads,['g43-sg1']));
assert.ok(affected.has('g43-sg1'));
assert.ok(affected.has('g45-sg1'), 'same-subgroup stream participant may follow ownership');
assert.ok(!affected.has('g43-sg2'), 'p/g 2 must not be transferred with p/g 1');

let seq=0;
const split = splitRemainingLoads(loads,[...affected],'nerad',{ 'g43-sg1':{4:1}, 'g45-sg1':{4:1} },()=>`n${++seq}`,'2026-10-01');
const newG43 = split.loads.find((x)=>x.transferOriginLoadId==='g43-sg1');
const oldG43sg2 = split.loads.find((x)=>x.id==='g43-sg2');
assert.equal(newG43.teacherId,'nerad');
assert.equal(oldG43sg2.teacherId,'ogiy');
const historicalG43 = split.loads.find((x)=>x.id==='g43-sg1');
assert.equal(historicalG43.transferHistoricalBefore,'2026-10-01');
assert.equal(Number(oldG43sg2.subgroup),2);
assert.ok(!(newG43.remoteSubgroupLoadIds || []).includes('g43-sg2'), 'future p/g 1 must drop cross-subgroup ДО ownership link');

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
assert.match(app, /sourceOwnershipScopes/);
assert.match(app, /teacherId \|\| ""\)\}\|\$\{Number\(row\?\.subgroup \|\| 0\)\}/);
assert.match(app, /transferSubgroupDetached:true/);
assert.match(app, /TRANSFER_MOVE_SG/);
assert.match(app, /transferHistoricalBefore/);
assert.match(app, /normalizedParticipantSubgroupsOverlap\(nextDataContext, inst, other, gid\)/);
assert.match(app, /String\(load\?\.groupId \|\| ""\) === String\(sourceLoad\.groupId \|\| ""\)/);

const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
assert.match(storage, /CLIENT_BUILD_ID = "1887"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1887/);
console.log('v1887: subgroup transfer isolation assertions passed');
