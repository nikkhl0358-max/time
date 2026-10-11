import assert from 'node:assert/strict';
import { remapTeacherTransferSubstitutions, transferTeacherRemainder } from '../frontend/src/teacherLoadTransfer.js';

const loads=[{id:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'LEK',subgroup:0,weekPattern:'perWeek',weeklyPairs:{1:1,2:1,3:1,4:1,5:1,6:1}}];
const schedule={instances:[{instId:'I1',loadId:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'LEK',weekPattern:'custom',customWeeks:[1,2,3,4,5,6]}],assignment:{I1:{day:0,period:1,roomId:'R'}},locked:[],unplaced:[]};
let n=0;
const split=transferTeacherRemainder({loads,schedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:6,makeId:()=>`n${++n}`});
const newId=split.futureIdMap.get('I1');
assert.ok(newId && newId !== 'I1');
const weekByDate={'2026-09-14':3,'2026-09-21':4,'2026-09-28':5};
const subs=[
  {id:'A',date:'2026-09-14',instId:'I1',type:'cancel'},
  {id:'B',date:'2026-09-21',instId:'I1',type:'cancel'},
  {id:'C',date:'2026-09-28',instId:'OTHER',targetInstId:'I1',type:'join_subgroup'},
];
const remapped=remapTeacherTransferSubstitutions(subs,split.futureIdMap,split.meta.movedWeeks,(d)=>weekByDate[d]);
assert.equal(remapped[0].instId,'I1','past cancellation must stay with former teacher');
assert.equal(remapped[1].instId,newId,'future cancellation must follow replacement teacher instance');
assert.equal(remapped[1].transferredFromInstId,'I1');
assert.equal(remapped[2].instId,'OTHER');
assert.equal(remapped[2].targetInstId,newId,'future join target must follow split instance');
assert.equal(subs[1].instId,'I1','source substitutions must not be mutated');
console.log('v1871: teacher-transfer substitution remap assertions passed');
