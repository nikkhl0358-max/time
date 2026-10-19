/**
 * Copy placement from a source week onto the EXISTING canonical requirements of
 * a target week.  Neither source-only rows nor repeated requirement identities
 * may create additional semester demand.
 */
export function planExactBulkWeekCopy({
  sourceWeek, targetWeek, currentWeek, instances, assignment, locked, unplaced,
  targetGraphInstances = [], weeksFor, requirementIds, requirementLayer, participantsFor,
}) {
  const source = Number(sourceWeek), target = Number(targetWeek);
  if (!Number.isInteger(source) || !Number.isInteger(target) || source < 1 || target < 1 || source === target) {
    throw new Error('Недели источника и назначения должны различаться.');
  }
  if (target < Number(currentWeek)) throw new Error('Прошедшие недели нельзя перезаписывать.');
  const all = Array.isArray(instances) ? instances : [];
  const assignments = assignment || {};
  const nextLocked = new Set((locked || []).map(String));
  const nextUnplaced = new Set((unplaced || []).map(String));
  const sourceLessons = all.filter(x => !x.isScheduleEvent && weeksFor(x).map(Number).includes(source));
  const usedIds = new Set(all.map(x => String(x.instId)));
  const unique = seed => {
    const base = String(seed).replace(/[^a-zA-Z0-9_:-]/g, '_');
    let id = base, counter = 2;
    while (usedIds.has(id)) id = `${base}_${counter++}`;
    usedIds.add(id);
    return id;
  };
  const nextAssignment = {...assignments};
  const nextInstances = [];
  const pastTrimIds = [];
  let trimmed = 0;
  for (const inst of all) {
    const weeks = weeksFor(inst).map(Number);
    if (!weeks.includes(target) || inst.isScheduleEvent) { nextInstances.push(inst); continue; }
    trimmed++;
    const remaining = weeks.filter(w => w !== target);
    if (!remaining.length) {
      delete nextAssignment[inst.instId];
      nextLocked.delete(String(inst.instId));
      nextUnplaced.delete(String(inst.instId));
      continue;
    }
    if (remaining.some(w => w < Number(currentWeek) && w !== 1) && target > Number(currentWeek)) {
      pastTrimIds.push(String(inst.instId));
    }
    nextInstances.push({
      ...inst, weekPattern:'custom', customWeeks:remaining,
      blockId:'', blockIndex:0, blockSize:1, blockMode:'',
      manualSplit:true, userManualSplit:true,
    });
  }

  const reqKey = (load, layer) => `${String(load)}|${String(layer)}`;
  const targetCounts = new Map();
  for (const inst of targetGraphInstances) {
    if (inst.isScheduleEvent) continue;
    const layer = requirementLayer(inst);
    for (const load of requirementIds(inst)) {
      const key=reqKey(load,layer);
      targetCounts.set(key,(targetCounts.get(key)||0)+1);
    }
  }
  if (!targetCounts.size) throw new Error(`График ${target}-й недели пуст. Копирование отменено, чтобы не потерять занятия.`);

  const covered = new Map();
  const remainingFor = key => (targetCounts.get(key)||0)-(covered.get(key)||0);
  const claim = key => covered.set(key,(covered.get(key)||0)+1);
  let placed = 0, copiedUnplaced = 0, remote = 0, skipped = 0, duplicate = 0;
  // Assignments take priority over unplaced source rows for a given load/layer.
  const orderedSource = [...sourceLessons].sort((a,b) => Number(!!assignments[b.instId]) - Number(!!assignments[a.instId]));
  for (const inst of orderedSource) {
    const layer = requirementLayer(inst);
    const eligible = requirementIds(inst).map(String).filter(load => remainingFor(reqKey(load,layer)) > 0);
    if (!eligible.length) {
      if (requirementIds(inst).some(load => targetCounts.has(reqKey(load,layer)))) duplicate++;
      else skipped++;
      continue;
    }
    const allIds = requirementIds(inst).map(String);
    let rowParticipants = null;
    if (eligible.length !== allIds.length) {
      const members = participantsFor?.(inst) || [];
      rowParticipants = members.filter(p => eligible.includes(String(p.loadId || '')));
      // Never keep the other stream groups implicitly if a subset cannot be proven.
      if (rowParticipants.length !== eligible.length) { skipped++; continue; }
    }
    const id = unique(`${inst.instId}__EXACT_W${target}`);
    const root = rowParticipants?.[0];
    const row = {
      ...inst, instId:id, weekPattern:'custom', customWeeks:[target],
      ...(root ? {groupId:root.groupId, loadId:root.loadId, subgroup:Number(root.subgroup||0),
        streamParticipants:rowParticipants.slice(1).map(p=>({...p})),
        streamGroupIds:rowParticipants.slice(1).map(p=>p.groupId),
        streamId:rowParticipants.length>1?(inst.streamId||''):''} : {}),
      requirementLayer:layer,
      blockId:'', blockIndex:0, blockSize:1, blockMode:'',
      manualSplit:true, userManualSplit:true,
      exactBulkWeekCopy:true, repeatedFromWeek:source,
    };
    nextInstances.push(row);
    const a = assignments[inst.instId];
    if (a) {
      nextAssignment[id] = {...a};
      nextLocked.add(id);
      nextUnplaced.delete(id);
      placed++;
    } else {
      nextUnplaced.add(id);
      copiedUnplaced++;
    }
    if (row.format === 'remote') remote++;
    for (const load of eligible) claim(reqKey(load,layer));
  }

  // Only missing canonical requirements become unplaced; never append a second
  // row for a requirement already represented by the source week.
  let graphOnly = 0;
  for (const inst of targetGraphInstances) {
    if (inst.isScheduleEvent) continue;
    const layer = requirementLayer(inst);
    const missing = requirementIds(inst).map(String).filter(load => remainingFor(reqKey(load,layer)) > 0);
    if (!missing.length) continue;
    const members = participantsFor?.(inst) || [];
    const chosen = members.filter(p => missing.includes(String(p.loadId||'')));
    if (missing.length !== requirementIds(inst).length && chosen.length !== missing.length) {
      // Prevent accidental duplication of other participants in a stream.
      throw new Error('Не удалось разделить поток по группам для требований целевой недели.');
    }
    const subset = chosen.length ? chosen : members;
    const root = subset[0];
    const row = {
      ...inst,
      ...(root ? {groupId:root.groupId, subgroup:Number(root.subgroup||0),loadId:root.loadId||inst.loadId,
        streamParticipants:subset.slice(1).map(p=>({...p})),
        streamGroupIds:subset.slice(1).map(p=>p.groupId), streamId:subset.length>1?(inst.streamId||''):''} : {}),
      instId:unique(`${inst.instId}__GRAPH_ONLY_W${target}`),
      weekPattern:'custom', customWeeks:[target], requirementLayer:layer,
      blockId:'', blockIndex:0, blockSize:1, blockMode:'',
      manualSplit:true,userManualSplit:true,bulkWeekGraphOnly:true,
    };
    nextInstances.push(row);
    nextUnplaced.add(row.instId);
    for (const load of missing) claim(reqKey(load,layer));
    graphOnly++;
  }
  if ([...targetCounts].some(([key,required]) => (covered.get(key)||0)!==required)) throw new Error('Некоторые требования целевой недели не обработаны. Изменения отменены.');
  return {
    instances:nextInstances,assignment:nextAssignment,
    locked:[...nextLocked],unplaced:[...nextUnplaced],pastTrimIds,
    counts:{trimmed,placed,copiedUnplaced,graphOnly,remote,offGraph:skipped,duplicate},
  };
}
