import fs from 'node:fs';
import assert from 'node:assert/strict';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
assert.match(app, /className="btn ghost sm load-transfer-row-btn"/);
assert.match(app, /onClick=\{\(\) => openTransferForLoad\(l\)\}/);
assert.match(app, /const \[transferDirectLoadId, setTransferDirectLoadId\]/);
assert.match(app, /const transferSeedIds = transferDirectLoadId/);
assert.match(app, /<b>Текущая запись<\/b>/);
console.log('v1874: row-local load transfer UI assertions passed');
