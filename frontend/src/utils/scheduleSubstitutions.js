/**
 * Утилиты для работы с заменами, переносами занятий (moved substitutions),
 * свободными парами и расширенными характеристиками аудиторий.
 */

export function canonicalId(val) {
  let id = String(val || '');
  const rx = /__(?:PUBW\d+(?:_\d+)*|EXACT_W\d+(?:_\d+)*|TRANSFER_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|CANONICAL_RECOVER_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|REMOTE_ONE_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)$/i;
  while (rx.test(id)) {
    id = id.replace(rx, '');
  }
  return id;
}

export function subMatchesInstance(sub, inst) {
  const subIds = [sub?.instId, sub?.sourceInstId, sub?.originalInstId]
    .map(canonicalId)
    .filter(Boolean);
  if (!subIds.length) return false;
  const instIds = [inst?.instId, inst?._publishedSourceInstId, inst?.id, inst?._originalInstId]
    .map(canonicalId)
    .filter(Boolean);
  return instIds.some(id => subIds.includes(id));
}

export function subMatchesView(sub, inst, kind, viewId) {
  const vid = String(viewId || '');
  if (!vid) return false;
  if (kind === 'group') {
    const gids = [inst?.groupId, ...(inst?.groupIds || []), ...(inst?.streamGroupIds || []), sub?.groupId, sub?.importGroupId].filter(Boolean).map(String);
    return gids.includes(vid);
  }
  if (kind === 'teacher') {
    const tids = [sub?.newTeacherId, sub?.teacherId, inst?.teacherId, sub?.importOldTeacherId, sub?.importNewTeacherId].filter(Boolean).map(String);
    return tids.includes(vid);
  }
  if (kind === 'room') {
    if (sub?.newFormat === 'remote') return false;
    const rids = [sub?.newRoomId, sub?.importNewRoom, inst?.roomId, inst?.dedicatedRoomId, sub?.importOldRoom, inst?.assignment?.roomId].filter(Boolean).map(String);
    return rids.includes(vid);
  }
  return true;
}

export function formatDateDM(isoDate) {
  if (!isoDate) return '';
  const parts = String(isoDate).split('-');
  if (parts.length < 3) return String(isoDate);
  return `${parts[2]}.${parts[1]}`;
}

function resolveSubject(sub, rawInst, subjects) {
  const subjName = sub.importSubject || '';
  const found = subjects.find(s => String(s.name || '').trim().toLowerCase() === subjName.trim().toLowerCase());
  return {
    subjectId: rawInst?.subjectId || found?.id || '',
    subjectName: rawInst?.subjectName || subjName
  };
}

function resolveMovedSlot(sub, rawInst) {
  const period = Number.isFinite(Number(sub.targetPeriod)) ? Number(sub.targetPeriod) + 1 : Number(rawInst?.period || 1);
  const roomId = sub.newFormat === 'remote' ? '' : (sub.newRoomId || sub.importNewRoom || rawInst?.roomId || sub.importOldRoom || '');
  const sourcePeriod = rawInst?.period ? Number(rawInst.period) - 1 : Math.max(0, (Number(sub.importOldPeriod) || 1) - 1);
  return { period, roomId, sourcePeriod };
}

function createMovedSubOverride(sub, slot) {
  return {
    ...sub,
    type: 'moved',
    isMovedTarget: true,
    moved: true,
    sourceDate: sub.date,
    sourcePeriod: slot.sourcePeriod,
    note: sub.note || 'Перенос занятия'
  };
}

