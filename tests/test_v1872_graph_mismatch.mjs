import assert from 'node:assert/strict';
import { buildUniquePlacedFactUnits, markSurplusPlacedOccurrences } from '../frontend/src/graphMismatchFacts.js';

const expected = new Map([['load|W6', 1]]);

// Screenshot case: one visible lesson plus a technical copy in the same slot.
let occ = [
  { occurrenceKey:'real::6', buckets:['load|W6'], unit:1, visibleFact:'1|2|full' },
  { occurrenceKey:'copy::6', buckets:['load|W6'], unit:1, visibleFact:'1|2|full' },
];
let { remainingUnits, actualUnits } = buildUniquePlacedFactUnits(occ);
assert.equal(actualUnits.get('load|W6'), 1, 'same visible slot must count once');
assert.equal(markSurplusPlacedOccurrences(occ, remainingUnits, expected).size, 0, 'same-slot technical copy must not create surplus warning');

// A real second lesson in another slot must still be detected.
occ = [
  { occurrenceKey:'a::6', buckets:['load|W6'], unit:1, visibleFact:'1|2|full' },
  { occurrenceKey:'b::6', buckets:['load|W6'], unit:1, visibleFact:'3|4|full' },
];
({ remainingUnits, actualUnits } = buildUniquePlacedFactUnits(occ));
assert.equal(actualUnits.get('load|W6'), 2);
assert.equal(markSurplusPlacedOccurrences(occ, remainingUnits, expected).size, 1, 'different visible slot above graph must remain surplus');

// Half-pairs in different halves together satisfy one graph pair.
occ = [
  { occurrenceKey:'h0::6', buckets:['load|W6'], unit:0.5, visibleFact:'1|2|half:0' },
  { occurrenceKey:'h1::6', buckets:['load|W6'], unit:0.5, visibleFact:'1|2|half:1' },
];
({ remainingUnits, actualUnits } = buildUniquePlacedFactUnits(occ));
assert.equal(actualUnits.get('load|W6'), 1);
assert.equal(markSurplusPlacedOccurrences(occ, remainingUnits, expected).size, 0);

// A duplicate of one half must not add another 0.5.
occ.push({ occurrenceKey:'h0copy::6', buckets:['load|W6'], unit:0.5, visibleFact:'1|2|half:0' });
({ remainingUnits, actualUnits } = buildUniquePlacedFactUnits(occ));
assert.equal(actualUnits.get('load|W6'), 1);
assert.equal(markSurplusPlacedOccurrences(occ, remainingUnits, expected).size, 0);

console.log('v1872: graph mismatch visible-fact assertions passed');
