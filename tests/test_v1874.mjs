import assert from 'node:assert/strict';
import { transferTeacherRemainder } from '../frontend/src/teacherLoadTransfer.js';

const baseLoad={id:'L1',groupId:'G1',teacherId:'T1',subjectId:'S1',typeId:'PR',subgroup:0,weekPattern:'perWeek',weeklyPairs:{1:1,2:1,3:1,4:1,5:1,6:1}};
const schedule={instances:[{instId:'I1',loadId:'L1',groupId:'G1',teacherId:'T1',subjectId:'S1',typeId:'PR',subgroup:0,format:'inperson',weekPattern:'custom',customWeeks:[1,2,3,4,5,6]}],assignment:{I1:{day:1,period:2,roomId:'R1'}},locked:[],unplaced:[]};
let n=0;
const r=transferTeacherRemainder({loads:[baseLoad],schedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:6,makeId:()=>`n${++n}`});
assert.equal(r.loads.length,2,'load is intentionally split into old+new rows');
assert.deepEqual(r.oldLoad.weeklyPairs,{1:1,2:1,3:1});
assert.deepEqual(r.newLoad.weeklyPairs,{4:1,5:1,6:1});
const oldInst=r.schedule.instances.find(x=>x.instId==='I1');
const newInst=r.schedule.instances.find(x=>x.loadId===r.newLoad.id);
assert.deepEqual(oldInst.customWeeks,[1,2,3]);
assert.deepEqual(newInst.customWeeks,[4,5,6]);
assert.equal(new Set([...oldInst.customWeeks,...newInst.customWeeks]).size,6);
assert.equal(oldInst.customWeeks.some(w=>newInst.customWeeks.includes(w)),false,'old and new teacher cannot own same week');

const targetExisting={id:'L2',groupId:'G1',teacherId:'T2',subjectId:'S1',typeId:'PR',subgroup:0,weekPattern:'perWeek',weeklyPairs:{5:1}};
assert.throws(()=>transferTeacherRemainder({loads:[baseLoad,targetExisting],schedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:6,makeId:()=>`z${++n}`}),/пересекающаяся строка/);

const duplicateSchedule={...schedule,instances:[...schedule.instances,{...schedule.instances[0],instId:'I2'}],assignment:{I1:{day:1,period:2,roomId:'R1'},I2:{day:1,period:2,roomId:'R2'}}};
assert.throws(()=>transferTeacherRemainder({loads:[baseLoad],schedule:duplicateSchedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:6,makeId:()=>`d${++n}`}),/остаточная двойная запись/);
console.log('v1874: teacher-transfer duplicate prevention assertions passed');
