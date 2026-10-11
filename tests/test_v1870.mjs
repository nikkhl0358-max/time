import assert from 'node:assert/strict';
import { normalizedLoadWeekPairs, loadHoursFromWeekPairs, transferTeacherRemainder } from '../frontend/src/teacherLoadTransfer.js';

const load = {id:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'PR',subgroup:0,roomType:'any',weekPattern:'perWeek',weeklyPairs:{1:1,2:1,3:0.5,4:1,5:1,6:0.5},streamGroupIds:[],remoteSubgroupLoadIds:[]};
assert.deepEqual(normalizedLoadWeekPairs(load,6), {1:1,2:1,3:0.5,4:1,5:1,6:0.5});
assert.equal(loadHoursFromWeekPairs(load.weeklyPairs),10);

let seq=0; const makeId=()=>`x${++seq}`;
const schedule={
  instances:[{instId:'I1',loadId:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'PR',subgroup:0,format:'inperson',weekPattern:'custom',customWeeks:[1,2,3,4,5,6],streamParticipants:[],streamGroupIds:[]}],
  assignment:{I1:{day:1,period:2,roomId:'R1'}},locked:['I1'],unplaced:[]
};
const r=transferTeacherRemainder({loads:[load],schedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:6,makeId});
assert.equal(r.loads.length,2);
const old=r.loads.find(x=>x.id==='L1'), neu=r.loads.find(x=>x.id!=='L1');
assert.deepEqual(old.weeklyPairs,{1:1,2:1,3:0.5});
assert.deepEqual(neu.weeklyPairs,{4:1,5:1,6:0.5});
assert.equal(r.meta.oldHours,5);
assert.equal(r.meta.newHours,5);
assert.equal(r.meta.oldHours+r.meta.newHours,10);
const oldInst=r.schedule.instances.find(x=>x.instId==='I1');
const newInst=r.schedule.instances.find(x=>x.loadId===neu.id);
assert.deepEqual(oldInst.customWeeks,[1,2,3]);
assert.deepEqual(newInst.customWeeks,[4,5,6]);
assert.equal(newInst.teacherId,'T2');
assert.equal(r.schedule.assignment[newInst.instId].roomId,'R1');
assert.ok(r.schedule.locked.includes(newInst.instId));

// Stream: G1 leaves the old teacher's stream from week 4. G2 stays with T1;
// the new T2 occurrence keeps day/period but requires a separate room.
seq=0;
const l2={...load,id:'L2',groupId:'G2',weeklyPairs:{1:1,2:1,3:1,4:1,5:1,6:1}};
const streamSchedule={
  instances:[{instId:'ST',loadId:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'PR',subgroup:0,format:'inperson',weekPattern:'custom',customWeeks:[1,2,3,4,5,6],streamParticipants:[{groupId:'G2',subgroup:0,loadId:'L2'}],streamGroupIds:['G2'],streamId:'STR'}],
  assignment:{ST:{day:2,period:3,roomId:'R9'}},locked:[],unplaced:[]
};
const sr=transferTeacherRemainder({loads:[{...load,weeklyPairs:{1:1,2:1,3:1,4:1,5:1,6:1}},l2],schedule:streamSchedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:6,makeId});
assert.equal(sr.meta.streamSplits,1);
const snewLoad=sr.loads.find(x=>x.transferredFromLoadId==='L1');
const past=sr.schedule.instances.find(x=>x.instId==='ST');
const residual=sr.schedule.instances.find(x=>x.teacherTransferResidualStream);
const replacement=sr.schedule.instances.find(x=>x.loadId===snewLoad.id);
assert.deepEqual(past.customWeeks,[1,2,3]);
assert.deepEqual(residual.customWeeks,[4,5,6]);
assert.equal(residual.groupId,'G2');
assert.equal(residual.teacherId,'T1');
assert.deepEqual(replacement.customWeeks,[4,5,6]);
assert.equal(replacement.groupId,'G1');
assert.equal(replacement.teacherId,'T2');
assert.equal(sr.schedule.assignment[replacement.instId].period,3);
assert.equal(sr.schedule.assignment[replacement.instId].roomId,null);
assert.equal(sr.schedule.assignment[replacement.instId].roomPending,true);
assert.equal(sr.schedule.assignment[residual.instId].roomId,'R9');

assert.throws(()=>transferTeacherRemainder({loads:[load],schedule:null,loadId:'L1',newTeacherId:'T2',startWeek:7,totalWeeks:7,makeId}),/нет оставшихся пар/);
console.log('v1870: teacher load transfer assertions passed');
