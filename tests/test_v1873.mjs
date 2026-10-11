import fs from 'node:fs';
import assert from 'node:assert/strict';
import { transferTeacherRemainder } from '../frontend/src/teacherLoadTransfer.js';

// Teacher transfer must split the authoritative graph itself; the new teacher
// must not require a manual graph rebuild.
const loads=[{id:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'LEK',weekPattern:'perWeek',weeklyPairs:{1:1,2:0.5,3:1,4:1,5:0.5}}];
const schedule={instances:[{instId:'I1',loadId:'L1',groupId:'G1',teacherId:'T1',subjectId:'S',typeId:'LEK',weekPattern:'custom',customWeeks:[1,2,3,4,5]}],assignment:{I1:{day:1,period:2,roomId:'R1'}},locked:[],unplaced:[]};
let seq=0;
const split=transferTeacherRemainder({loads,schedule,loadId:'L1',newTeacherId:'T2',startWeek:4,totalWeeks:5,makeId:()=>`x${++seq}`});
assert.deepEqual(split.oldLoad.weeklyPairs,{1:1,2:0.5,3:1});
assert.deepEqual(split.newLoad.weeklyPairs,{4:1,5:0.5});
assert.equal(split.newLoad.teacherId,'T2');
const newInstId=split.futureIdMap.get('I1');
assert.equal(split.schedule.assignment[newInstId].day,1);
assert.equal(split.schedule.assignment[newInstId].period,2);

const src=fs.readFileSync(new URL('../frontend/src/App.jsx',import.meta.url),'utf8');
for(const token of [
  'buildActualSubstitutionRows',
  'const substitutionRows=buildActualSubstitutionRows()',
  'XLSX.utils.book_append_sheet(wb,wsSubs,"Замены")',
  'Отмена + выдача задания',
  'Отмена · перенос не назначен',
  'Замена преподавателя · другая подгруппа',
  'Изменение: ${bits.join(", ")}',
  'Будущие изменения помечаются как запланированные',
]) assert.ok(src.includes(token),`missing v1873 report token: ${token}`);
console.log('v1873: transfer graph split and complete substitutions sheet assertions passed');
