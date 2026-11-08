<template>
  <SpatialCard :maxTilt="5" class="lesson-card-wrapper">
    <div :class="['lesson-item', { 'has-substitution': hasSub, 'is-cancelled': isCancelled, 'is-event': isEvent }]">
      <!-- Плашка замены / отмены -->
      <div v-if="hasSub" class="sub-alert-bar">
        <span class="sub-icon">{{ isCancelled ? '🚫' : '⚡' }}</span>
        <span class="sub-title">{{ isCancelled ? 'ОТМЕНА' : 'ЗАМЕНА' }}</span>
        <span v-if="subDetails" class="sub-note">{{ subDetails }}</span>
      </div>

      <!-- Заголовок пары: Звонки/Время и бейджи (тип, подгруппа) -->
      <div class="lesson-meta-top">
        <div class="time-slot-badge" :class="{ 'event-slot': isEvent }">
          <template v-if="isEvent">
            <span v-if="periodTime" class="slot-time">{{ periodTime }}</span>
            <span v-if="periodTime" class="slot-separator">·</span>
            <span class="slot-name">Мероприятие</span>
          </template>
          <template v-else>
            <span v-if="periodTime" class="slot-time">{{ periodTime }}</span>
            <span v-if="periodTime" class="slot-separator">·</span>
            <span class="slot-name">{{ period }} пара</span>
          </template>
        </div>

        <div class="badges-row">
          <span v-if="lessonType" :class="['type-badge', typeBadgeClass]">
            {{ lessonType }}
          </span>
          <span v-if="isRemote" class="format-badge remote" title="Дистанционное занятие">
            Дистант
          </span>
          <span v-if="instance?.subgroup" class="subgroup-badge">
            {{ instance.subgroup }} подгруппа
          </span>
          <span 
            v-if="effectiveGroups.length > 1 && role === 'group'" 
            class="stream-badge" 
            :title="`Потоковое занятие для групп: ${streamGroupsTitle}`"
          >
            Поток
          </span>
        </div>
      </div>

      <!-- Отдельная плашка площадки (Вознесенский пр., 44 / Садовая) -->
      <div v-if="locationBadge && !isRemote" class="special-flags-row">
        <span class="location-badge" :title="`Корпус/площадка: ${locationBadge}`">
          {{ locationBadge }}
        </span>
      </div>

      <!-- Название дисциплины / Мероприятия -->
      <h4 :class="['subject-title', { 'cancelled-text': isCancelled }]">
        {{ effectiveSubject }}
      </h4>

      <!-- Подвал карточки: Группы / Преподаватель / Аудитория -->
      <div class="lesson-footer">
        <!-- Режим ПРЕПОДАВАТЕЛЬ: отображаем группу (или группы потока) с возможностью перехода -->
        <div v-if="role === 'teacher'" class="groups-info">
          <svg class="group-icon-svg" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M7 14s-1 0-1-1 1-4 5-4 5 3 5 4-1 1-1 1H7zm4-6a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm-5.784 6A2.238 2.238 0 0 1 5 13c0-1.355.68-2.75 1.936-3.72A6.325 6.325 0 0 0 5 9c-4 0-5 3-5 4s1 1 1 1h4.216zM4.5 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"/>
          </svg>
          <div class="groups-chips-list">
            <button
              v-for="grp in effectiveGroups"
              :key="grp.id"
              type="button"
              class="group-tag-btn"
              :title="`Перейти к расписанию группы ${grp.name}`"
              @click.stop="handleGroupClick(grp.id)"
            >
              {{ grp.name }}
            </button>
            <span v-if="!effectiveGroups.length" class="no-group-text">Группа не указана</span>
          </div>
        </div>

        <!-- Режим АУДИТОРИЯ: группа + преподаватель -->
        <div v-else-if="role === 'room'" class="room-entity-meta">
          <div class="groups-chips-list">
            <button
              v-for="grp in effectiveGroups"
              :key="grp.id"
              type="button"
              class="group-tag-btn"
              :title="`Перейти к расписанию группы ${grp.name}`"
              @click.stop="handleGroupClick(grp.id)"
            >
              {{ grp.name }}
            </button>
          </div>
          <button
            v-if="effectiveTeacherId"
            type="button"
            class="teacher-info teacher-btn"
            :title="`Перейти к расписанию преподавателя: ${effectiveTeacher}`"
            @click.stop="handleTeacherClick"
          >
            <svg class="teacher-icon-svg" viewBox="0 0 16 16" width="12" height="12" fill="currentColor" aria-hidden="true">
              <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm4 8c0-2.21-3.58-3.5-6-3.5S2 10.79 2 13v1h12v-1zm-1.2 0H3.2c.45-1.21 2.5-2.3 4.8-2.3s4.35 1.09 4.8 2.3z"/>
            </svg>
            <span class="teacher-name">{{ effectiveTeacher }}</span>
          </button>
        </div>

        <!-- Режим ГРУППА: преподаватель с переходом -->
        <template v-else>
          <button
            v-if="effectiveTeacherId"
            type="button"
            class="teacher-info teacher-btn"
            :title="`Перейти к расписанию преподавателя: ${effectiveTeacher}`"
            @click.stop="handleTeacherClick"
          >
            <svg class="teacher-icon-svg" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
              <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm4 8c0-2.21-3.58-3.5-6-3.5S2 10.79 2 13v1h12v-1zm-1.2 0H3.2c.45-1.21 2.5-2.3 4.8-2.3s4.35 1.09 4.8 2.3z"/>
            </svg>
            <span class="teacher-name">{{ effectiveTeacher }}</span>
          </button>
          <div v-else class="teacher-info" :title="effectiveTeacher">
            <svg class="teacher-icon-svg" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
              <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm4 8c0-2.21-3.58-3.5-6-3.5S2 10.79 2 13v1h12v-1zm-1.2 0H3.2c.45-1.21 2.5-2.3 4.8-2.3s4.35 1.09 4.8 2.3z"/>
            </svg>
            <span class="teacher-name">{{ effectiveTeacher }}</span>
          </div>
        </template>

        <div 
          v-if="formattedRoom && !isRemote" 
          :class="['room-chip', { 'room-substituted': hasRoomSub }]"
          :title="`Аудитория: ${effectiveRoom}`"
        >
          <span class="room-name">{{ formattedRoom }}</span>
        </div>
      </div>
    </div>
  </SpatialCard>
