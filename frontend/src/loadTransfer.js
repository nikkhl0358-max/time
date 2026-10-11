// v1875: load transfer is historical, not a destructive teacher rename.
// Completed teaching remains on the original teacher/load row. Only the
// remainder is moved to the target teacher. Shared scheduling links are
// followed only inside the same subgroup ownership scope; different subgroups
// may be co-scheduled but must remain independently transferable.
export function expandTransferLoadIds(loads = [], seedIds = []) {
  const rows = Array.isArray(loads) ? loads : [];
  const byId = new Map(rows.filter((row) => row?.id).map((row) => [String(row.id), row]));
  const selected = new Set((seedIds || []).map(String).filter((id) => byId.has(id)));
  if (!selected.size) return [];

  // v1888: stream/DO/co-schedule links describe *when rows are taught together*;
  // they do not describe common ownership of teaching load.  A transfer that is
  // started from 2КДД43 p/g 1 must therefore never pull 3КДД40 p/g 1 merely
  // because both rows share streamId/streamGroupIds/remoteSubgroupLoadIds.
  //
  // We only follow a technical duplicate when it is still the exact same
  // ownership identity: group + subgroup + subject + lesson type + teacher.
  // This keeps old duplicate fragments together without crossing into another
  // group or another subgroup.  Selecting several rows explicitly still works:
  // every selected ownership identity is processed independently.
  const ownershipKey = (row) => [
    String(row?.groupId || ""),
    Number(row?.subgroup || 0),
    String(row?.subjectId || ""),
    String(row?.typeId || row?.lessonTypeId || ""),
    String(row?.teacherId || ""),
  ].join("|");

  let changed = true;
  while (changed) {
    changed = false;
    const current = [...selected].map((id) => byId.get(id)).filter(Boolean);
    for (const row of current) {
      const rowKey = ownershipKey(row);
      const linkedIds = new Set((row.remoteSubgroupLoadIds || []).map(String));
      for (const other of rows) {
        if (!other?.id || selected.has(String(other.id))) continue;
        if (ownershipKey(other) !== rowKey) continue;
        const directlyLinked =
          (row.streamId && String(other.streamId || "") === String(row.streamId)) ||
          linkedIds.has(String(other.id)) ||
          (other.remoteSubgroupLoadIds || []).map(String).includes(String(row.id)) ||
          (row.coScheduleId && String(other.coScheduleId || "") === String(row.coScheduleId));
        if (!directlyLinked) continue;
        selected.add(String(other.id));
        changed = true;
      }
    }
  }
  return [...selected];
}

export function resolveTransferParticipantLoadId(loads = [], part = {}, inst = {}, preferredIds = []) {
  const rows = Array.isArray(loads) ? loads : [];
  const explicit = String(part?.loadId || "");
  const groupId = String(part?.groupId || "");
  if (!groupId) return "";
  const subgroup = Number(part?.subgroup ?? inst?.subgroup ?? 0) || 0;
  const subjectId = String(inst?.subjectId || "");
  const typeId = String(inst?.typeId || inst?.lessonTypeId || "");
  const teacherId = String(inst?.teacherId || "");

  const sameLessonRow = (row) => {
    if (!row?.id || String(row.groupId || "") !== groupId) return false;
    if (subjectId && String(row.subjectId || "") !== subjectId) return false;
    if (typeId && String(row.typeId || row.lessonTypeId || "") !== typeId) return false;
    if (teacherId && String(row.teacherId || "") !== teacherId) return false;
    return true;
  };

  // v1889: an old combined card may carry the PRIMARY group's loadId on a
  // secondary participant. Existence of the id is not enough: the row must
  // belong to this participant's group and the same lesson. When subgroup is
  // explicit (>0), require that too. Otherwise ignore the stale id and recover
  // the correct load below.
  if (explicit) {
    const explicitRow = rows.find((row) => String(row?.id || "") === explicit);
    const subgroupCompatible = !subgroup || Number(explicitRow?.subgroup || 0) === subgroup;
    if (explicitRow && sameLessonRow(explicitRow) && subgroupCompatible) return explicit;
  }

  const sameLessonCandidates = rows.filter(sameLessonRow);
  if (!sameLessonCandidates.length) return "";

  const preferred = new Set([...preferredIds].map(String));
  const preferredCandidates = sameLessonCandidates.filter((row) => preferred.has(String(row.id)));
  // Old streamGroupIds/groupIds did not always store a subgroup per secondary
  // group. If exactly one explicitly selected load belongs to this group and
  // lesson, that selection is more authoritative than stale participant data.
  if (preferredCandidates.length === 1) return String(preferredCandidates[0].id);
  if (preferredCandidates.length > 1) {
    const exactPreferred = preferredCandidates.filter((row) => Number(row.subgroup || 0) === subgroup);
    if (exactPreferred.length === 1) return String(exactPreferred[0].id);
  }

  const exact = sameLessonCandidates.filter((row) => Number(row.subgroup || 0) === subgroup);
  if (exact.length === 1) return String(exact[0].id);
  return "";
}