export function buildMovedInstance(sub, rawInst, dayIdx, dateISO, subjects = []) {
  const subj = resolveSubject(sub, rawInst, subjects);
  const slot = resolveMovedSlot(sub, rawInst);
  const teacherId = sub.newTeacherId || rawInst?.teacherId || sub.importOldTeacherId || sub.teacherId || '';
  const groupId = rawInst?.groupId || sub.importGroupId || sub.groupId || '';
  const subg = Number(rawInst?.subgroup || sub.subgroup || (sub.importSubgroup ? Number(sub.importSubgroup) : 0));
  return {
    ...(rawInst || {}),
    instId: `moved-${sub.id || sub.instId || Math.random()}`,
    _isMovedSynthetic: true,
    day: dayIdx,
    period: slot.period,
    subjectId: subj.subjectId,
    subjectName: subj.subjectName,
    teacherId,
    groupId,
    groupIds: rawInst?.groupIds || (groupId ? [groupId] : []),
    streamGroupIds: rawInst?.streamGroupIds || [],
    subgroup: subg,
    format: sub.newFormat || rawInst?.format || 'inperson',
    roomId: slot.roomId,
    sourceDate: sub.date,
    sourcePeriod: slot.sourcePeriod,
    targetDate: sub.targetDate,
    targetPeriod: sub.targetPeriod,
    note: sub.note || 'Перенос занятия',
    substitution: createMovedSubOverride(sub, slot)
  };
}

export function collectMovedLessonsForDate(substitutions, instances, kind, viewId, targetDate, dayIdx, subjects = []) {
  if (!targetDate || !Array.isArray(substitutions)) return [];
  const list = [];
  for (const sub of substitutions) {
    if (!['moved', 'excursion'].includes(sub?.type) || String(sub.targetDate) !== String(targetDate)) continue;
    const rawInst = (instances || []).find(i => subMatchesInstance(sub, i));
    if (!subMatchesView(sub, rawInst, kind, viewId)) continue;
    list.push(buildMovedInstance(sub, rawInst, dayIdx, targetDate, subjects));
  }
  return list;
}

export function calculateFreePeriods(occupiedPeriods, periodsPerDay = 7) {
  const maxP = Math.max(1, Number(periodsPerDay) || 7);
  const occupiedSet = new Set((occupiedPeriods || []).map(Number));
  const free = [];
  for (let p = 1; p <= maxP; p += 1) {
    if (!occupiedSet.has(p)) free.push(p);
  }
  return free;
}

export function calculateDayGaps(occupiedPeriods) {
  const nums = [...new Set((occupiedPeriods || []).map(Number))].sort((a, b) => a - b);
  if (nums.length < 2) return [];
  const gaps = [];
  for (let p = nums[0] + 1; p < nums[nums.length - 1]; p += 1) {
    if (!nums.includes(p)) gaps.push(p);
  }
  return gaps;
}

function resolveRoomTypeCategory(room, typeObj) {
  const typeName = String(typeObj?.name || '').toLowerCase();
  const roomName = String(room?.name || '').toLowerCase();
  const isComputer = Boolean(room?.hasComputers || typeName.includes('комп') || roomName.includes('комп'));
  const isArt = Boolean(room?.isArtRoom || typeName.includes('худож') || roomName.includes('худож'));
  const isLab = Boolean(typeName.includes('лабор') || roomName.includes('лабор'));
  const isGym = Boolean(typeName.includes('спорт') || roomName.includes('спорт') || typeName.includes('зал'));
  return { isComputer, isArt, isLab, isGym };
}

function resolveRoomSizeLabel(sizeObj, capacity = 0) {
  const sizeName = String(sizeObj?.name || '').toLowerCase();
  if (sizeName.includes('бол') || (!sizeName && capacity >= 35)) return 'большая';
  if (sizeName.includes('сред') || (!sizeName && capacity >= 18)) return 'средняя';
  if (sizeName.includes('мал') || (!sizeName && capacity > 0)) return 'малая';
  return sizeName || 'большая';
}

function resolveMeaningfulTypeLabel(cats, typeObj, roomName = '') {
  if (cats.isGym) return 'спортзал';
  if (cats.isComputer) return 'компьютерная';
  if (cats.isArt) return 'художественная';
  if (cats.isLab) return 'лаборатория';
  const name = String(typeObj?.name || '').trim().toLowerCase();
  if (name.includes('актов')) return 'актовый зал';
  return 'обычная';
}

function buildRoomTagLabel(sizeLabel, typeLabel) {
  const parts = [];
  if (sizeLabel) parts.push(sizeLabel);
  if (typeLabel) parts.push(typeLabel);
  return parts.join(' · ') || (sizeLabel || 'обычная');
}

