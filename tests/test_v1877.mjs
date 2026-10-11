import assert from 'node:assert/strict';
import { normalizeExcludedGroupIds, substitutionExcludesGroup, trimStreamOccurrenceGroups } from '../frontend/src/streamPartialCancellation.js';

const inst={instId:'S1',groupId:'g1',subgroup:0,loadId:'l1',teacherId:'t1',subjectId:'subj',typeId:'lec',streamParticipants:[{groupId:'g3',subgroup:0,loadId:'l3'}],streamGroupIds:['g3']};
const parts=[{groupId:'g1',subgroup:0,loadId:'l1'},{groupId:'g3',subgroup:0,loadId:'l3'}];
assert.deepEqual(normalizeExcludedGroupIds(['g3','g3','']),['g3']);
assert.equal(substitutionExcludesGroup({type:'stream_group_cancel',excludedGroupIds:['g3']},'g3'),true);
assert.equal(substitutionExcludesGroup({type:'stream_group_cancel',excludedGroupIds:['g3']},'g1'),false);
const keepG1=trimStreamOccurrenceGroups(inst,parts,['g3']);
assert.equal(keepG1.groupId,'g1');
assert.deepEqual(keepG1.streamGroupIds,[]);
assert.equal(keepG1.manualStreamBreak,true);
assert.equal(keepG1.dateSpecificStreamSplit,true);
const keepG3=trimStreamOccurrenceGroups(inst,parts,['g1']);
assert.equal(keepG3.groupId,'g3');
assert.equal(keepG3.loadId,'l3');
assert.equal(trimStreamOccurrenceGroups(inst,parts,['g1','g3']),null);
console.log('v1877 partial stream cancellation assertions passed');

import fs from 'node:fs';
const appSrc=fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url),'utf8');
const storageSrc=fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url),'utf8');
assert.match(appSrc,/stream_group_cancel/);
assert.match(appSrc,/Отменить у части потока/);
assert.match(appSrc,/partialStreamCellForGroup/);
assert.match(appSrc,/belongsToGroup\(cell\.inst, groupId\)/, 'date conflict check must free the cancelled group slot');
assert.match(storageSrc,/CLIENT_BUILD_ID = "187[78]"/);
