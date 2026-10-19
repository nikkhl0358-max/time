// Strict timetable identity: restored load identifiers may differ after recovery.
// We only permit a fallback match when ALL scheduling-relevant properties agree.
export function lessonIdentity(row) {
  const participants=[{groupId:row.groupId,subgroup:row.subgroup},...(row.streamParticipants||[])]
    .map(p=>`${p.groupId||''}:${Number(p.subgroup||0)}`).sort();
  return JSON.stringify({participants,subjectId:row.subjectId||'',teacherId:row.teacherId||'',
    typeId:row.typeId||'',format:row.format||'inperson',halfPair:!!row.halfPair,
    layer:String(row.requirementLayer||'1')});
}
export function buildUniqueGraphIdentityMap(graphRows) {
  const found=new Map(), ambiguous=new Set();
  for(const row of graphRows){
    const key=lessonIdentity(row);
    if(found.has(key)) ambiguous.add(key);
    else found.set(key,row);
  }
  for(const key of ambiguous) found.delete(key);
  return found;
}
