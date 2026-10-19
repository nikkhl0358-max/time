import assert from 'node:assert/strict';
import {planExactBulkWeekCopy} from './bulkWeekSnapshot.mjs';
const base={groupId:'g1',subjectId:'s1',teacherId:'t1',typeId:'x1',format:'inperson',subgroup:0,requirementLayer:'1',weekPattern:'custom'};
function row(id,load,week){return {...base,instId:id,loadId:load,customWeeks:[week]}}
const old=row('old','restored_old',7), graph=row('graph','canonical_new',9);
const args={sourceWeek:7,targetWeek:9,currentWeek:8,instances:[old],assignment:{old:{day:0,period:1,roomId:'r1'}},locked:[],unplaced:[],targetGraphInstances:[graph],weeksFor:r=>r.customWeeks,requirementIds:r=>[r.loadId],requirementLayer:r=>r.requirementLayer,participantsFor:r=>[{groupId:r.groupId,loadId:r.loadId,subgroup:r.subgroup}]};
const result=planExactBulkWeekCopy(args);
assert.equal(result.counts.placed,1);assert.equal(result.counts.graphOnly,0);
assert.equal(result.instances[1].loadId,'canonical_new');
assert.equal(result.instances[1].weekCopyReconciledFromLoadId,'restored_old');
assert.equal(result.assignment[result.instances[1].instId].roomId,'r1');
const unsafe=planExactBulkWeekCopy({...args,targetGraphInstances:[graph,{...graph,instId:'graph2'}]});
assert.equal(unsafe.counts.placed,0);assert.equal(unsafe.counts.graphOnly,2);
console.log('week9 exact recovery: 2 tests passed');