</template>

<script setup>
import { computed } from 'vue';
import SpatialCard from '../common/SpatialCard.vue';

const props = defineProps({
  role: { type: String, default: 'group' },
  instance: { type: Object, required: true },
  period: { type: Number, required: true },
  subjectName: { type: String, default: '' },
  teacherName: { type: String, default: '' },
  roomName: { type: String, default: '' },
  substitution: { type: Object, default: null },
  getGroupName: { type: Function, default: null },
  getTeacherName: { type: Function, default: null },
  getRoomName: { type: Function, default: null },
  getRoomLocation: { type: Function, default: () => '' },
  getSubjectName: { type: Function, default: null },
  getLessonTypeName: { type: Function, default: () => '' },
  getPeriodTime: { type: Function, default: () => '' }
});

const emit = defineEmits(['select-teacher', 'select-group']);

const effectiveTeacherId = computed(() => {
  return props.substitution?.newTeacherId || props.instance?.teacherId || '';
});

function handleTeacherClick() {
  if (effectiveTeacherId.value) {
    emit('select-teacher', effectiveTeacherId.value);
  }
}

function handleGroupClick(groupId) {
  if (groupId) {
    emit('select-group', groupId);
  }
}

const effectiveGroups = computed(() => {
  const list = [];
  const addGid = (gid) => {
    if (gid && !list.some(g => String(g.id) === String(gid))) {
      const name = props.getGroupName ? props.getGroupName(gid) : String(gid);
      list.push({ id: gid, name: name || String(gid) });
    }
  };
  if (props.instance?.groupId) addGid(props.instance.groupId);
  if (Array.isArray(props.instance?.streamGroupIds)) {
    props.instance.streamGroupIds.forEach(addGid);
  }
  if (Array.isArray(props.instance?.streamParticipants)) {
    props.instance.streamParticipants.forEach(p => addGid(p?.groupId));
  }
  return list;
});

