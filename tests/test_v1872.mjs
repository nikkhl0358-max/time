import fs from 'node:fs';
const src = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const required = [
  'Факт занятий',
  'buildActualTeachingRows',
  'exportActualTeachingReport',
  'Опубликованное расписание',
  'По замене, ак.ч.',
  'Практики, консультации, пересдачи и аттестации не включаются',
  'inst.halfPair ? 1 : 2',
  'cell?.subOverride?.cancelled',
];
for (const token of required) {
  if (!src.includes(token)) throw new Error(`v1872 missing report rule: ${token}`);
}
if (!src.includes('sub?.type!=="moved"') && !(src.includes('movedByDate.get(date)') && src.includes('indexMovedSubstitutionsByDate'))) throw new Error('v1872: moved substitutions must be date-filtered');
console.log('v1872: actual teaching report integration assertions passed');