export function getRoomCategoryDetails(room, roomTypes = [], roomSizes = []) {
  if (!room) return { tagLabel: '', description: '', isArt: false, isComputer: false, isLab: false };
  const rName = String(room.name || '');
  const typeObj = (roomTypes || []).find(t => String(t.id) === String(room.typeId));
  const sizeObj = (roomSizes || []).find(s => String(s.id) === String(room.sizeId));
  const cats = resolveRoomTypeCategory(room, typeObj);
  const sizeLabel = resolveRoomSizeLabel(sizeObj, room.capacity || 0);
  const typeLabel = resolveMeaningfulTypeLabel(cats, typeObj, rName);
  const tagLabel = buildRoomTagLabel(sizeLabel, typeLabel);
  const mainTypeTitle = typeObj?.name || (cats.isArt ? 'Художественный кабинет' : (cats.isComputer ? 'Компьютерный класс' : 'Аудитория'));
  const sizeDesc = sizeLabel ? `${sizeLabel} (${room.capacity || 0} мест)` : (room.capacity ? `${room.capacity} мест` : '');
  const descParts = [mainTypeTitle, sizeDesc, room.equipment].filter(Boolean);
  return { tagLabel, description: descParts.join(' · '), ...cats, sizeLabel, capacity: room.capacity || 0 };
}

function keyForCell(inst, sub) {
  const teacherId = sub?.newTeacherId || inst?.teacherId || '';
  const period = Number(inst?.period ?? -1);
  return [period, inst?.groupId || '', teacherId, inst?.subjectId || '', inst?.lessonTypeId || inst?.typeId || '', inst?.format || ''].join('|');
}

export function collapsePublicSiblingSubgroups(cells = []) {
  const out = [], consumed = new Set();
  for (let i = 0; i < cells.length; i += 1) {
    if (consumed.has(i)) continue;
    const c = cells[i], inst = c?.inst || c, sub = c?.substitution || inst?.substitution || {};
    const sg = Number(inst?.subgroup || 0);
    if (!sub?.cancelled && !sub?.isCancelled && sg > 0) {
      const k = keyForCell(inst, sub);
      const sibIdx = cells.findIndex((other, j) => {
        if (j <= i || consumed.has(j)) return false;
        const oInst = other?.inst || other, oSub = other?.substitution || oInst?.substitution || {};
        const osg = Number(oInst?.subgroup || 0);
        return !oSub?.cancelled && !oSub?.isCancelled && osg > 0 && osg !== sg && keyForCell(oInst, oSub) === k;
      });
      if (sibIdx >= 0) {
        consumed.add(sibIdx);
        const oInst = cells[sibIdx]?.inst || cells[sibIdx];
        out.push({ ...(c || {}), _publicCombinedSubgroups: [sg, Number(oInst?.subgroup || 0)].sort((a, b) => a - b) });
        continue;
      }
    }
    out.push(c);
  }
  return out;
}

export function publicChangeDetails(sub, inst, data = {}) {
  if (!sub) return [];
  const details = [], teachers = data.teachers || [], rooms = data.rooms || [];
  if (sub.moved || sub.isMovedTarget) details.push({ type: 'move', label: 'Перенос занятия' });
  const oldT = (teachers.find(t => String(t.id) === String(inst?.teacherId)) || {}).name || '';
  const newT = (teachers.find(t => String(t.id) === String(sub.newTeacherId)) || {}).name || '';
  if (newT && oldT && newT !== oldT) details.push({ type: 'teacher', label: `Замена преподавателя: ${oldT} → ${newT}` });
  else if (newT && !oldT) details.push({ type: 'teacher', label: `Преподаватель: ${newT}` });
  const oldR = (rooms.find(r => String(r.id) === String(inst?.roomId)) || {}).name || '';
  const newR = (rooms.find(r => String(r.id) === String(sub.newRoomId)) || {}).name || '';
  if (newR && oldR && newR !== oldR) details.push({ type: 'room', label: `Смена кабинета: ауд. ${oldR} → ауд. ${newR}` });
  if (sub.newFormat === 'remote') details.push({ type: 'format', label: 'Изменение формата: дистанционно' });
  if (sub.joinedSubgroup) details.push({ type: 'teacher', label: 'Замена преподавателя · объединение подгрупп' });
  return details;
}

