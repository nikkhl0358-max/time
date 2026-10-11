import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { performance } from 'node:perf_hooks';
import { buildScheduleDateLookup } from '../frontend/src/scheduleHotpath.js';
import {
  calendarExceptionForFast, isHolidayFast, groupPracticeForDateFast,
  findSubstitutionFast, indexMovedSubstitutionsByDate, indexRowsByDate,
  indexAssignedInstancesByDay, findMovedSourceInstanceFast
} from '../frontend/src/fastScheduleLookups.js';

// Lookup equivalence: ordering and historical/public ids are not optional.
const data = {
  calendarExceptions:[{date:'2026-09-28',noClasses:false,id:'first'},{date:'2026-09-28',noClasses:true,id:'second'}],
  config:{calendarExceptions:[{date:'2026-09-28',noClasses:true,id:'config'},{date:'2026-09-29',noClasses:true,id:'config2'}],holidays:['2026-09-29']},
  practices:[
    {groupId:'g1',dateStart:'2026-09-20',dateEnd:'2026-09-30',id:'p1'},
    {groupId:'g1',dateStart:'2026-09-28',dateEnd:'2026-10-02',id:'p2'},
    {groupId:'g2',dateStart:'2026-09-28',dateEnd:'2026-09-28',id:'p3'},
    {groupId:7,dateStart:'2026-09-28',dateEnd:'2026-09-28',id:'p4'},
  ],
};
for (const date of ['2026-09-18','2026-09-28','2026-09-29','2026-10-03']) {
  const slow=[...data.calendarExceptions,...data.config.calendarExceptions].find(x=>x.date===date)||null;
  assert.equal(calendarExceptionForFast(data,date),slow);
  assert.equal(isHolidayFast(data.config,date),data.config.holidays.includes(date));
  for(const groupId of ['g1','g2','g3','7',7]){
    const old=data.practices.find(p=>p.groupId===groupId && p.dateStart<=date && date<=p.dateEnd)||null;
    assert.equal(groupPracticeForDateFast(data,groupId,date),old);
  }
}
const updated={...data,practices:[{groupId:'g1',dateStart:'2026-10-01',dateEnd:'2026-10-02',id:'new'}]};
assert.equal(groupPracticeForDateFast(updated,'g1','2026-09-28'),null,'new immutable snapshot invalidates practice index');
const substitutions=[
  {date:'2026-09-28',instId:'old',type:'cancel',id:'a'},
  {date:'2026-09-28',instId:'visible',type:'sub',id:'b'},
  {date:'2026-09-28',instId:'old',type:'sub',id:'c'},
  {date:'2026-09-29',instId:'visible',type:'moved',targetDate:'2026-10-01',id:'d'},
  {date:'2026-09-30',instId:'other',type:'moved',targetDate:'2026-10-01',id:'e'},
];
for(const date of ['2026-09-28','2026-09-29','2026-10-01']) {
  for(const [visible,source] of [['visible','old'],['old','visible'],['other',''],['none','']]) {
    const slow=substitutions.find(s=>s.date===date && (String(s.instId||'')===visible || (!!source && String(s.instId||'')===source)))||null;
    assert.equal(findSubstitutionFast(substitutions,date,visible,source),slow);
  }
}
assert.deepEqual(indexMovedSubstitutionsByDate(substitutions).get('2026-10-01'),[substitutions[3],substitutions[4]]);
assert.deepEqual(indexRowsByDate(substitutions).get('2026-09-28'),substitutions.slice(0,3));
const instances=[{instId:'v1',_publishedSourceInstId:'old'}, {instId:'old'}, {instId:'v2',_publishedSourceInstId:'old'}, {instId:'z'}];
for(const id of ['old','v1','v2','z','unknown']){
  const slow=instances.find(i=>String(i.instId||'')===id || String(i._publishedSourceInstId||'')===id)||null;
  assert.equal(findMovedSourceInstanceFast(instances,id),slow);
}
const assignments={v1:{day:1,period:2},old:{day:1,period:2},v2:{day:'1',period:3},z:{day:0,period:0}};
const dayIndex=indexAssignedInstancesByDay(instances,assignments);
for(const day of [0,1,2,'1']){
  const slow=instances.map(inst=>({inst,a:assignments[inst.instId]})).filter(r=>r.a && r.a.day===day);
  assert.deepEqual(dayIndex.get(day)||[],slow);
}

