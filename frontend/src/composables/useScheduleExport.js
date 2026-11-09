/**
 * Экспорт расписания в формат CSV и печать расписания.
 */

function formatLessonRow(inst, dayName, period, getSubject, getTeacher, getRoom) {
  const room = inst.roomId || inst.dedicatedRoomId || inst.preferredRoomIds?.[0] || '';
  return {
    'День недели': dayName,
    'Пара': period,
    'Дисциплина': getSubject ? getSubject(inst.subjectId) : (inst.subjectId || ''),
    'Преподаватель': getTeacher ? getTeacher(inst.teacherId) : (inst.teacherId || ''),
    'Аудитория': getRoom ? getRoom(room) : room,
    'Подгруппа': inst.subgroup ? `${inst.subgroup} п/г` : 'Вся группа'
  };
}

function sanitizeCSVCell(rawVal) {
  let val = String(rawVal ?? '');
  // Защита от CSV Formula Injection (CWE-1236):
  if (/^[=+\-@\t\r]/.test(val)) {
    val = `'${val}`;
  }
  return `"${val.replace(/"/g, '""')}"`;
}

function convertToCSV(rows) {
  if (!rows.length) return '';
  const headers = Object.keys(rows[0]);
  const lines = [headers.map(sanitizeCSVCell).join(';')];
  for (const row of rows) {
    const values = headers.map(h => sanitizeCSVCell(row[h]));
    lines.push(values.join(';'));
  }
  return lines.join('\r\n');
}

function sanitizeFileName(name) {
  return String(name || 'КТМУ').replace(/[/\\?%*:|"<>]/g, '_').trim();
}

function downloadFile(content, fileName, mimeType) {
  const blob = new Blob(['\ufeff' + content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function useScheduleExport() {
  function exportScheduleCSV(instances, entityName, getSubject, getTeacher, getRoom) {
    if (!Array.isArray(instances) || !instances.length) {
      alert('Нет занятий для экспорта на текущую неделю');
      return;
    }
    const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const rows = instances.map(inst => {
      const d = inst.day !== undefined ? inst.day : (inst.blockIndex || 0);
      return formatLessonRow(inst, dayNames[d] || '', inst.period || 1, getSubject, getTeacher, getRoom);
    });
    const csv = convertToCSV(rows);
    const safeName = sanitizeFileName(entityName);
    downloadFile(csv, `Расписание_${safeName}.csv`, 'text/csv;charset=utf-8;');
  }

  function printSchedule() {
    window.print();
  }

  return { exportScheduleCSV, printSchedule };
}

export {
  formatLessonRow,
  sanitizeCSVCell,
  convertToCSV,
  sanitizeFileName,
  downloadFile
};
