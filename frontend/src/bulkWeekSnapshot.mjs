/**
 * A lossless, all-group snapshot of one timetable week into another.
 * The target graph contributes only requirements that do not exist in the
 * source snapshot; it never rewrites manual format / location choices.
 */
export function planExactBulkWeekCopy({
  sourceWeek, targetWeek, currentWeek, instances, assignment, locked, unplaced,
  targetGraphInstances = [], weeksFor, requirementIds, requirementLayer, participantsFor,
}) {
  const source = Number(sourceWeek), target = Number(targetWeek);
  if (!Number.isInteger(source) || !Number.isInteger(target) || source < 1 || target < 1 || source === target) {
    throw new Error('Недели источника и назначения должны различаться.');
  }
  const all = Array.isArray(instances) ? instances : [];
  const assignments = assignment || {};
  const lockedSet = new Set((locked || []).map(String));
  const unplacedSet = new Set((unplaced || []).map(String));
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
  const nextLocked = new Set(lockedSet);
  const nextUnplaced = new Set(unplacedSet);
  const nextInstances = [];
  const pastTrimIds = [];
  let trimmed = 0;
  for (const inst of all) {
    const weeks = weeksFor(inst).map(Number);
    if (!weeks.includes(target)) { nextInstances.push(inst); continue; }
    // Only lessons, not one-off events: event dates cannot be safely translated.
    if (inst.isScheduleEvent) { nextInstances.push(inst); continue; }
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

  // Format-independent semantic requirement identity. A manual switch to ДО
  // is never rejected merely because the canonical graph uses 'inperson'.
  const reqKey = (load, layer) => `${String(load)}|${String(layer)}`;
  const covered = new Set();
  const cover = inst => {
    for (const load of requirementIds(inst)) covered.add(reqKey(load, requirementLayer(inst)));
  };
  let placed = 0, copiedUnplaced = 0, remote = 0, offGraph = 0;
  const targetGraphKeys = new Set();
  for (const inst of targetGraphInstances) {
    if (inst.isScheduleEvent) continue;
    for (const load of requirementIds(inst)) targetGraphKeys.add(reqKey(load, requirementLayer(inst)));
  }
  for (const inst of sourceLessons) {
    const id = unique(`${inst.instId}__EXACT_W${target}`);
    const row = {
      ...inst, instId:id, weekPattern:'custom', customWeeks:[target],
      requirementLayer:requirementLayer(inst),
      blockId:'', blockIndex:0, blockSize:1, blockMode:'',
      manualSplit:true, userManualSplit:true,
      exactBulkWeekCopy:true, repeatedFromWeek:source,
    };
    nextInstances.push(row);
    const a = assignments[inst.instId];
    if (a) {
      // Preserve everything: ДО/очно is on instance; room, extra room, split
      // half, day, period, and other placement flags are on assignment.
      nextAssignment[id] = {...a};
      nextLocked.add(id);
      nextUnplaced.delete(id);
      placed++;
    } else {
      nextUnplaced.add(id);
      copiedUnplaced++;
    }
    if (row.format === 'remote') remote++;
    if (requirementIds(inst).some(load => !targetGraphKeys.has(reqKey(load,requirementLayer(inst))))) offGraph++;
    cover(inst);
  }

  // Graph-only lessons are retained as unplaced; never steal a slot or
  // override a source lesson. Split missing members of streams when possible.
  let graphOnly = 0;
  for (const inst of targetGraphInstances) {
    if (inst.isScheduleEvent) continue;
    const layer = requirementLayer(inst);
    const missing = requirementIds(inst).filter(load => !covered.has(reqKey(load,layer)));
    if (!missing.length) continue;
    const members = (participantsFor?.(inst) || []);
    const chosen = members.filter(p => missing.includes(String(p.loadId || '')));
    const subset = chosen.length && chosen.length < members.length ? chosen : members;
    const root = subset[0];
    const row = {
      ...inst,
      ...(root ? {groupId:root.groupId, subgroup:Number(root.subgroup || 0), loadId:root.loadId || inst.loadId} : {}),
      ...(root ? {streamParticipants:subset.slice(1).map(p=>({...p})), streamGroupIds:subset.slice(1).map(p=>p.groupId), streamId:subset.length>1?(inst.streamId || ''):''} : {}),
      instId:unique(`${inst.instId}__GRAPH_ONLY_W${target}`),
      weekPattern:'custom', customWeeks:[target], requirementLayer:layer,
      blockId:'', blockIndex:0, blockSize:1, blockMode:'',
      manualSplit:true, userManualSplit:true, bulkWeekGraphOnly:true,
    };
    nextInstances.push(row);
    nextUnplaced.add(row.instId);
    for (const id of missing) covered.add(reqKey(id,layer));
    graphOnly++;
  }
  return {
    instances:nextInstances, assignment:nextAssignment,
    locked:[...nextLocked], unplaced:[...nextUnplaced], pastTrimIds,
    counts:{trimmed, placed, copiedUnplaced, graphOnly, remote, offGraph},
  };
}