const streamGroupsTitle = computed(() => {
  return effectiveGroups.value.map(g => g.name).join(', ');
});

const hasSub = computed(() => Boolean(props.substitution));

const isCancelled = computed(() => {
  const type = props.substitution?.type;
  return type === 'cancel' || type === 'cancel_assignment';
});

const isEvent = computed(() => {
  return Boolean(props.instance?.isScheduleEvent || props.instance?.manualEvent || props.instance?.eventTitle);
});

const eventTime = computed(() => {
  const inst = props.instance;
  if (!inst) return '';
  return [inst.eventStartTime, inst.eventEndTime].filter(Boolean).join('–') || inst.time || '';
});

const effectiveRoomId = computed(() => {
  if (props.substitution?.newRoomId) return props.substitution.newRoomId;
  return props.instance?.roomId || props.instance?.dedicatedRoomId || props.instance?.preferredRoomIds?.[0] || '';
});

const locationBadge = computed(() => {
  if (isRemote.value) return '';
  if (props.getRoomLocation) {
    const loc = props.getRoomLocation(effectiveRoomId.value, effectiveRoom.value);
    if (loc) return loc;
  }
  const lower = String(effectiveRoom.value || '').toLowerCase();
  if (lower.includes('садов') || lower.startsWith('с-')) return '↗ ул. Садовая, 54';
  if (lower.startsWith('в-') || lower.includes('возн')) return '↗ Вознесенский пр., 44';
  return '';
});

const periodTime = computed(() => {
  if (isEvent.value && eventTime.value) return eventTime.value;
  if (!props.getPeriodTime) return '';
  return props.getPeriodTime(props.period, {
    groupId: props.instance?.groupId,
    dayIndex: props.instance?.day
  });
});

const hasRoomSub = computed(() => Boolean(props.substitution?.newRoomId));

const effectiveSubject = computed(() => {
  if (isEvent.value) {
    return props.instance?.eventTitle || (props.instance?.eventKind === 'courses' ? 'Курсы' : 'Мероприятие');
  }
  if (props.substitution?.newSubjectId && props.getSubjectName) {
    return props.getSubjectName(props.substitution.newSubjectId);
  }
  return props.subjectName || 'Дисциплина без названия';
});

const effectiveTeacher = computed(() => {
  if (props.substitution?.newTeacherId && props.getTeacherName) {
    return props.getTeacherName(props.substitution.newTeacherId);
  }
  return props.teacherName || 'Преподаватель не назначен';
});

const effectiveRoom = computed(() => {
  if (props.substitution?.newRoomId && props.getRoomName) {
    return props.getRoomName(props.substitution.newRoomId);
  }
  return props.roomName || '';
});

const isRemote = computed(() => {
  const format = props.instance?.format || props.substitution?.newFormat;
  if (format === 'remote' || format === 'distance' || format === 'dist') return true;
  const rawRoom = String(effectiveRoom.value || props.roomName || '').toLowerCase();
  if (rawRoom.includes('дистант') || rawRoom === 'до' || rawRoom.includes('онлайн')) return true;
  const subj = String(effectiveSubject.value || '').toLowerCase();
  return subj.includes('(до)') || subj.includes('дистант');
});

function formatRoomLabel(rawRoom) {
  if (isRemote.value || !rawRoom) return '';
  const text = String(rawRoom).trim();
  const lower = text.toLowerCase();
  if (lower.includes('дистант') || lower === 'до' || lower.includes('онлайн') || lower.includes('уточн')) {
    return '';
  }
  if (lower.includes('зал') || lower.includes('ауд') || lower.includes('каб')) {
    return text;
  }
  return `Ауд. ${text}`;
}

