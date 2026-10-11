// v1870: split one teaching load when the teacher changes during the semester.
// The load/graph is split by week, so already completed hours remain attached to
// the former teacher and only the remaining hours belong to the replacement.

const num = (v) => Number(v) || 0;
const uniq = (xs) => [...new Set(xs)];

export function normalizedLoadWeekPairs(load, totalWeeks, parityForWeek = null) {
  const out = {};
  const explicit = load?.weeklyPairs && typeof load.weeklyPairs === 'object' ? load.weeklyPairs : {};
  if ((load?.weekPattern || 'all') === 'perWeek' || Object.keys(explicit).length) {
    for (const [rawWeek, rawCount] of Object.entries(explicit)) {
      const week = Number(rawWeek), count = Math.max(0, num(rawCount));
      if (week >= 1 && week <= totalWeeks && count > 0) out[week] = count;
    }
    return out;
  }
  const pairs = Math.max(0, num(load?.pairsPerWeek));
  if (!pairs) return out;
  const pattern = load?.weekPattern || 'all';
  if (pattern === 'custom') {
    for (const rawWeek of load?.customWeeks || []) {
      const week = Number(rawWeek);
      if (week >= 1 && week <= totalWeeks) out[week] = pairs;
    }
    return out;
  }
  for (let week = 1; week <= totalWeeks; week++) {
    const parity = parityForWeek ? parityForWeek(week) : (week % 2 ? 'odd' : 'even');
    if (pattern === 'all' || pattern === parity) out[week] = pairs;
  }
  return out;
}

export function loadHoursFromWeekPairs(weeklyPairs) {
  return Object.values(weeklyPairs || {}).reduce((sum, value) => sum + Math.max(0, num(value)), 0) * 2;
}


// v1871: substitutions/cancellations are date overlays keyed by instId. When a
// recurring instance is split at the teacher-transfer boundary, future overlays
// must follow the new instance id or a cancellation/replacement can silently
// stay attached to the former teacher's past-only instance.
export function remapTeacherTransferSubstitutions(substitutions, futureIdMap, movedWeeks, weekForDate) {
  const rows = Array.isArray(substitutions) ? substitutions : [];
  const idMap = futureIdMap instanceof Map ? futureIdMap : new Map(Object.entries(futureIdMap || {}));
  const allowedWeeks = new Set((movedWeeks || []).map(Number));
  if (!idMap.size || !allowedWeeks.size || typeof weekForDate !== 'function') return rows;
  return rows.map((sub) => {
    const week = sub?.date ? Number(weekForDate(sub.date)) : NaN;
    if (!Number.isFinite(week) || !allowedWeeks.has(week)) return sub;
    const mappedInstId = idMap.get(String(sub?.instId || ''));
    const mappedTargetInstId = idMap.get(String(sub?.targetInstId || ''));
    if (!mappedInstId && !mappedTargetInstId) return sub;
    return {
      ...sub,
      ...(mappedInstId ? { instId: mappedInstId, transferredFromInstId: sub.instId } : {}),
      ...(mappedTargetInstId ? { targetInstId: mappedTargetInstId, transferredFromTargetInstId: sub.targetInstId } : {}),
    };
  });
}

function instanceWeeks(inst, totalWeeks, parityForWeek = null) {
  const pattern = inst?.weekPattern || 'all';
  if (pattern === 'custom') return uniq((inst?.customWeeks || []).map(Number).filter((w) => w >= 1 && w <= totalWeeks)).sort((a,b)=>a-b);
  if (pattern === 'perWeek') return Object.entries(inst?.weeklyPairs || {}).filter(([,v])=>num(v)>0).map(([w])=>Number(w)).filter((w)=>w>=1&&w<=totalWeeks).sort((a,b)=>a-b);
  const out = [];
  for (let week=1; week<=totalWeeks; week++) {
    const parity = parityForWeek ? parityForWeek(week) : (week % 2 ? 'odd' : 'even');
    if (pattern === 'all' || pattern === parity) out.push(week);
  }
  return out;
}

