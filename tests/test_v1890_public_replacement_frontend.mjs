import fs from 'node:fs';
const src = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
if (!src.includes('function canonicalPublicInstId(value)')) throw new Error('missing canonical public inst id helper');
if (!src.includes('publicInstMatchesSubstitution(inst, s)')) throw new Error('overlay does not use canonical substitution matching');
if (!src.includes('substitutionTargetInstIds.has(canonicalPublicInstId(inst.instId))')) throw new Error('teacher public filtering is not canonical-id aware');
if (!storage.includes('CLIENT_BUILD_ID = "1890"')) throw new Error('build id is not 1890');
console.log('v1890: public replacement frontend assertions passed');
