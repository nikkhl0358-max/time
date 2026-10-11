import fs from 'fs';
import assert from 'assert';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const py = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');

assert.match(storage, /CLIENT_BUILD_ID\s*=\s*"1892"/);
assert.match(py, /PUBLIC_SHARD_SCHEMA_VERSION\s*=\s*1892/);
assert.match(py, /CURRENT_SCHEDULE_CLIENT_BUILD\s*=\s*1892/);
assert.match(app, /\.map\(\(inst\) => enrichInstanceStreamGroups\(current, inst\)\)/);
assert.match(app, /publicationAffectedGroupIds = \[\.\.\.new Set\(/);
assert.match(app, /participantLoadIds\.add\(String\(part\.loadId\)\)/);
assert.match(app, /projectScheduleOccurrences\(currentSchedule, target, true\)/);
assert.match(app, /loads:mergePublishedReferencedLoads\(previous\.loads/);
assert.match(app, /groupIds: scope === "group" \? publicationAffectedGroupIds/);
assert.match(py, /def _repair_public_implicit_stream_membership\(source, working\):/);
assert.match(py, /source = _repair_public_implicit_stream_membership\(source, project\)/);
console.log('v1892: group publication materializes stream participants');