const formattedRoom = computed(() => {
  if (isRemote.value) return '';
  return formatRoomLabel(effectiveRoom.value);
});

function formatLessonTypeName(rawName) {
  if (!rawName) return '';
  const lower = String(rawName).trim().toLowerCase();
  if (lower === 'лек' || lower === 'лекция') return 'Лекция';
  if (lower === 'пр' || lower === 'практика') return 'Практика';
  if (lower === 'лаб' || lower === 'лабораторная') return 'Лабораторная';
  if (lower === 'зч' || lower === 'зачёт' || lower === 'зачет') return 'Зачёт';
  if (lower === 'зчо' || lower.includes('диф')) return 'Диф. зачёт';
  if (lower.includes('контр')) return 'Контрольная';
  if (lower.includes('курс')) return 'Курсовая';
  if (lower.includes('проект')) return 'Индивидуальный проект';
  return rawName;
}

const lessonType = computed(() => {
  if (isEvent.value) return 'Мероприятие';
  const typeId = props.instance?.typeId;
  const rawType = typeId && props.getLessonTypeName ? props.getLessonTypeName(typeId) : '';
  return formatLessonTypeName(rawType);
});

function resolveTypeClass(name) {
  const lower = String(name || '').toLowerCase();
  if (lower.startsWith('лек')) return 'type-lecture';
  if (lower.startsWith('пр')) return 'type-practice';
  if (lower.startsWith('лаб')) return 'type-lab';
  if (lower.includes('зач') || lower.includes('зч')) return 'type-credit';
  return 'type-other';
}

const typeBadgeClass = computed(() => resolveTypeClass(lessonType.value));

const subDetails = computed(() => {
  if (!props.substitution) return '';
  if (props.substitution.note) return props.substitution.note;
  if (isCancelled.value) return 'Занятие отменено';
  if (props.substitution.newTeacherId) return 'Замена преподавателя';
  if (props.substitution.newRoomId) return 'Смена аудитории';
  if (props.substitution.newSubjectId) return 'Замена предмета';
  return 'Изменение в расписании';
});
</script>

<style scoped>
.lesson-card-wrapper {
  margin-bottom: 10px;
}

.lesson-item {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-left: 4px solid var(--blue-600);
  background: #ffffff;
  width: 100%;
  box-sizing: border-box;
  min-width: 0;
  overflow: hidden;
}

.lesson-item.is-event {
  border-left-color: #8b5cf6;
}

.lesson-item.has-substitution {
  border-left-color: var(--orange-500);
  background: var(--orange-50);
}

.lesson-item.is-cancelled {
  border-left-color: #ef4444;
  background: #fef2f2;
}

.sub-alert-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--orange-500);
  color: #ffffff;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.03em;
}

.lesson-item.is-cancelled .sub-alert-bar {
  background: #ef4444;
}

.sub-icon {
  font-size: 12px;
}

.sub-title {
  font-weight: 800;
}

.sub-note {
  font-weight: 500;
  opacity: 0.95;
  margin-left: auto;
  font-size: 10px;
}

.lesson-meta-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  width: 100%;
  min-width: 0;
}

.time-slot-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--blue-700);
  background: var(--blue-100);
  padding: 3px 8px;
  border-radius: 6px;
  max-width: 100%;
}

.time-slot-badge.event-slot {
  background: #f3e8ff;
  color: #7e22ce;
  border: 1px solid #e9d5ff;
}

.slot-time {
  font-family: var(--font-mono);
  font-weight: 800;
  color: var(--blue-800);
}

.slot-separator {
  opacity: 0.45;
  font-weight: 400;
}

.slot-name {
  font-weight: 400;
  opacity: 0.85;
}

