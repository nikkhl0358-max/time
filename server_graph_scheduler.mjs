import fs from 'node:fs';

const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
const data = input.data || {};
const groupId = String(input.groupId || '');
const overloadMode = String(input.overloadMode || 'strict');
const overloadTolerance = Math.max(0, Number(input.overloadTolerance) || 0);
const loads = Array.isArray(data.loads) ? data.loads : [];
const groups = Array.isArray(data.groups) ? data.groups : [];
const teachers = Array.isArray(data.teachers) ? data.teachers : [];
const subjects = Array.isArray(data.subjects) ? data.subjects : [];
const lessonTypes = Array.isArray(data.lessonTypes) ? data.lessonTypes : [];
const rooms = Array.isArray(data.rooms) ? data.rooms : [];
const curriculumPlan = Array.isArray(data.curriculumPlan) ? data.curriculumPlan : [];
const practices = Array.isArray(data.practices) ? data.practices : [];
const config = data.config || {};
const byId = (arr,id) => arr.find(x => String(x.id) === String(id));
const groupIdsForLoad = l => [l?.groupId, ...((l?.streamGroupIds || []))].filter(Boolean).map(String);
const addDays = (dateStr,n) => { const d=new Date(`${dateStr}T00:00:00`); d.setDate(d.getDate()+n); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const weekdayOf = dateStr => (new Date(`${dateStr}T00:00:00`).getDay()+6)%7;
const mondayOf = dateStr => addDays(dateStr,-weekdayOf(dateStr));
const weekNumberOf = (start,dateStr) => { if(!start)return null; const diff=Math.round((new Date(`${dateStr}T00:00:00`)-new Date(`${mondayOf(start)}T00:00:00`))/86400000); return diff<0?null:Math.floor(diff/7)+1; };
const totalWeeks = (() => { if(!config.semesterStart || !config.semesterEnd) return 0; return weekNumberOf(config.semesterStart,mondayOf(config.semesterEnd)) || 0; })();
const effective = (l,w) => {
  if ((l.weekPattern || 'perWeek') === 'perWeek') return Number(l.weeklyPairs?.[w]) || 0;
  const p=Number(l.pairsPerWeek)||0;
  if(l.weekPattern==='custom') return (l.customWeeks||[]).includes(w)?p:0;
  if(l.weekPattern==='odd') return w%2===1?p:0;
  if(l.weekPattern==='even') return w%2===0?p:0;
  return p;
};
const subgroupWeight = l => Number(l?.subgroup || 0)>0 ? 0.5 : 1;
const isPracticeWeek = (l,w) => {
  if(!config.semesterStart) return false;
  const ws=addDays(mondayOf(config.semesterStart),(w-1)*7), we=addDays(ws,6);
  return groupIdsForLoad(l).some(gid => practices.some(p => String(p.groupId)===gid && (!p.dateEnd || p.dateEnd>=ws) && (!p.dateStart || p.dateStart<=we)));
};
const courseOf = g => { const m=String(g?.name||'').match(/^(\d+)/); return Math.max(1,Math.min(4,Number(m?.[1])||1)); };
const maxPairsPerDay = gid => {
  const g=byId(groups,gid), course=courseOf(g), base=String(g?.educationBase||'9');
  const r=(config.dailyLoadRules||[]).find(x=>Number(x.course)===course && String(x.educationBase)===base);
  return Math.max(1,Math.min(5,Number(r?.maxPairs)||5));
};
const availableDays = (gid,w) => {
  if(!config.semesterStart) return 0;
  const monday=addDays(mondayOf(config.semesterStart),(w-1)*7); let n=0;
  const g=byId(groups,gid);
  for(let day=0;day<(config.activeDays||[]).length;day++){
    if(!config.activeDays?.[day] || Number(g?.dayOff)===day) continue;
    const date=addDays(monday,day);
    if(date<config.semesterStart || date>config.semesterEnd || (config.holidays||[]).includes(date)) continue;
    if(practices.some(p=>String(p.groupId)===String(gid) && (!p.dateEnd||p.dateEnd>=date) && (!p.dateStart||p.dateStart<=date))) continue;
    n++;
  }
  return n;
};
const weekCapacity = (gid,w) => maxPairsPerDay(gid)*availableDays(gid,w);
const typeName = l => String(byId(lessonTypes,l?.typeId)?.name||'').toLowerCase().replace(/ё/g,'е');
const kind = l => { const n=typeName(l); if(n.includes('лек')) return 'lecture'; if(n.includes('практ')||n==='пр') return 'practice'; if(n.includes('лаб')) return 'lab'; if(n.includes('контр')) return 'control'; if(n.includes('зач')||n.includes('зчо')) return 'credit'; return 'regular'; };
const teacherUnavailableAt = (t, day, period, week=null) => {
  const key=`${day}_${period}`;
  if(!t?.availabilityByParity) return (t?.unavailable||[]).includes(key);
  if(week!=null){
    const arr=Number(week)%2===0 ? (t.unavailableEven||[]) : (t.unavailableOdd||[]);
    return arr.includes(key);
  }
  return (t.unavailableOdd||[]).includes(key) && (t.unavailableEven||[]).includes(key);
};
const roomOptions = l => {
  if((l.format||'inperson')==='remote') return 999;
  const s=byId(subjects,l.subjectId)||{}, t=byId(teachers,l.teacherId)||{}; let opts=[...rooms];
  if(t.dedicatedRoomId) opts=opts.filter(r=>String(r.id)===String(t.dedicatedRoomId));
  else if((l.allowedRoomIds||[]).length) opts=opts.filter(r=>(l.allowedRoomIds||[]).map(String).includes(String(r.id)));
  else if(l.roomType && l.roomType!=='любая') opts=opts.filter(r=>String(r.typeId)===String(l.roomType));
  if(s.requiresComputerRoom) opts=opts.filter(r=>r.hasComputers); if(s.requiresArtRoom) opts=opts.filter(r=>r.isArtRoom);
  if(s.requiredRoomTypeId) opts=opts.filter(r=>String(r.typeId)===String(s.requiredRoomTypeId)); return opts.length;
};
const teacherSlots = (l,w) => {
  const t=byId(teachers,l.teacherId); if(!t || t.isVacancy) return 999;
  if(!config.semesterStart) return 0; const monday=addDays(mondayOf(config.semesterStart),(w-1)*7); let n=0;
  for(let day=0;day<(config.activeDays||[]).length;day++){
    if(!config.activeDays?.[day]) continue; const date=addDays(monday,day);
    if(date<config.semesterStart || date>config.semesterEnd || (config.holidays||[]).includes(date)) continue;
    if(groupIdsForLoad(l).some(gid => Number(byId(groups,gid)?.dayOff)===day || practices.some(p=>String(p.groupId)===gid && (!p.dateEnd||p.dateEnd>=date) && (!p.dateStart||p.dateStart<=date)))) continue;
    for(let p=0;p<Number(config.periodsPerDay||6);p++) if(!teacherUnavailableAt(t,day,p,w)) n++;
  } return n;
};
const linkedRows = l => l.streamId ? loads.filter(x=>String(x.streamId)===String(l.streamId)) : [l];
const linkedIds = l => new Set(linkedRows(l).map(x=>String(x.id)));
const planPairs = (gid,l) => {
  const g=byId(groups,gid); if(!g?.specialtyId) return null;
  const rows=curriculumPlan.filter(p=>String(p.specialtyId)===String(g.specialtyId) && String(p.educationBase||'9')===String(g.educationBase||'9') && Number(p.semesterNumber||1)===Number(g.curriculumSemester||1) && String(p.subjectId)===String(l.subjectId) && (!p.lessonTypeId || String(p.lessonTypeId)===String(l.typeId)));
  return rows.length ? Math.max(0,rows.reduce((s,p)=>s+(Number(p.totalHours)||0),0)/2) : null;
};
const sameBucket=(a,b)=>Number(a?.subgroup||0)===Number(b?.subgroup||0);
const rowTotal=l=>Array.from({length:totalWeeks},(_,i)=>effective(l,i+1)).reduce((a,b)=>a+b,0);
const autoLimit = l => {
  const ids=linkedIds(l); const vals=groupIdsForLoad(l).map(gid=>{
    const plan=planPairs(gid,l); if(plan==null)return null; const seen=new Set();
    const other=loads.filter(x=>!ids.has(String(x.id)) && groupIdsForLoad(x).includes(String(gid)) && String(x.subjectId)===String(l.subjectId) && String(x.typeId)===String(l.typeId) && sameBucket(x,l)).filter(x=>{ if(!x.streamId)return true; if(seen.has(x.streamId))return false; seen.add(x.streamId); return true; }).reduce((s,x)=>s+rowTotal(x),0);
    return Math.max(0,plan-other);
  }).filter(v=>v!=null); return vals.length?Math.min(...vals):null;
};

if(!groupId || !totalWeeks){ console.log(JSON.stringify({ok:false,error:'Не выбрана группа или не задан семестр'})); process.exit(0); }
const targetLoads=loads.filter(l=>groupIdsForLoad(l).includes(groupId));
const seenStreams=new Set();
const anchors=targetLoads.filter(l=>{ if(!l.streamId)return true; if(seenStreams.has(l.streamId))return false; seenStreams.add(l.streamId); return true; });
const editable=anchors.filter(l=>!linkedRows(l).some(x=>x.graphLocked));
const editableIds=new Set(editable.flatMap(l=>linkedRows(l).map(x=>String(x.id))));
const next=loads.map(l=>editableIds.has(String(l.id))?{...l,weekPattern:'perWeek',customWeeks:[],weeklyPairs:{},graphMode:'smart'}:{...l,weeklyPairs:{...(l.weeklyPairs||{})}});
const nextById=new Map(next.map(l=>[String(l.id),l]));
const groupWeek={}; for(let w=1;w<=totalWeeks;w++) groupWeek[w]=loads.filter(l=>!editableIds.has(String(l.id)) && groupIdsForLoad(l).includes(groupId)).reduce((s,l)=>s+effective(l,w)*subgroupWeight(l),0);
const teacherWeek=new Map(); const tw=(t,w)=>teacherWeek.get(`${t}|${w}`)||0; const addTw=(t,w,n)=>teacherWeek.set(`${t}|${w}`,tw(t,w)+n);
for(const l of loads.filter(l=>!editableIds.has(String(l.id)))) if(l.teacherId) for(let w=1;w<=totalWeeks;w++) addTw(l.teacherId,w,effective(l,w));
const scarcity=l=>{ let feasible=0,slots=0; for(let w=1;w<=totalWeeks;w++){if(isPracticeWeek(l,w))continue; const cap=weekCapacity(groupId,w)-groupWeek[w]; const tf=teacherSlots(l,w)-tw(l.teacherId,w); if(cap>0&&tf>0&&roomOptions(l)>0){feasible++;slots+=Math.min(cap,tf);}} return {feasible,slots}; };
const rank={lecture:0,regular:1,practice:2,lab:3,control:4,credit:5};
const ordered=[...editable].sort((a,b)=>rank[kind(a)]-rank[kind(b)] || scarcity(a).slots-scarcity(b).slots || String(a.id).localeCompare(String(b.id),'ru',{numeric:true}));
const problems=[]; const priorityOrder=[];
const subjectWeeks=(subjectId,kinds=null)=>{ const out=[]; for(const l of next){if(String(l.subjectId)!==String(subjectId))continue;if(kinds&&!kinds.includes(kind(l)))continue;for(let w=1;w<=totalWeeks;w++)if(effective(l,w)>0)out.push(w);} return out; };
for(const anchor of ordered){
  const l=nextById.get(String(anchor.id)); let left=autoLimit(anchor); const sc=scarcity(anchor); priorityOrder.push({loadId:anchor.id,subject:byId(subjects,anchor.subjectId)?.name||'Дисциплина',teacher:byId(teachers,anchor.teacherId)?.name||'без преподавателя',available:sc.slots,total:Math.max(sc.slots,1),estimatedSlots:sc.slots,roomOptions:roomOptions(anchor)});
  if(left==null){problems.push(`${byId(subjects,l.subjectId)?.name||'Дисциплина'}: нет часов в учебном плане`);continue;}
  const k=kind(l); const loadWeight=subgroupWeight(l); let guard=0;
  while(left>0.0001 && guard++<Math.max(100,totalWeeks*Math.ceil(left)*5)){
    const preferredChunk=({block2:2,block3:3,block4:4}[l.pairing||"none"]||1);
    const step=left>=preferredChunk?preferredChunk:(left>=1?1:0.5);
    const lectureWs=subjectWeeks(l.subjectId,['lecture']); const firstLecture=lectureWs.length?Math.min(...lectureWs):null;
    const instrWs=subjectWeeks(l.subjectId,['lecture','regular','practice','lab']); const lastInstr=instrWs.length?Math.max(...instrWs):null;
    const candidates=[];
    for(let w=1;w<=totalWeeks;w++){
      if(isPracticeWeek(l,w))continue; if((k==='practice'||k==='lab')&&firstLecture!=null&&w<firstLecture)continue; if((k==='practice'||k==='lab')&&firstLecture==null&&next.some(x=>String(x.subjectId)===String(l.subjectId)&&kind(x)==='lecture'))continue;
      if((k==='control'||k==='credit')&&lastInstr!=null&&w<lastInstr)continue;
      const cap=weekCapacity(groupId,w), current=groupWeek[w]||0, projected=Math.max(0,current+loadWeight*step-cap);
      if(overloadMode==='strict'&&projected>1e-9)continue; if(overloadMode==='tolerance'&&projected-overloadTolerance>1e-9)continue;
      const raw=teacherSlots(l,w), occupied=tw(l.teacherId,w); if(raw<999&&occupied+step>raw+1e-9)continue; if(roomOptions(l)<=0)continue;
      const own=Number(l.weeklyPairs?.[w])||0;
      const sameKindSubject=next.filter(x=>String(x.subjectId)===String(l.subjectId)&&kind(x)===k&&groupIdsForLoad(x).includes(groupId)&&String(x.id)!==String(l.id)).reduce((s,x)=>s+effective(x,w),0);
      const complementarySubject=next.filter(x=>{
        if(String(x.subjectId)!==String(l.subjectId)||!groupIdsForLoad(x).includes(groupId)||String(x.id)===String(l.id))return false;
        const xk=kind(x); if(k==='lecture')return xk==='practice'||xk==='lab'; if(k==='practice'||k==='lab')return xk==='lecture'; return false;
      }).reduce((s,x)=>s+effective(x,w),0);
      // v145.1: раньше общий sameSubject штрафовал и полезную связку Лек+Пр,
      // из-за чего автографик специально разводил их по разным неделям. Теперь
      // повтор того же вида получает штраф, а комплементарная Лек/Пр — бонус.
      let score=own*150+current*10+occupied*22+sameKindSubject*40-complementarySubject*55+projected*500;

      // v145: непрерывный недельный рисунок важнее небольшой экономии по
      // текущей загрузке. Заполняем дырки и расширяем существующий блок,
      // не создавая «1,1,1,2,0,1» при доступной пропущенной неделе.
      const usedWeeks=Object.keys(l.weeklyPairs||{}).filter(x=>Number(l.weeklyPairs?.[x])>0).map(Number).sort((a,b)=>a-b);
      if(usedWeeks.length){
        const minUsed=usedWeeks[0], maxUsed=usedWeeks[usedWeeks.length-1];
        if(own<=0 && w>minUsed && w<maxUsed) score-=900;
        else if(own<=0 && (w===minUsed-1 || w===maxUsed+1)) score-=360;
        else if(own<=0 && (w<minUsed-1 || w>maxUsed+1)){
          const distance=w<minUsed ? (minUsed-w-1) : (w-maxUsed-1);
          score+=220+distance*120;
        }
      } else if(k!=='control' && k!=='credit') score+=w*5;

      if(k==='lecture')score+=w*2.5; if(k==='practice'||k==='lab')score+=firstLecture===w?60:0; if(k==='control')score-=w*7; if(k==='credit')score-=w*10;
      candidates.push({w,score});
    }
    let pool=candidates;
    if(step>=1){
      const firstLayer=candidates.filter(c=>(Number(l.weeklyPairs?.[c.w])||0)<1);
      if(firstLayer.length)pool=firstLayer;
      // v145.2: Лек и Пр/Лаб одной дисциплины должны совпадать по неделям
      // настолько, насколько это позволяют реальные ограничения. Практика,
      // расставляемая после лекции, сначала использует недели этой лекции,
      // вместо схемы «лекции по чётным, практики по нечётным».
      if(k==='lecture'||k==='practice'||k==='lab'){
        const paired=pool.filter(c=>{
          const comp=next.filter(x=>{
            if(String(x.subjectId)!==String(l.subjectId)||!groupIdsForLoad(x).includes(groupId)||String(x.id)===String(l.id))return false;
            const xk=kind(x); if(k==='lecture')return xk==='practice'||xk==='lab'; if(k==='practice'||k==='lab')return xk==='lecture'; return false;
          }).reduce((sum,x)=>sum+effective(x,c.w),0);
          return comp>0 && (Number(l.weeklyPairs?.[c.w])||0)<1;
        });
        if(paired.length)pool=paired.map(c=>({...c,score:c.score-2000}));
      }
    }
    else { const attached=candidates.filter(c=>(Number(l.weeklyPairs?.[c.w])||0)>0); if(attached.length)pool=attached.map(c=>({...c,score:c.score-500})); }
    pool.sort((a,b)=>a.score-b.score||a.w-b.w); const best=pool[0]; if(!best)break;
    // v1644: streamId связывает строки по времени, но НЕ делает их часы одинаковыми.
    // Раньше каждый шаг anchor безусловно прибавлялся всем peers потока, поэтому
    // строка с меньшим объёмом могла получить лишние пары. Ограничиваем каждый
    // peer его собственным допустимым объёмом.
    const peers=l.streamId?next.filter(x=>String(x.streamId)===String(l.streamId)): [l];
    for(const p of peers){
      const originalPeer=loads.find(x=>String(x.id)===String(p.id))||p;
      const peerCap=autoLimit(originalPeer);
      const currentTotal=rowTotal(p);
      const add=peerCap==null?0:Math.max(0,Math.min(step,peerCap-currentTotal));
      if(add<=1e-9)continue;
      p.weekPattern='perWeek';p.customWeeks=[];p.graphMode='smart';
      p.weeklyPairs={...(p.weeklyPairs||{}),[best.w]:(Number(p.weeklyPairs?.[best.w])||0)+add};
    }
    groupWeek[best.w]=(groupWeek[best.w]||0)+loadWeight*step; addTw(l.teacherId,best.w,step); left-=step;
  }
  if(left>0.0001)problems.push(`${byId(subjects,l.subjectId)?.name||'Дисциплина'} (${byId(lessonTypes,l.typeId)?.name||'вид'}): не распределено ${String(left*2).replace('.',',')} ак.ч.`);
}
const overloadWeeks=[]; for(let w=1;w<=totalWeeks;w++){const over=Math.max(0,(groupWeek[w]||0)-weekCapacity(groupId,w)); if(over>0)overloadWeeks.push({week:w,overPairs:over});}
const totalPairs=anchors.reduce((s,l)=>s+(autoLimit(l)||0),0); const placedPairs=anchors.reduce((s,l)=>{const n=nextById.get(String(l.id)); return s+rowTotal(n||l);},0);
console.log(JSON.stringify({ok:true,result:{loads:next,summary:{total:totalPairs,placed:placedPairs,unplaced:Math.max(0,totalPairs-placedPairs),problems,overloadWeeks,overloadMode,overloadTolerance,priorityOrder:priorityOrder.slice(0,8)}}}));
