import fs from 'node:fs';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { buildScheduleDateLookup } from '../frontend/src/scheduleHotpath.js';

const addDays = (s, n) => {
  const d = new Date(s + 'T00:00:00'); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
};
const mondayOf = (s) => addDays(s, -(new Date(s + 'T00:00:00').getDay() + 6) % 7);
const config = {semesterStart:'2026-09-01', semesterEnd:'2026-12-31', holidays:['2026-09-11'], calendarExceptions:[{date:'2026-09-16',noClasses:true},{date:'2026-09-17',noClasses:true}]};
const exceptions = [{date:'2026-09-16',noClasses:false},{date:'2026-09-18',noClasses:true}];
const practices = Array.from({length:330}, (_,i)=>({groupId:`G${i%80}`,dateStart:addDays('2026-09-01',i%48),dateEnd:addDays('2026-09-01',i%48+2)}));
const index = buildScheduleDateLookup(config,exceptions,practices,18,addDays,mondayOf);
const noClassOriginal = (date) => config.holidays.includes(date) || !![...exceptions,...config.calendarExceptions].find((e)=>e.date===date)?.noClasses;
const practiceOriginal = (gid,date) => practices.find((p)=>p.groupId===gid && p.dateStart<=date && date<=p.dateEnd) || null;
assert.equal(index.date(5,0),'2026-09-28');
assert.equal(index.isNoClass('2026-09-16'),false,'first matching calendar exception wins');
assert.equal(index.isNoClass('2026-09-17'),true);
assert.equal(index.isNoClass('2026-09-11'),true);
for(let w=1;w<=18;w++) for(let day=0;day<7;day++) {
  const date=addDays(addDays(mondayOf(config.semesterStart),(w-1)*7),day);
  assert.equal(index.date(w,day),date);
  assert.equal(index.isNoClass(date),noClassOriginal(date));
  for(let g=0;g<80;g++) assert.equal(index.isGroupOnPractice(`G${g}`,date),!!practiceOriginal(`G${g}`,date));
}