function participants(inst) {
  const raw = [
    { groupId: inst?.groupId || '', subgroup: Number(inst?.subgroup || 0), loadId: inst?.loadId || '' },
    ...(Array.isArray(inst?.streamParticipants) ? inst.streamParticipants : []),
  ].filter((p)=>p?.groupId);
  if (raw.length <= 1 && Array.isArray(inst?.streamGroupIds) && inst.streamGroupIds.length) {
    for (const groupId of inst.streamGroupIds) raw.push({ groupId, subgroup: Number(inst?.subgroup || 0), loadId: '' });
  }
  const seen = new Set();
  return raw.filter((p)=>{
    const key = `${String(p.groupId||'')}|${Number(p.subgroup||0)}|${String(p.loadId||'')}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

function withWeeks(inst, weeks) {
  return { ...inst, weekPattern: 'custom', customWeeks: uniq(weeks.map(Number)).sort((a,b)=>a-b), weeklyPairs: undefined };
}

function uniqueInstId(base, used) {
  let id = base, n = 2;
  while (used.has(id)) id = `${base}_${n++}`;
  used.add(id); return id;
}

function sourceStandaloneInstance(inst, sourceLoad, newLoad, weeks, id) {
  return {
    ...withWeeks(inst, weeks),
    instId: id,
    loadId: newLoad.id,
    groupId: sourceLoad.groupId,
    subgroup: Number(sourceLoad.subgroup || 0),
    teacherId: newLoad.teacherId,
    subjectId: sourceLoad.subjectId,
    typeId: sourceLoad.typeId,
    roomType: sourceLoad.roomType || inst.roomType,
    // Do not inherit teacher-specific room preferences from the former teacher.
    preferredRoomIds: [], dedicatedRoomId: '',
    requiredCapacity: Number(sourceLoad.expectedStudents) > 0 ? Number(sourceLoad.expectedStudents) : 0,
    streamId: '', streamGroupIds: [], streamParticipants: [], groupIds: [],
    coScheduleId: '', remoteSubgroupLoadIds: [],
    mandatoryRemoteAssembly: false,
    lectureInPersonStream: false,
    lectureRemoteStream: false,
    practicalInPersonStream: false,
    transferredTeacherRemainder: true,
    transferredFromInstId: inst.instId,
  };
}

function residualStreamInstance(inst, otherParts, loadById, weeks, id) {
  const primaryPart = otherParts.find((p)=>p.loadId && loadById.has(String(p.loadId))) || otherParts[0];
  const primaryLoad = primaryPart?.loadId ? loadById.get(String(primaryPart.loadId)) : null;
  const rest = otherParts.filter((p)=>p !== primaryPart);
  return {
    ...withWeeks(inst, weeks),
    instId: id,
    loadId: primaryLoad?.id || primaryPart?.loadId || inst.loadId,
    groupId: primaryLoad?.groupId || primaryPart?.groupId || inst.groupId,
    subgroup: Number(primaryLoad?.subgroup ?? primaryPart?.subgroup ?? inst.subgroup ?? 0),
    teacherId: primaryLoad?.teacherId || inst.teacherId,
    subjectId: primaryLoad?.subjectId || inst.subjectId,
    typeId: primaryLoad?.typeId || inst.typeId,
    roomType: primaryLoad?.roomType || inst.roomType,
    streamParticipants: rest,
    streamGroupIds: uniq(rest.map((p)=>p.groupId).filter(Boolean)),
    streamId: otherParts.length > 1 ? (inst.streamId || '') : '',
    teacherTransferResidualStream: true,
    transferredFromInstId: inst.instId,
  };
}

export function transferTeacherRemainder({ loads, schedule, loadId, newTeacherId, startWeek, totalWeeks, parityForWeek = null, makeId = null }) {
  const rows = Array.isArray(loads) ? loads : [];
  const source = rows.find((row)=>String(row?.id||'')===String(loadId||''));
  if (!source) throw new Error('Строка нагрузки не найдена');
  if (!newTeacherId) throw new Error('Выберите нового преподавателя');
  if (String(source.teacherId||'') === String(newTeacherId)) throw new Error('Новый преподаватель совпадает с текущим');
  const boundary = Math.max(1, Math.min(Number(totalWeeks)||1, Number(startWeek)||1));
  const allPairs = normalizedLoadWeekPairs(source, totalWeeks, parityForWeek);
  const oldPairs = {}, newPairs = {};
  for (const [rawWeek, count] of Object.entries(allPairs)) {
    const week = Number(rawWeek);
    if (week >= boundary) newPairs[week] = count;
    else oldPairs[week] = count;
  }
  if (!Object.keys(newPairs).length) throw new Error(`С ${boundary}-й недели у этой нагрузки нет оставшихся пар`);

  // v1874 safety: do not create a second future load when the replacement
  // teacher already has the same group/subject/type/subgroup in any of the
  // transferred weeks. This is deliberately conservative: the administrator
  // must resolve/merge the existing load instead of silently producing two
  // authoritative graph rows for the same teaching requirement.
  const movedWeeksForPreflight = new Set(Object.keys(newPairs).map(Number));
  const overlappingTargetLoads = rows.filter((row) => {
    if (!row || String(row.id || '') === String(source.id || '')) return false;
    if (String(row.teacherId || '') !== String(newTeacherId || '')) return false;
    if (String(row.groupId || '') !== String(source.groupId || '')) return false;
    if (String(row.subjectId || '') !== String(source.subjectId || '')) return false;
    if (String(row.typeId || row.lessonTypeId || '') !== String(source.typeId || source.lessonTypeId || '')) return false;
    if (Number(row.subgroup || 0) !== Number(source.subgroup || 0)) return false;
    const weeks = normalizedLoadWeekPairs(row, totalWeeks, parityForWeek);
    return Object.keys(weeks).map(Number).some((week) => movedWeeksForPreflight.has(week));
  });
  if (overlappingTargetLoads.length) {
    throw new Error('Передача остановлена: у нового преподавателя уже есть пересекающаяся строка этой нагрузки в передаваемых неделях. Сначала объедините/исправьте нагрузку, чтобы не получить две записи.');
  }

  // v1874 safety: a transfer must not multiply an already existing residual
  // schedule duplicate. Only placed occurrences are checked; several unplaced
  // requirements of the same load are legitimate. Room is intentionally NOT
  // part of the key, because the historical failure mode is one lesson copied
  // into two different rooms.
  const srcScheduleForPreflight = schedule && typeof schedule === 'object' ? schedule : null;
  if (srcScheduleForPreflight && Array.isArray(srcScheduleForPreflight.instances)) {
    const occurrenceSeen = new Map();
    const duplicateHits = [];
    for (const inst of srcScheduleForPreflight.instances) {
      if (!inst || inst.isScheduleEvent) continue;
      const instParts = participants(inst);
      const sourcePart = String(inst.loadId || '') === String(source.id || '')
        ? { groupId: source.groupId, subgroup: Number(source.subgroup || 0), loadId: source.id }
        : instParts.find((part) => String(part?.loadId || '') === String(source.id || ''));
      if (!sourcePart) continue;
      const a = srcScheduleForPreflight.assignment?.[inst.instId];
      if (!a) continue;
      const weeks = instanceWeeks(inst, totalWeeks, parityForWeek).filter((week) => movedWeeksForPreflight.has(Number(week)));
      for (const week of weeks) {
        const halfKey = inst.halfPair ? `half:${Number(a.half ?? 0)}` : 'full';
        const key = [
          Number(week), Number(a.day), Number(a.period), halfKey,
          String(source.groupId || ''), Number(source.subgroup || 0),
          String(source.subjectId || ''), String(source.typeId || source.lessonTypeId || ''),
          String(inst.format || 'inperson'),
        ].join('|');
        if (occurrenceSeen.has(key)) {
          duplicateHits.push([occurrenceSeen.get(key), String(inst.instId || '')]);
        } else occurrenceSeen.set(key, String(inst.instId || ''));
      }
    }
    if (duplicateHits.length) {
      throw new Error('Передача остановлена: в будущих неделях этой нагрузки уже есть остаточная двойная запись одной пары. Сначала запустите «Остаточные дубли» и устраните конфликт, затем повторите передачу.');
    }
  }

  const randomId = makeId || (()=>Math.random().toString(36).slice(2,10));
  const usedLoadIds = new Set(rows.map((row)=>String(row?.id||'')).filter(Boolean));
  let newLoadId = `${source.id}__T${boundary}_${randomId()}`;
  let loadSeq = 2;
  while (usedLoadIds.has(newLoadId)) newLoadId = `${source.id}__T${boundary}_${loadSeq++}_${randomId()}`;

  const oldLoad = {
    ...source, weekPattern:'perWeek', customWeeks:[], weeklyPairs:oldPairs,
    teacherTransferOutWeek: boundary, teacherTransferToId: newTeacherId,
  };
  const newLoad = {
    ...source, id:newLoadId, teacherId:newTeacherId,
    weekPattern:'perWeek', customWeeks:[], weeklyPairs:newPairs,
    // A stream/co-schedule relationship belongs to a concrete teacher. The new
    // teacher starts as an independent load; it can be joined to another stream
    // explicitly afterwards if appropriate.
    streamId:'', streamGroupIds:[], remoteSubgroupLoadIds:[], coScheduleId:'',
    transferredFromLoadId:source.id, transferredFromTeacherId:source.teacherId,
    transferStartWeek:boundary,
  };
  const nextLoads = rows.map((row)=>String(row?.id||'')===String(source.id)?oldLoad:row).concat(newLoad);

  const srcSchedule = schedule && typeof schedule === 'object' ? schedule : null;
  if (!srcSchedule || !Array.isArray(srcSchedule.instances)) {
    return { loads:nextLoads, schedule:srcSchedule, oldLoad, newLoad, meta:{startWeek:boundary,oldHours:loadHoursFromWeekPairs(oldPairs),newHours:loadHoursFromWeekPairs(newPairs),movedWeeks:Object.keys(newPairs).map(Number),scheduleInstancesMoved:0,streamSplits:0} };
  }

  const movedWeekSet = new Set(Object.keys(newPairs).map(Number));
  const loadById = new Map(nextLoads.map((row)=>[String(row?.id||''),row]));
  const usedInstIds = new Set(srcSchedule.instances.map((inst)=>String(inst?.instId||'')).filter(Boolean));
  const nextInstances = [];
  const nextAssignment = { ...(srcSchedule.assignment || {}) };
  const nextLocked = new Set((srcSchedule.locked || []).map(String));
  const nextUnplaced = new Set((srcSchedule.unplaced || []).map(String));
  const futureIdMap = new Map();
  let scheduleInstancesMoved = 0, streamSplits = 0;

  for (const inst of srcSchedule.instances) {
    const parts = participants(inst);
    const sourcePartPresent = String(inst?.loadId||'')===String(source.id) || parts.some((p)=>String(p?.loadId||'')===String(source.id));
    if (!sourcePartPresent || inst?.isScheduleEvent) { nextInstances.push(inst); continue; }
    const weeks = instanceWeeks(inst, totalWeeks, parityForWeek);
    const movedWeeks = weeks.filter((w)=>movedWeekSet.has(Number(w)));
    if (!movedWeeks.length) { nextInstances.push(inst); continue; }
    const keptWeeks = weeks.filter((w)=>!movedWeekSet.has(Number(w)));
    const oldAssignment = nextAssignment[inst.instId] ? { ...nextAssignment[inst.instId] } : null;
    const wasLocked = nextLocked.has(String(inst.instId));
    const wasUnplaced = nextUnplaced.has(String(inst.instId));

    if (keptWeeks.length) nextInstances.push(withWeeks(inst, keptWeeks));
    else {
      delete nextAssignment[inst.instId];
      nextLocked.delete(String(inst.instId));
      nextUnplaced.delete(String(inst.instId));
    }

    const otherParts = parts.filter((p)=>String(p?.loadId||'')!==String(source.id) && !(String(inst?.loadId||'')===String(source.id) && !p?.loadId && String(p?.groupId||'')===String(source.groupId||'') && Number(p?.subgroup||0)===Number(source.subgroup||0)));
    const hasOtherParticipants = otherParts.some((p)=>String(p?.groupId||'')!==String(source.groupId||'') || Number(p?.subgroup||0)!==Number(source.subgroup||0) || (p?.loadId && String(p.loadId)!==String(source.id)));

    if (hasOtherParticipants) {
      const residualId = uniqueInstId(`${inst.instId}__TRANSFER_RESIDUAL_W${boundary}`, usedInstIds);
      const residual = residualStreamInstance(inst, otherParts, loadById, movedWeeks, residualId);
      nextInstances.push(residual);
      if (oldAssignment) nextAssignment[residualId] = { ...oldAssignment };
      if (wasLocked) nextLocked.add(residualId);
      if (wasUnplaced && !oldAssignment) nextUnplaced.add(residualId);
      streamSplits++;
    }

    const newInstId = uniqueInstId(`${inst.instId}__TRANSFER_NEW_W${boundary}`, usedInstIds);
    const moved = sourceStandaloneInstance(inst, source, newLoad, movedWeeks, newInstId);
    nextInstances.push(moved);
    futureIdMap.set(String(inst.instId), newInstId);
    if (oldAssignment) {
      // If the old occurrence was a physical stream, the replacement teacher
      // cannot silently share the same room with the remaining stream. Keep the
      // day/period but require a room decision. Remote lessons need no room.
      nextAssignment[newInstId] = hasOtherParticipants && moved.format !== 'remote'
        ? { ...oldAssignment, roomId:null, extraRoomId:null, extraRoomIds:[], roomPending:true }
        : { ...oldAssignment };
    } else nextUnplaced.add(newInstId);
    if (wasLocked && oldAssignment) nextLocked.add(newInstId);
    scheduleInstancesMoved++;
  }

  const nextSchedule = {
    ...srcSchedule,
    instances:nextInstances,
    assignment:nextAssignment,
    locked:[...nextLocked],
    unplaced:[...nextUnplaced].filter((id)=>!nextAssignment[id]),
  };

  return {
    loads:nextLoads, schedule:nextSchedule, oldLoad, newLoad, futureIdMap,
    meta:{startWeek:boundary,oldHours:loadHoursFromWeekPairs(oldPairs),newHours:loadHoursFromWeekPairs(newPairs),movedWeeks:Object.keys(newPairs).map(Number).sort((a,b)=>a-b),scheduleInstancesMoved,streamSplits}
  };
}
