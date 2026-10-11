import fs from 'node:fs';
const app=fs.readFileSync(new URL('../frontend/src/App.jsx',import.meta.url),'utf8');
const storage=fs.readFileSync(new URL('../frontend/src/storage.js',import.meta.url),'utf8');
if(app.includes('graphOccurrenceDedupe')) throw new Error('v1868 graphOccurrence helper must be rolled back');
if(app.includes('currentWeekFactualDuplicateIds')) throw new Error('v1868 duplicate graph status hook must be rolled back');
for (const needle of [
  'String(load?.typeId || "") !== String(inst?.typeId || "")',
  'modal.group-graph-quick-modal',
  'exportTeachersCompactExcel',
  'сборка {CLIENT_BUILD_ID}'
]) if(!app.includes(needle)) throw new Error(`missing invariant: ${needle}`);
if(!storage.includes('CLIENT_BUILD_ID = "1869"')) throw new Error('wrong build id');
console.log('v1869: rollback invariants passed');