// Extract and execute the REAL pre/post admin index bodies, with identical
// fixture dependencies. This detects semantic drift from signature hoisting.
const before=fs.readFileSync(new URL('./fixture_v1875_schedule_index.txt',import.meta.url),'utf8');
const after=fs.readFileSync(new URL('../frontend/src/App.jsx',import.meta.url),'utf8');
const extract=(src)=>{
  const start=src.indexOf('  const buildScheduleCellIndex = (schedule, onlyWeek = null) => {');
  const end=src.indexOf('\n  const emptyCellIndex = useMemo',start);
  assert(start>=0 && end>start,'index body must be found');
  return src.slice(start,end).trim();
};
const iso=(d)=>`${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
const addDays=(s,n)=>{const d=new Date(s+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+Number(n));return iso(d);};
const mondayOf=(s)=>{const d=new Date(s+'T00:00:00Z');return addDays(s,-(d.getUTCDay()+6)%7);};
const config={semesterStart:'2026-09-01',semesterEnd:'2027-01-17'};
const randomInst=[];const assignment={};const byWeek=new Map();const byId=new Map();const weeksById=new Map();
for(let i=0;i<1250;i++){
  const weekSet=i%7===0?[2,5,7,10,15]:Array.from({length:18},(_,j)=>j+1);
  const inst={instId:`i${i}`,loadId:`l${i%950}`,groupId:`g${i%95}`,groupIds:[`g${i%95}`,`g${(i+3)%95}`],streamParticipants:[{groupId:`g${i%95}`,subgroup:i%3,loadId:`l${i%950}`}],teacherId:`t${i%30}`,subjectId:`s${i%11}`,typeId:`lt${i%3}`,format:i%4?'inperson':'remote',subgroup:i%3,halfPair:i%13===0,customWeeks:weekSet,weekPattern:'custom'};
  randomInst.push(inst);byId.set(inst.instId,inst);weeksById.set(inst.instId,weekSet);
  assignment[inst.instId]={day:i%6,period:i%4,half:i%2};
  for(const w of weekSet){if(!byWeek.has(w))byWeek.set(w,[]);byWeek.get(w).push(inst.instId);}
}
const ctx={
 data:{config}, scheduleInstIdsByWeek:byWeek,scheduleInstById:byId,scheduleWeeksByInstId:weeksById,
 offGraphPurgePendingIds:new Set(['i5']),offGraphOccurrencePurgePendingKeys:new Set(['i2::3']),
 weekNumbersForInstance:(_config,inst)=>inst.customWeeks,
 isNoClassDate:(_data,date)=>date==='2026-10-12',
 instanceAppliesToDate:(_config,inst,date)=>inst.customWeeks.includes(1+Math.floor((new Date(date+'T00:00:00Z')-new Date(mondayOf(config.semesterStart)+'T00:00:00Z'))/(7*86400000))),
 streamGroups:(inst)=>inst.groupIds,
 isGroupOnPractice:(_data,gid,date)=>gid==='g9' && date>='2026-09-28' && date<='2026-10-05',
 canonicalRequirementLayer:(inst)=>inst.halfPair?'half':'full',
 instanceTeacherIds:(inst)=>[inst.teacherId],
 mondayOf,addDays,Map,Set,Number,String,Array,console
};
ctx.scheduleDateLookup=buildScheduleDateLookup({...config,holidays:['2026-10-12']},[],[{groupId:'g9',dateStart:'2026-09-28',dateEnd:'2026-10-05'}],20,addDays,mondayOf);
const compile=(src)=>vm.runInNewContext(`${extract(src)}\nbuildScheduleCellIndex`,{...ctx});
const oldIndex=compile(before),newIndex=compile(after);
const normalize=(index)=>{
 const sort=(m,fn)=>[...m].map(([key,rows])=>[key,fn(rows)]).sort(([a],[b])=>String(a).localeCompare(String(b)));
 return {
  bySlot:sort(index.bySlot,rows=>rows.map(r=>r.inst.instId)),
  byDayPeriod:sort(index.byDayPeriod,rows=>rows.map(r=>r.inst.instId)),
  weeksByInstId:sort(index.weeksByInstId,weeks=>[...weeks]),
  instanceIndexById:sort(index.instanceIndexById,x=>x),
 };
};
const schedule={instances:randomInst,assignment};
for(const week of [null,1,2,5,10,18]){
  assert.deepEqual(JSON.parse(JSON.stringify(normalize(newIndex(schedule,week)))),JSON.parse(JSON.stringify(normalize(oldIndex(schedule,week)))),`admin index must match original for week=${week}`);
}
assert.match(after,/const visibleAggregateList = aggregateShowAll \? list : list\.slice/, 'large aggregate views must be paginated');
assert.match(after,/\{visibleAggregateList\.flatMap\(\(group\)/, 'group table must render bounded list');
assert.match(after,/visibleAllTeachers : visibleAggregateList/, 'room table must render bounded list');
assert.match(after,/const exactDraft = draftLoadsRef\.current \|\| \[\]/,'quick graph save safety retained');
assert.match(after,/repairPracticeHiddenAssignments/,'practice repair retained');
assert.match(after,/transferTeacherRemainder/,'teacher transfer retained');
assert.match(after,/buildActualSubstitutionRows/,'full substitutions report retained');
console.log('v1876: indexed lookups, public day selection, aggregate paging and admin index semantic equivalence PASSED');
