// Roll back only cells touched by a rejected schedule patch.  Replacing the
// entire previous schedule would erase later successful drags in other cells.
const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function rollbackRejectedSchedulePatch(current, before, attempted, patch = {}) {
  const changed = new Set();
  for (const field of ['assignmentSet', 'instancesSet']) {
    for (const id of Object.keys(patch[field] || {})) changed.add(String(id));
  }
  for (const field of ['assignmentRemove', 'instancesRemove', 'lockedAdd', 'lockedRemove', 'unplacedAdd', 'unplacedRemove']) {
    for (const id of (patch[field] || [])) changed.add(String(id));
  }
  if (!changed.size) return current;
  const next = { ...(current || {}) };
  const currentAssignments = current?.assignment || {};
  const beforeAssignments = before?.assignment || {};
  const attemptedAssignments = attempted?.assignment || {};
  let assignments = null;
  for (const id of changed) {
    const currentOwns = own(currentAssignments, id);
    const attemptedOwns = own(attemptedAssignments, id);
    if (currentOwns !== attemptedOwns || !same(currentAssignments[id], attemptedAssignments[id])) continue;
    if (!assignments) assignments = { ...currentAssignments };
    if (own(beforeAssignments, id)) assignments[id] = beforeAssignments[id];
    else delete assignments[id];
  }
  if (assignments) next.assignment = assignments;

  const currentInstances = current?.instances || [];
  const beforeMap = new Map((before?.instances || []).map(row => [String(row?.instId || ''), row]));
  const attemptedMap = new Map((attempted?.instances || []).map(row => [String(row?.instId || ''), row]));
  const currentMap = new Map(currentInstances.map(row => [String(row?.instId || ''), row]));
  let instancesChanged = false;
  for (const id of changed) {
    const cur = currentMap.get(id);
    const attempt = attemptedMap.get(id);
    if (currentMap.has(id) !== attemptedMap.has(id) || !same(cur, attempt)) continue;
    if (beforeMap.has(id)) currentMap.set(id, beforeMap.get(id));
    else currentMap.delete(id);
    instancesChanged = true;
  }
  if (instancesChanged) next.instances = [...currentMap.values()];

  for (const field of ['locked', 'unplaced']) {
    const cur = new Set((current?.[field] || []).map(String));
    const attemptedSet = new Set((attempted?.[field] || []).map(String));
    const beforeSet = new Set((before?.[field] || []).map(String));
    let dirty = false;
    for (const id of changed) {
      if (cur.has(id) !== attemptedSet.has(id)) continue; // newer edit owns this id
      if (beforeSet.has(id) === cur.has(id)) continue;
      if (beforeSet.has(id)) cur.add(id);
      else cur.delete(id);
      dirty = true;
    }
    if (dirty) next[field] = [...cur];
  }
  return next;
}
