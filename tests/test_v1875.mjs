import fs from 'node:fs';
import assert from 'node:assert/strict';

const src = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');

assert.match(src, /const draftLoadsRef = useRef\(data\.loads \|\| \[\]\)/, 'quick graph must keep a synchronous draft ref');
assert.match(src, /dirtyRef\.current = true;[\s\S]{0,120}setDirty\(true\)/, 'cell commit must synchronously mark dirty');
assert.match(src, /const exactDraft = draftLoadsRef\.current \|\| \[\];[\s\S]{0,120}setGraphLoads\(exactDraft\)/, 'save must persist the exact ref draft, not a stale render closure');
assert.match(src, /if \(!dirtyRef\.current\)/, 'save must use synchronous dirty ref');
assert.match(src, /missingSavedIds[\s\S]{0,350}throw new Error/, 'partial server acknowledgement must fail visibly');
assert.match(storage, /CLIENT_BUILD_ID = "187[5-8]"/, 'client build must be 1875–1878');

console.log('v1875: quick group graph save race and acknowledgement guards present');
