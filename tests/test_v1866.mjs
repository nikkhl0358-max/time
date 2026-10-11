import assert from 'node:assert/strict';
import { dedupePublicLessonRows } from '../frontend/src/publicLessonDedupe.js';

const gsub=()=>[0];
const tokens=c=>(c.inst.streamParticipants||[]).map(p=>`${p.groupId}:${p.subgroup}`);
const base={groupId:'g1',subgroup:0,subjectId:'composition',teacherId:'volkova',format:'inperson',streamParticipants:[{groupId:'g1',subgroup:0}]};

// Legacy/current records can encode the SAME lesson type in different fields.
// Public UI renders both as «Пр», therefore duplicate identity must canonicalize them.
const a={inst:{...base,instId:'old',typeId:'pr'},a:{day:2,period:2,roomId:'204'}};
const b={inst:{...base,instId:'new',lessonTypeId:'pr'},a:{day:2,period:2,roomId:'203-1'}};
const rows=dedupePublicLessonRows([a,b],'group','g1',gsub,tokens);
assert.equal(rows.length,1,'legacy typeId + current lessonTypeId must collapse into one public lesson');
assert.equal(rows[0]._publicDuplicateRoomConflict,true,'different rooms must be surfaced as a conflict');
assert.deepEqual(rows[0]._publicDuplicateRoomIds,['203-1','204']);

// A genuinely different lesson type must remain a separate lesson.
const c={inst:{...base,instId:'lecture',lessonTypeId:'lec'},a:{day:2,period:2,roomId:'204'}};
const rows2=dedupePublicLessonRows([a,c],'group','g1',gsub,tokens);
assert.equal(rows2.length,2,'different lesson types must not be collapsed');
console.log('v1866: canonical lesson-type duplicate assertions passed');
