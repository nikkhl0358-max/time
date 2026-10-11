// Date-specific partial cancellation of a multi-group stream.
// This never mutates the semester stream/load: it only creates a display/effective
// occurrence for one substitution date with selected participant groups removed.

export function normalizeExcludedGroupIds(values = []) {
  return [...new Set((Array.isArray(values) ? values : [values]).map((x) => String(x || "")).filter(Boolean))];
}

export function substitutionExcludesGroup(substitution, groupId) {
  if (!substitution || substitution.type !== "stream_group_cancel") return false;
  const gid = String(groupId || "");
  return !!gid && normalizeExcludedGroupIds(substitution.excludedGroupIds).includes(gid);
}

export function trimStreamOccurrenceGroups(inst, participants = [], excludedGroupIds = []) {
  if (!inst) return null;
  const excluded = new Set(normalizeExcludedGroupIds(excludedGroupIds));
  const normalized = (Array.isArray(participants) ? participants : [])
    .filter((p) => p?.groupId)
    .map((p) => ({ ...p, groupId: String(p.groupId), subgroup: Number(p.subgroup || 0) || 0, loadId: p.loadId || "" }));
  const active = normalized.filter((p) => !excluded.has(String(p.groupId)));
  if (!active.length) return null;

  const primary = active[0];
  const rest = active.slice(1);
  return {
    ...inst,
    groupId: primary.groupId,
    subgroup: Number(primary.subgroup || 0) || 0,
    loadId: primary.loadId || (String(inst.groupId || "") === String(primary.groupId) ? (inst.loadId || "") : ""),
    groupIds: [],
    streamParticipants: rest.map((p) => ({ ...p })),
    streamGroupIds: [...new Set(rest.map((p) => String(p.groupId)).filter((gid) => gid && gid !== String(primary.groupId)))],
    // Prevent load-link enrichment from re-attaching the group that was
    // deliberately removed for this one concrete date.
    manualStreamBreak: true,
    manualStreamSnapshot: true,
    dateSpecificStreamSplit: true,
  };
}
