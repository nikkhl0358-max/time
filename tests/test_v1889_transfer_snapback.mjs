import fs from 'node:fs';
import assert from 'node:assert/strict';
import { resolveTransferParticipantLoadId } from '../frontend/src/loadTransfer.js';

const loads = [
  {id:'g40',groupId:'g40',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr'},
  {id:'g43',groupId:'g43',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr'},
];
// Legacy secondary participant inherited the primary group's loadId. It must be
// ignored and resolved back to the selected secondary group's own load.
assert.equal(
  resolveTransferParticipantLoadId(loads,{groupId:'g43',subgroup:1,loadId:'g40'},{groupId:'g40',subgroup:1,teacherId:'ogiy',subjectId:'s',typeId:'pr'},new Set(['g43'])),
  'g43'
);

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
assert.match(app, /for \(const gid of \(inst\?\.groupIds \|\| \[\]\)\)/);
assert.match(app, /groupIds:\[\.\.\.new Set\(remapped\.map/);
assert.match(app, /groupIds:\[\.\.\.new Set\(\[primaryLoad\?\.groupId \|\| src\.groupId/);

const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
assert.match(server, /transfer_scoped = bool/);
assert.match(server, /inst\.get\("manualStreamBreak"\)/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1889/);
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
assert.match(storage, /CLIENT_BUILD_ID = "1889"/);
console.log('v1889: transfer split snap-back regression assertions passed');