// Kept for compatibility with old tests/imports. New UI uses splitRemainingLoads.
export function transferLoadsToTeacher(loads = [], seedIds = [], targetTeacherId = "") {
  const target = String(targetTeacherId || "");
  if (!target) return { loads: Array.isArray(loads) ? loads : [], affectedIds: [], requestedIds: [] };
  const requestedIds = (seedIds || []).map(String);
  const affectedIds = expandTransferLoadIds(loads, requestedIds);
  const affected = new Set(affectedIds);
  return {
    loads: (loads || []).map((row) => row?.id && affected.has(String(row.id)) ? { ...row, teacherId: target } : row),
    affectedIds,
    requestedIds,
  };
}

const pairSum = (wp = {}) => Object.values(wp || {}).reduce((sum, value) => sum + Math.max(0, Number(value) || 0), 0);
const cleanWeeklyPairs = (wp = {}) => Object.fromEntries(Object.entries(wp || {})
  .map(([week, value]) => [String(Number(week)), Math.max(0, Number(value) || 0)])
  .filter(([week, value]) => Number(week) > 0 && value > 1e-9));

// completedByLoadWeek shape: { [loadId]: { [week]: pairUnitsAlreadyConducted } }
// idFactory is injected by the UI so this module remains deterministic in tests.
export function splitRemainingLoads(loads = [], affectedIds = [], targetTeacherId = "", completedByLoadWeek = {}, idFactory = null, transferEffectiveFrom = "") {
  const target = String(targetTeacherId || "");
  const rows = Array.isArray(loads) ? loads : [];
  const affected = new Set((affectedIds || []).map(String));
  const makeId = typeof idFactory === "function" ? idFactory : (() => `transfer_${Math.random().toString(36).slice(2,10)}`);
  if (!target || !affected.size) return { loads: rows, mapping: {}, transferredIds: [], historicalIds: [], transferredPairs: 0, historicalPairs: 0 };

  const rowById = new Map(rows.filter((row)=>row?.id).map((row)=>[String(row.id), row]));
  const split = new Map();
  for (const row of rows) {
    const id = String(row?.id || "");
    if (!id || !affected.has(id)) continue;
    const planned = cleanWeeklyPairs(row.weeklyPairs || {});
    const completedRaw = completedByLoadWeek?.[id] || {};
    const oldWp = {}, newWp = {};
    for (const [week, plannedValueRaw] of Object.entries(planned)) {
      const plannedValue = Math.max(0, Number(plannedValueRaw) || 0);
      const completed = Math.max(0, Math.min(plannedValue, Number(completedRaw?.[week] ?? completedRaw?.[Number(week)] ?? 0) || 0));
      const remaining = Math.max(0, plannedValue - completed);
      if (completed > 1e-9) oldWp[week] = completed;
      if (remaining > 1e-9) newWp[week] = remaining;
    }
    const historicalPairs = pairSum(oldWp), remainingPairs = pairSum(newWp);
    if (remainingPairs <= 1e-9) {
      split.set(id, { mode:"none", row, oldWp, newWp, historicalPairs, remainingPairs, targetId:"" });
    } else if (historicalPairs <= 1e-9) {
      // Nothing has been conducted yet: the same row can safely change owner.
      split.set(id, { mode:"move", row, oldWp, newWp, historicalPairs, remainingPairs, targetId:id });
    } else {
      split.set(id, { mode:"split", row, oldWp, newWp, historicalPairs, remainingPairs, targetId:String(makeId()) });
    }
  }

  const mapping = Object.fromEntries([...split.entries()].filter(([,x])=>x.mode!=="none").map(([id,x])=>[id,x.targetId]));
  const oldHistoryIds = new Set([...split.entries()].filter(([,x])=>x.historicalPairs>1e-9).map(([id])=>id));
  const streamKeyMap = new Map(), coKeyMap = new Map();
  const linkedKey = (map, raw, prefix) => {
    const key = String(raw || "");
    if (!key) return "";
    if (!map.has(key)) map.set(key, `${key}__${prefix}_${makeId()}`);
    return map.get(key);
  };

  const out = [];
  let transferredPairs = 0, historicalPairs = 0;
  const transferredIds = [], historicalIds = [];
  for (const row of rows) {
    const id = String(row?.id || "");
    const info = split.get(id);
    if (!info) { out.push(row); continue; }
    historicalPairs += info.historicalPairs;
    transferredPairs += info.remainingPairs;

    if (info.mode === "none") {
      out.push(row);
      if (info.historicalPairs > 0) historicalIds.push(id);
      continue;
    }

    const remapRemote = (refs, keepHistorical) => (refs || []).map(String).map((ref) => {
      if (keepHistorical) return oldHistoryIds.has(ref) ? ref : "";
      const linked = rowById.get(ref);
      // v1887: after an ownership transfer, a cross-subgroup ДО link must be
      // broken. Otherwise p/g 1 can silently re-attach to p/g 2 and later
      // scheduling operations start changing both teachers again.
      if (linked && Number(linked.subgroup || 0) !== Number(row.subgroup || 0)) return "";
      return mapping[ref] || (affected.has(ref) ? "" : ref);
    }).filter(Boolean);

    if (info.mode === "move") {
      const moved = {
        ...row,
        teacherId: target,
        weekPattern:"perWeek", customWeeks:[], weeklyPairs:{...info.newWp}, graphMode:"manual",
        remoteSubgroupLoadIds: remapRemote(row.remoteSubgroupLoadIds, false),
        streamId: row.streamId ? linkedKey(streamKeyMap, row.streamId, "TRANSFER") : row.streamId,
        coScheduleId: row.coScheduleId ? linkedKey(coKeyMap, row.coScheduleId, "TRANSFER") : row.coScheduleId,
        transferFromTeacherId: row.teacherId || "",
        ...(transferEffectiveFrom ? { transferEffectiveFrom } : {}),
      };
      out.push(moved); transferredIds.push(id);
      continue;
    }

    const historical = {
      ...row,
      weekPattern:"perWeek", customWeeks:[], weeklyPairs:{...info.oldWp}, graphMode:"manual",
      remoteSubgroupLoadIds: remapRemote(row.remoteSubgroupLoadIds, true),
      ...(transferEffectiveFrom ? { transferHistoricalBefore:transferEffectiveFrom, transferToTeacherId:target } : {}),
    };
    const remainder = {
      ...row,
      id: info.targetId,
      teacherId: target,
      weekPattern:"perWeek", customWeeks:[], weeklyPairs:{...info.newWp}, graphMode:"manual",
      remoteSubgroupLoadIds: remapRemote(row.remoteSubgroupLoadIds, false),
      streamId: row.streamId ? linkedKey(streamKeyMap, row.streamId, "TRANSFER") : row.streamId,
      coScheduleId: row.coScheduleId ? linkedKey(coKeyMap, row.coScheduleId, "TRANSFER") : row.coScheduleId,
      transferOriginLoadId: id,
      transferFromTeacherId: row.teacherId || "",
      ...(transferEffectiveFrom ? { transferEffectiveFrom } : {}),
    };
    out.push(historical, remainder);
    historicalIds.push(id); transferredIds.push(info.targetId);
  }

  return { loads: out, mapping, transferredIds, historicalIds, transferredPairs, historicalPairs };
}