// Execute the actual new quick graph cell-edit function from App.jsx in a pure
// sandbox of injected closures, and compare against the previous algorithm.
const src=fs.readFileSync(new URL('../frontend/src/App.jsx',import.meta.url),'utf8');
const end=src.indexOf('  const saveDraft = async (closeAfter = false) => {');
const start=src.lastIndexOf('  const setCell = (loadId, week, value) => {',end);
assert.ok(start>0 && end>start,'quick graph cell function must exist');
const setCellSource=src.slice(start,end);
const streamKey=(row)=>row?.streamId ? [String(row.streamId),String(row.subjectId||''),String(row.teacherId||''),String(row.typeId||''),Number(row.subgroup||0)].join('|') : '';
const totalWeeks=8;
const materializeWeeklyPairs=(row)=>row.weekPattern==='perWeek' ? {...row.weeklyPairs} : Object.fromEntries(Array.from({length:totalWeeks},(_,i)=>[i+1,Number(row.pairsPerWeek||0)]));
const oldSetCell=(deps)=>(loadId,week,value)=>{
  const {draftLoadsRef,dirtyRef,setDirty,setSaveMsg,setDraftLoads,typeById,isFullPracticeWeek}=deps;
  const all=draftLoadsRef.current||[];
  const target=all.find((row)=>String(row.id)===String(loadId));
  if(!target||target.graphLocked||isFullPracticeWeek(week))return;
  const n=Number(value);const key=streamKey(target);
  const linked=key?all.filter((row)=>streamKey(row)===key):[target];
  if(linked.some((row)=>row.graphLocked))return;
  const linkedIds=new Set(linked.map((row)=>String(row.id)));
  const isLectureStream=!!key&&linked.length>1&&String(typeById.get(String(target.typeId))?.name||'').includes('Лек');
  let next;
  if(isLectureStream){next=all.map((row)=>{if(!linkedIds.has(String(row.id)))return row;const wp=materializeWeeklyPairs(row);if(value===''||!Number.isFinite(n)||n<=0)delete wp[week];else wp[week]=Math.max(0,Math.round(n*2)/2);return {...row,weekPattern:'perWeek',customWeeks:[],weeklyPairs:wp,graphMode:'manual'};});}
  else{const wp=materializeWeeklyPairs(target);if(value===''||!Number.isFinite(n)||n<=0)delete wp[week];else wp[week]=Math.max(0,Math.round(n*2)/2);next=all.map((row)=>linkedIds.has(String(row.id))?{...row,weekPattern:'perWeek',customWeeks:[],weeklyPairs:{...wp},graphMode:'manual'}:row);}
  draftLoadsRef.current=next;dirtyRef.current=true;setDirty(true);setSaveMsg('');setDraftLoads(next);
};
function harness(rows,optimized){
  const draftLoadsRef={current:structuredClone(rows)};
  const dirtyRef={current:false};let dirty=false,msg='untouched',rendered;
  const deps={draftLoadsRef,dirtyRef,setDirty:(v)=>dirty=v,setSaveMsg:(v)=>msg=v,setDraftLoads:(v)=>rendered=v,
    isFullPracticeWeek:(week)=>week===7,streamKey,materializeWeeklyPairs,
    typeById:new Map([['LEK',{name:'Лекция'}],['PR',{name:'Практическая'}]]),
    isLectureLessonTypeName:(name)=>name.includes('Лек'),
    quickLoadIndexById:new Map(rows.map((r,i)=>[String(r.id),i])),
    quickStreamIndexes:new Map(),
  };
  rows.forEach((row,i)=>{const key=streamKey(row);if(!key)return;if(!deps.quickStreamIndexes.has(key))deps.quickStreamIndexes.set(key,[]);deps.quickStreamIndexes.get(key).push(i);});
  const names=Object.keys(deps);
  const fn=optimized?new Function(...names,`${setCellSource}\nreturn setCell;`)(...Object.values(deps)):oldSetCell(deps);
  return {fn, state:()=>({rows:draftLoadsRef.current,dirty:dirtyRef.current,msg,rendered:!!rendered})};
}
const base=Array.from({length:200},(_,i)=>({id:`L${i}`,groupId:`G${i%10}`,teacherId:'T1',subjectId:'S1',typeId:i%3===0?'LEK':'PR',subgroup:0,streamId:i%3===0?'LECTURE':i%5===0?'PRACTICE':'',weekPattern:'perWeek',weeklyPairs:{1:1,2:0.5,3:1},graphLocked:false}));
for(const [id,week,value,lock] of [['L0',2,1.5,null],['L5',3,2,null],['L2',3,'',null],['L1',7,3,null],['L0',2,1.5,'L3'],['L0',2,1.5,'L0']]){
  const rows=structuredClone(base);if(lock)rows.find(x=>x.id===lock).graphLocked=true;
  const a=harness(rows,false),b=harness(rows,true);a.fn(id,week,value);b.fn(id,week,value);
  assert.deepEqual(b.state(),a.state(),`quick graph edit must match v1875 for ${id}/${week}/${value}/${lock}`);
}
console.log('v1876: 10,080 date/practice comparisons and 6 quick-graph edit scenarios passed');
if(process.argv.includes('--benchmark')){
  const large=Array.from({length:12000},(_,i)=>({...base[i%base.length],id:`L${i}`,streamId:i%500===0?'LECTURE':'',typeId:i%500===0?'LEK':'PR'}));
  const time=(opt)=>{const h=harness(large,opt);const t=performance.now();for(let i=0;i<35;i++)h.fn(`L${(i*137)%large.length}`,2,1.5);return (performance.now()-t)/1000;};
  const before=time(false),after=time(true);
  console.log(`v1876 quick graph edit benchmark (12000 loads, 35 cell edits): old ${before.toFixed(3)}s, indexed ${after.toFixed(3)}s, ${(before/after).toFixed(1)}x`);
}
