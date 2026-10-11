import assert from 'node:assert/strict';
import { duplicateFactualOccurrenceIds } from '../frontend/src/graphOccurrenceDedupe.js';

const base = {week:5,day:1,period:2,participants:['g1:0'],subjectId:'s1',teachers:['t1'],lessonTypeId:'pr',format:'remote',halfKey:'full'};
let ids = duplicateFactualOccurrenceIds([{id:'old',...base},{id:'new',...base}]);
assert.deepEqual([...ids].sort(), ['new','old'], 'same factual slot must be recognized as technical duplicate');

ids = duplicateFactualOccurrenceIds([{id:'a',...base},{id:'b',...base,period:3}]);
assert.equal(ids.size,0,'two distinct slots are two graph occurrences and must not be collapsed');

ids = duplicateFactualOccurrenceIds([{id:'a',...base},{id:'b',...base,participants:['g2:0']}]);
assert.equal(ids.size,0,'different participant groups must not be collapsed');

ids = duplicateFactualOccurrenceIds([{id:'single',...base},{id:'stream',...base,participants:['g1:0','g2:0']}]);
assert.deepEqual([...ids].sort(), ['single','stream'],'stale single-group card beside a stream must be recognized for their shared participant');

ids = duplicateFactualOccurrenceIds([{id:'a',...base},{id:'b',...base,lessonTypeId:'lek'}]);
assert.equal(ids.size,0,'different lesson types must remain distinct');
console.log('v1868: 5 factual-occurrence duplicate assertions passed');