.special-flags-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  min-width: 0;
  flex-wrap: wrap;
}

.badges-row {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.type-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 5px;
  letter-spacing: 0.01em;
}

.type-lecture {
  background: #e0f2fe;
  color: #0369a1;
  border: 1px solid #bae6fd;
}

.type-practice {
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
}

.type-lab {
  background: #fff7ed;
  color: #c2410c;
  border: 1px solid #fed7aa;
}

.type-credit {
  background: #fef3c7;
  color: #b45309;
  border: 1px solid #fde68a;
}

.type-other {
  background: #f1f5f9;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.format-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 5px;
}

.format-badge.remote {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}

.format-badge.inperson {
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.location-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10.5px;
  font-weight: 700;
  color: #245c8b;
  background: #ddeeff;
  border: 1px solid #a9cce9;
  border-radius: 999px;
  padding: 2px 8px;
  line-height: 1.2;
  letter-spacing: 0.01em;
  white-space: nowrap;
  box-shadow: 0 1px 2px rgba(36, 92, 139, 0.06);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.subgroup-badge {
  font-size: 10px;
  font-weight: 600;
  color: var(--ink-secondary);
  background: var(--border-light);
  padding: 2px 6px;
  border-radius: 5px;
  white-space: nowrap;
}

.stream-badge {
  font-size: 10px;
  font-weight: 700;
  color: #7e22ce;
  background: #f3e8ff;
  border: 1px solid #e9d5ff;
  padding: 2px 6px;
  border-radius: 5px;
  white-space: nowrap;
}

.subject-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink-primary);
  line-height: 1.35;
  word-break: break-word;
  overflow-wrap: anywhere;
  hyphens: auto;
}

.subject-title.cancelled-text {
  text-decoration: line-through;
  opacity: 0.7;
}

.lesson-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 4px;
  width: 100%;
  min-width: 0;
}

.groups-info {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.group-icon-svg {
  color: var(--blue-700);
  flex-shrink: 0;
  opacity: 0.85;
}

.groups-chips-list {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  min-width: 0;
}

.group-tag-btn {
  display: inline-flex;
  align-items: center;
  padding: 2.5px 8px;
  background: var(--blue-50);
  border: 1px solid var(--blue-200);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 800;
  color: var(--blue-700);
  cursor: pointer;
  transition: all 0.16s ease;
  white-space: nowrap;
}

.group-tag-btn:hover {
  background: var(--blue-600);
  color: #ffffff;
  border-color: var(--blue-600);
  transform: translateY(-1px);
}

.no-group-text {
  font-size: 11px;
  color: var(--ink-muted);
  font-style: italic;
}

.room-entity-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.teacher-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.teacher-btn {
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  text-align: left;
  border-radius: 4px;
  transition: opacity 0.15s ease;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.teacher-name {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.teacher-btn:hover .teacher-name {
  color: var(--blue-700);
  text-decoration: underline;
}

.teacher-btn:focus-visible {
  outline: 2px solid var(--blue-500);
  outline-offset: 2px;
}

.teacher-icon-svg {
  flex-shrink: 0;
  color: var(--ink-secondary);
  opacity: 0.8;
}

.room-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  color: var(--blue-700);
  white-space: nowrap;
  flex-shrink: 0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.room-chip.room-substituted {
  border-color: var(--orange-500);
  color: var(--orange-700);
  background: var(--orange-100);
}

@media (max-width: 480px) {
  .lesson-item {
    padding: 10px 12px;
    gap: 6px;
  }
  .time-slot-badge {
    font-size: 10px;
    padding: 2.5px 6px;
  }
  .slot-time {
    font-size: 10.5px;
  }
  .type-badge {
    font-size: 10px;
    padding: 1.5px 5px;
  }
  .subject-title {
    font-size: 13px;
  }
  .teacher-info {
    font-size: 11px;
  }
  .room-chip {
    font-size: 10.5px;
    padding: 2px 6px;
  }
}
</style>
