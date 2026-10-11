import assert from 'node:assert/strict';
import { splitRemainingLoads } from '../frontend/src/loadTransfer.js';

const loads = [
  {id:'a',teacherId:'old',groupId:'g1',subjectId:'s',typeId:'lec',weekPattern:'perWeek',weeklyPairs:{1:1,2:2,3:1},streamId:'st',remoteSubgroupLoadIds:['b']},
  {id:'b',teacherId:'old',groupId:'g2',subjectId:'s',typeId:'lec',weekPattern:'perWeek',weeklyPairs:{1:1,2:2,3:1},streamId:'st',remoteSubgroupLoadIds:['a']},
  {id:'x',teacherId:'other',groupId:'g3',subjectId:'z',typeId:'pr',weekPattern:'perWeek',weeklyPairs:{1:1}},
];
let seq=0;
const r = splitRemainingLoads(loads,['a','b'],'new',{
  a:{1:1,2:1},
  b:{1:1,2:1},
},()=>`n${++seq}`);
assert.equal(r.transferredPairs,4); // each row transfers week2=1 + week3=1
assert.equal(r.historicalPairs,4); // each row keeps week1=1 + week2=1
assert.equal(r.mapping.a,'n1');
assert.ok(r.mapping.b);
const oldA=r.loads.find(x=>x.id==='a');
const newA=r.loads.find(x=>x.id===r.mapping.a);
assert.deepEqual(oldA.weeklyPairs,{'1':1,'2':1});
assert.deepEqual(newA.weeklyPairs,{'2':1,'3':1});
assert.equal(oldA.teacherId,'old');
assert.equal(newA.teacherId,'new');
assert.notEqual(oldA.streamId,newA.streamId);
assert.ok(newA.remoteSubgroupLoadIds.includes(r.mapping.b));
assert.ok(!newA.remoteSubgroupLoadIds.includes('b'));
assert.equal(r.loads.find(x=>x.id==='x').teacherId,'other');

// Nothing conducted: do not create a historical duplicate, simply move row ownership.
seq=0;
const m=splitRemainingLoads([loads[0]],['a'],'new',{},()=>`q${++seq}`);
assert.equal(m.loads.length,1);
assert.equal(m.loads[0].id,'a');
assert.equal(m.loads[0].teacherId,'new');
assert.deepEqual(m.loads[0].weeklyPairs,{'1':1,'2':2,'3':1});

// Everything retained: nothing is transferred.
const n=splitRemainingLoads([loads[0]],['a'],'new',{a:{1:1,2:2,3:1}},()=>`z${++seq}`);
assert.equal(n.transferredPairs,0);
assert.equal(n.loads.length,1);
assert.equal(n.loads[0].teacherId,'old');

console.log('v1875: remaining-load split assertions passed');
