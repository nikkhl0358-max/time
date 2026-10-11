import assert from 'node:assert/strict';
import { expandTransferLoadIds, transferLoadsToTeacher } from '../frontend/src/loadTransfer.js';
const rows = [
  {id:'a',teacherId:'t1',groupId:'g1',subjectId:'s',typeId:'pr',subgroup:0,streamId:'st',streamGroupIds:['g2']},
  {id:'b',teacherId:'t1',groupId:'g2',subjectId:'s',typeId:'pr',subgroup:0,streamId:'st',streamGroupIds:['g1']},
  {id:'c',teacherId:'t1',groupId:'g3',subjectId:'x',typeId:'pr',subgroup:0,remoteSubgroupLoadIds:['d']},
  {id:'d',teacherId:'t1',groupId:'g4',subjectId:'x',typeId:'pr',subgroup:0,remoteSubgroupLoadIds:['c']},
  {id:'e',teacherId:'t1',groupId:'g5',subjectId:'z',typeId:'lek',subgroup:0},
];
assert.deepEqual(new Set(expandTransferLoadIds(rows,['a'])), new Set(['a','b']));
assert.deepEqual(new Set(expandTransferLoadIds(rows,['c'])), new Set(['c','d']));
let r=transferLoadsToTeacher(rows,['a'],'t2');
assert.equal(r.loads.find(x=>x.id==='a').teacherId,'t2');
assert.equal(r.loads.find(x=>x.id==='b').teacherId,'t2');
assert.equal(r.loads.find(x=>x.id==='e').teacherId,'t1');
r=transferLoadsToTeacher(rows,['e'],'t3');
assert.equal(r.loads.find(x=>x.id==='e').teacherId,'t3');
assert.equal(r.affectedIds.length,1);
console.log('v1873: load transfer assertions passed');
