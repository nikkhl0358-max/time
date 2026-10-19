/** Conservative graph reconciliation for a single week.
 * Remove only surplus, unplaced, week-scoped technical copies with ONE load.
 * Never remove placed rows, recurring rows, streams, out-of-graph manual lessons,
 * or rows for which canonical graph quantity is ambiguous. */
export function planWeekGraphExcess({week, instances, assignment, targetGraphInstances, weeksFor, requirementIds, requirementLayer}) {
  const target=Number(week), placement=assignment||{};
  const keyOf=(id,layer)=>`${String(id)}|${String(layer)}`;
  const expected=new Map(), actual=new Map();
  const keysOf = row => [...new Set((requirementIds(row)||[]).map(String).filter(Boolean))].map(id=>keyOf(id,requirementLayer(row)));
  for (const row of targetGraphInstances||[]) {
    if (row.isScheduleEvent) continue;
    for (const key of keysOf(row)) expected.set(key,(expected.get(key)||0)+1);
  }
  const rows=(instances||[]).filter(row=>!row.isScheduleEvent && weeksFor(row).map(Number).includes(target));
  for (const row of rows) for (const key of keysOf(row)) actual.set(key,(actual.get(key)||0)+1);
  const removable=[],review=[];
  // Prefer removing graph-only unplaced artifacts over copied unplaced lessons.
  const candidates=rows.filter(row => {
    const weeks=weeksFor(row).map(Number);
    return weeks.length===1 && weeks[0]===target && !placement[row.instId] &&
      (row.bulkWeekGraphOnly===true || row.exactBulkWeekCopy===true) &&
      keysOf(row).length===1 &&
      !row.streamId && !(row.streamParticipants||[]).length && !(row.streamGroupIds||[]).length;
  }).sort((a,b)=>Number(!!b.bulkWeekGraphOnly)-Number(!!a.bulkWeekGraphOnly));
  for (const row of candidates) {
    const key=keysOf(row)[0];
    const expectedCount=expected.get(key);
    const actualCount=actual.get(key)||0;
    // Do not infer that a manual off-graph lesson is a duplicate.
    if (!expectedCount) {review.push({id:row.instId,reason:'Нет требования графика',key});continue;}
    if (actualCount<=expectedCount) continue;
    removable.push({id:row.instId,key,kind:row.bulkWeekGraphOnly?'GRAPH_ONLY':'EXACT'});
    actual.set(key,actualCount-1);
  }
  const stillExcess=[...actual].filter(([key,count])=>count>(expected.get(key)||0)).map(([key,count])=>({key,actual:count,expected:expected.get(key)||0}));
  return {removable,review,stillExcess,examined:rows.length,expected:expected.size};
}
