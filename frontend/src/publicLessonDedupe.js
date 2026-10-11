// v1865: Presentation guard for residual schedule duplicates in public views.
//
// A legacy/manual stream split can leave two technical instances for the same
// real lesson occurrence.  They may even carry different room assignments.
// For a group/teacher this is still ONE lesson, so do not show two cards.
// Room views are intentionally not collapsed: until an administrator resolves
// the conflict, every room must continue to show that it is technically occupied.
//
// This helper NEVER mutates schedule storage.  When duplicate copies disagree
// on rooms it annotates the surviving cell so the public UI can show an explicit
// room conflict instead of silently choosing one room.
export function dedupePublicLessonRows(cells, kind, viewId, groupSubgroups, participantTokens) {
  if (kind === 'room') return [...(cells || [])];
  const out = [];
  const indexByKey = new Map();
  const roomIdsFor = (cell) => {
    const inst = cell?.inst || {};
    const a = cell?.a || {};
    if (inst.format === 'remote') return ['__REMOTE__'];
    return [...new Set([a.roomId || '', a.extraRoomId || '', ...(a.extraRoomIds || [])].map(String).filter(Boolean))].sort();
  };
  for (const cell of cells || []) {
    const inst = cell?.inst;
    const a = cell?.a;
    if (!inst || !a || inst.isScheduleEvent || cell.subOverride) {
      out.push(cell);
      continue;
    }
    const scope = kind === 'group'
      ? `group:${String(viewId)}:${(groupSubgroups(cell) || []).map(Number).sort((x,y)=>x-y).join(',')}`
      : `participants:${(participantTokens(cell) || []).map(String).sort().join(',')}`;
    if (scope.endsWith(':')) { out.push(cell); continue; }
    // Room is deliberately NOT part of identity. Two otherwise identical cards
    // in different rooms are a residual conflict, not two lessons for a student.
    const key = [scope, String(inst.subjectId || ''), String(inst.lessonTypeId || inst.typeId || ''),
      [...new Set([inst.teacherId || '', ...(inst.teacherIds || [])].map(String).filter(Boolean))].sort().join(','),
      inst.format || 'inperson', Number(a.day), Number(a.period),
      inst.halfPair ? `half:${a.half ?? 0}` : 'full'].join('|');
    if (!indexByKey.has(key)) {
      const first = { ...cell, _publicDuplicateInstIds: [String(inst.instId || '')].filter(Boolean), _publicDuplicateRoomIds: roomIdsFor(cell) };
      indexByKey.set(key, out.length);
      out.push(first);
      continue;
    }
    const idx = indexByKey.get(key);
    const prev = out[idx];
    const ids = [...new Set([...(prev?._publicDuplicateInstIds || []), String(inst.instId || '')].filter(Boolean))];
    const rooms = [...new Set([...(prev?._publicDuplicateRoomIds || []), ...roomIdsFor(cell)])].sort();
    out[idx] = {
      ...prev,
      _publicDuplicateInstIds: ids,
      _publicDuplicateRoomIds: rooms,
      _publicDuplicateConflict: ids.length > 1,
      _publicDuplicateRoomConflict: rooms.length > 1,
    };
  }
  return out;
}
