<template>
  <SpatialCard :maxTilt="5" class="lesson-card-wrapper">
    <div :class="['lesson-item', { 'has-substitution': hasSub, 'is-cancelled': isCancelled, 'is-event': isEvent }]">
      <!-- Плашка замены / отмены / переноса -->
      <div v-if="hasSub" :class="['sub-alert-bar', subAlertClass]">
        <svg v-if="isMoved" class="sub-icon-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="9 14 4 9 9 4"></polyline>
          <path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
        </svg>
        <svg v-else-if="isCancelled" class="sub-icon-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
        </svg>
        <svg v-else class="sub-icon-svg" viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
          <path d="M13 2L3 14h7v8l10-12h-7z"/>
        </svg>
        <span class="sub-title">{{ subAlertTitle }}</span>
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
          <span v-if="isRemote" class="format-badge remote" title="Дистанционное занятие">
            <svg class="badge-icon-remote" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
              <line x1="8" y1="21" x2="16" y2="21"></line>
              <line x1="12" y1="17" x2="12" y2="21"></line>
            </svg>
            <span>Дистант</span>
          </span>
          <span v-if="subgroupBadge" class="subgroup-badge">
            {{ subgroupBadge }}
          </span>
          <span 
            v-if="effectiveGroups.length > 1" 
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
        <span class="subject-icon">🎓</span>
        {{ displaySubjectTitle }}
        <span v-if="subgroupBadge" class="subgroup-inline"> ({{ subgroupBadge }})</span>
        <span v-if="weekNumbersText" class="week-numbers-inline"> · {{ weekNumbersText }}</span>
        <span v-if="isStream && effectiveGroups.length <= 1" class="stream-inline"> · 🔗 поток</span>
      </h4>

      <!-- Строка пояснения переноса / деталей изменений -->
      <div v-if="changeDetails.length" class="change-details-list">
        <div v-for="(ch, idx) in changeDetails" :key="idx" class="source-move-row">
          <span class="source-move-arrow">↪</span>
          <span class="source-move-text">{{ ch.label }}</span>
        </div>
        <div v-if="sourceMoveText && !hasSourceMoveInDetails" class="source-move-row">
          <span class="source-move-arrow">↪</span>
          <span class="source-move-text">{{ sourceMoveText }}</span>
        </div>
      </div>
      <div v-else-if="sourceMoveText" class="source-move-row">
        <span class="source-move-arrow">↪</span>
        <span class="source-move-text">{{ sourceMoveText }}</span>
      </div>

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
          <button
            v-if="hasTeacherSub && effectiveTeacherId"
            type="button"
            class="teacher-info teacher-btn replacement-teacher-btn"
            :title="`Замена преподавателя: ${effectiveTeacher}`"
            @click.stop="handleTeacherClick"
          >
            <svg class="teacher-icon-svg" viewBox="0 0 16 16" width="12" height="12" fill="currentColor" aria-hidden="true">
              <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm4 8c0-2.21-3.58-3.5-6-3.5S2 10.79 2 13v1h12v-1zm-1.2 0H3.2c.45-1.21 2.5-2.3 4.8-2.3s4.35 1.09 4.8 2.3z"/>
            </svg>
            <span class="teacher-name">Замена: {{ effectiveTeacher }}</span>
          </button>
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
        <template v-else-if="hasRealTeacher">
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
          v-if="formattedRoom && !isRemote && !isCancelled" 
          :class="['room-chip', { 'room-substituted': hasRoomSub }]"
          :title="`Аудитория: ${effectiveRoom}${roomCategoryBadge ? ` (${roomCategoryBadge})` : ''}`"
        >
          <span class="room-name">📍 {{ formattedRoom }}</span>
          <span v-if="roomCategoryBadge" class="room-tag-mini">{{ roomCategoryBadge }}</span>
        </div>
        <div v-else-if="isRemote" class="room-chip room-remote" title="Дистанционное занятие">
          <span class="room-name">📍 дистанционно</span>
        </div>

        <!-- Нижний бейдж статуса замены/переноса (по скриншоту 1: ПЕРЕНОС) -->
        <span 
          v-if="statusBadgeLabel" 
          :class="['sub-badge', statusBadgeClass]"
        >
          {{ statusBadgeLabel }}
        </span>
      </div>
    </div>
  </SpatialCard>
</template>

<script setup>
import { computed } from 'vue';
import SpatialCard from '../common/SpatialCard.vue';
import { weekNumbersLabel } from '../../fastScheduleLookups.js';
import { formatDateDM, publicChangeDetails } from '../../utils/scheduleSubstitutions.js';

const props = defineProps({
  role: { type: String, default: 'group' },
  instance: { type: Object, required: true },
  period: { type: Number, required: true },
  dateISO: { type: String, default: '' },
  subjectName: { type: String, default: '' },
  teacherName: { type: String, default: '' },
  roomName: { type: String, default: '' },
  substitution: { type: Object, default: null },
  semesterConfig: { type: Object, default: () => ({}) },
  teachers: { type: Array, default: () => [] },
  rooms: { type: Array, default: () => [] },
  getGroupName: { type: Function, default: null },
  getTeacherName: { type: Function, default: null },
  getRoomName: { type: Function, default: null },
  getRoomDetails: { type: Function, default: null },
  getRoomLocation: { type: Function, default: () => '' },
  getSubjectName: { type: Function, default: null },
  getLessonTypeName: { type: Function, default: () => '' },
  getPeriodTime: { type: Function, default: () => '' }
});

const emit = defineEmits(['select-teacher', 'select-group']);

const effectiveTeacherId = computed(() => {
  return props.substitution?.newTeacherId || props.substitution?.teacherId || props.instance?.teacherId || '';
});

const hasTeacherSub = computed(() => {
  const newT = props.substitution?.newTeacherId || props.substitution?.teacherId;
  return Boolean(newT && String(newT) !== String(props.instance?.teacherId || ''));
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
  if (Array.isArray(props.instance?.groupIds)) {
    props.instance.groupIds.forEach(addGid);
  }
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

const isMovedTarget = computed(() => {
  if (props.instance?._isMovedSynthetic || props.substitution?.isMovedTarget) return true;
  if (props.substitution?.type === 'moved' && props.substitution?.targetDate) {
    return String(props.substitution.targetDate) === String(props.dateISO);
  }
  return false;
});

const isMovedAway = computed(() => {
  return Boolean(props.substitution?.type === 'moved' && !isMovedTarget.value);
});

const isMoved = computed(() => isMovedTarget.value || isMovedAway.value);

const isCancelled = computed(() => {
  if (isMovedTarget.value) return false;
  const type = props.substitution?.type;
  return ['cancel', 'cancel_assignment', 'pending_move', 'moved', 'excursion'].includes(type);
});

const subAlertTitle = computed(() => {
  if (isMoved.value) return 'ПЕРЕНОС';
  if (isCancelled.value) return 'ОТМЕНА';
  return 'ЗАМЕНА';
});

const subAlertClass = computed(() => {
  if (isMoved.value) return 'alert-moved';
  if (isCancelled.value) return 'alert-cancelled';
  return 'alert-substitution';
});

const sourceMoveText = computed(() => {
  if (!isMovedTarget.value) return '';
  const srcDate = props.instance?.sourceDate || props.substitution?.sourceDate;
  if (!srcDate) return '';
  const dateFormatted = formatDateDM(srcDate);
  const srcP = props.instance?.sourcePeriod !== undefined ? Number(props.instance.sourcePeriod) : props.substitution?.sourcePeriod;
  const periodText = Number.isFinite(Number(srcP)) ? ` · ${Number(srcP) + 1} пары` : '';
  return `Перенесено с ${dateFormatted}${periodText}`;
});

const roomCategoryBadge = computed(() => {
  if (isRemote.value || !effectiveRoomId.value || !props.getRoomDetails) return '';
  const details = props.getRoomDetails(effectiveRoomId.value);
  return details?.tagLabel || '';
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
    dayIndex: props.instance?.day,
    dateStr: props.dateISO,
    roomId: effectiveRoomId.value
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

const displaySubjectTitle = computed(() => {
  const s = String(effectiveSubject.value || '').trim();
  const t = String(lessonType.value || '').trim();
  return t ? `${s}, ${t}` : s;
});

const effectiveTeacher = computed(() => {
  if (props.substitution?.newTeacherId && props.getTeacherName) {
    return props.getTeacherName(props.substitution.newTeacherId);
  }
  return props.teacherName || '';
});

const hasRealTeacher = computed(() => {
  const t = String(effectiveTeacher.value || '').trim();
  return Boolean(t && t !== 'Преподаватель не назначен' && t !== '—');
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
  if (lower.includes('зал') || lower.includes('ауд') || lower.includes('каб') || lower.includes('бассейн') || lower.includes('мастерск')) {
    return text;
  }
  return `ауд. ${text}`;
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

function resolveMovedAwayText(sub) {
  const targetDate = formatDateDM(sub.targetDate);
  const targetP = Number.isFinite(Number(sub.targetPeriod)) ? `${Number(sub.targetPeriod) + 1} пара` : '';
  const details = [targetDate ? `на ${targetDate}` : '', targetP].filter(Boolean).join(' · ');
  return details ? `Перенесено ${details}` : 'Перенос занятия';
}

const subDetails = computed(() => {
  if (!props.substitution) return '';
  if (isMovedAway.value) return resolveMovedAwayText(props.substitution);
  if (isMovedTarget.value) return props.substitution.note || 'Перенос занятия';
  if (props.substitution.note) return props.substitution.note;
  if (props.substitution.type === 'cancel_assignment') return 'Отменено с выдачей задания';
  if (props.substitution.type === 'pending_move') return 'Отменено · перенос будет назначен позже';
  if (props.substitution.type === 'excursion') {
    return props.role === 'teacher' ? 'Отменено · у группы экскурсия' : 'Мероприятие: Экскурсия';
  }
  if (isCancelled.value) return 'Занятие отменено';
  if (props.substitution.newTeacherId) return 'Замена преподавателя';
  if (props.substitution.newRoomId) return 'Смена аудитории';
  if (props.substitution.newSubjectId) return 'Замена предмета';
  return 'Изменение в расписании';
});

const weekNumbersText = computed(() => {
  if (props.role === 'group') return '';
  const inst = props.instance;
  if (!inst?.weekPattern || inst.weekPattern === 'all') return '';
  return weekNumbersLabel(props.semesterConfig, inst);
});

const isStream = computed(() => {
  const inst = props.instance;
  return Boolean((inst?.streamGroupIds || []).length > 0 || (inst?.streamParticipants || []).length > 1);
});

const subgroupBadge = computed(() => {
  const combined = props.instance?._publicCombinedSubgroups;
  if (Array.isArray(combined) && combined.length > 1) {
    return `Подгруппы ${combined.join(', ')}`;
  }
  const sg = Number(props.instance?.subgroup || 0);
  return sg > 0 ? `п/г ${sg}` : '';
});

const changeDetails = computed(() => {
  const sub = props.substitution || props.instance?.substitution;
  if (!sub) return [];
  return publicChangeDetails(sub, props.instance, {
    teachers: props.teachers,
    rooms: props.rooms
  });
});

const hasSourceMoveInDetails = computed(() => {
  return changeDetails.value.some(ch => String(ch.label).startsWith('Перенесено с'));
});

const statusBadgeInfo = computed(() => {
  const sub = props.substitution || props.instance?.substitution;
  if (!sub) return null;
  if (isMovedTarget.value || sub.moved || sub.isMovedTarget) {
    return { label: 'ПЕРЕНОС', class: 'moved' };
  }
  if (sub.excursion || sub.type === 'excursion') {
    return { label: props.role === 'teacher' ? 'ОТМЕНА · ЭКСКУРСИЯ' : 'ЭКСКУРСИЯ', class: 'cancel' };
  }
  if (sub.movedAway || isMovedAway.value) {
    return { label: 'ПЕРЕНОС', class: 'moved' };
  }
  if (sub.pendingMove || sub.type === 'pending_move') {
    return { label: 'ПЕРЕНОС', class: 'pending' };
  }
  if (sub.assignmentIssued || sub.type === 'cancel_assignment') {
    return { label: 'ВЫДАЧА ЗАДАНИЯ', class: 'assignment' };
  }
  if (isCancelled.value || sub.cancelled) {
    return { label: 'ОТМЕНА', class: 'cancel' };
  }
  if (hasTeacherSub.value) return { label: 'ЗАМЕНА', class: 'replacement' };
  if (hasRoomSub.value) return { label: 'СМЕНА КАБИНЕТА', class: 'room-change' };
  return { label: 'ИЗМЕНЕНИЕ', class: 'default' };
});

const statusBadgeLabel = computed(() => statusBadgeInfo.value?.label || '');
const statusBadgeClass = computed(() => statusBadgeInfo.value?.class || '');
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

.sub-alert-bar.alert-moved {
  background: #f59e0b;
  color: #ffffff;
}

.lesson-item.is-cancelled .sub-alert-bar {
  background: #ef4444;
}

.lesson-item.is-cancelled .sub-alert-bar.alert-moved {
  background: #f59e0b;
}

.source-move-row {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  color: #b45309;
  background: #fef3c7;
  padding: 3px 8px;
  border-radius: 6px;
  width: fit-content;
  margin-top: 2px;
}

.source-move-arrow {
  font-weight: 700;
  color: #d97706;
}

.room-tag-mini {
  font-size: 11px;
  font-weight: 500;
  color: #475569;
  background: #e2e8f0;
  padding: 1.5px 7px;
  border-radius: 5px;
  margin-left: 4px;
  display: inline-flex;
  align-items: center;
}

.sub-icon-svg {
  flex-shrink: 0;
  display: inline-block;
  vertical-align: middle;
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
  font-size: 11.5px;
  color: #0284c7;
  background: #e0f2fe;
  padding: 3px 9px;
  border-radius: 8px;
  max-width: 100%;
}

.time-slot-badge.event-slot {
  background: #f3e8ff;
  color: #7e22ce;
  border: 1px solid #e9d5ff;
}

.slot-time {
  font-family: var(--font-mono);
  font-weight: 700;
  color: #0284c7;
}

.slot-separator {
  opacity: 0.45;
  font-weight: 400;
}

.slot-name {
  font-weight: 500;
  color: #0284c7;
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
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.01em;
  padding: 2.5px 8px;
  border-radius: 6px;
  border: 1px solid #bfdbfe;
}

.room-chip.room-remote {
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  color: #0284c7;
  font-weight: 600;
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
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 4px;
  width: 100%;
  min-width: 0;
}

.groups-info {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 120px;
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
  flex: 0 1 auto;
  min-width: 0;
  flex-wrap: wrap;
}

.teacher-info {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--ink-secondary);
  flex: 0 0 auto;
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
  flex: 0 0 auto;
  min-width: 0;
}

.teacher-name {
  display: inline-block;
  font-weight: 600;
  color: var(--ink-secondary);
  white-space: nowrap;
  line-height: 1.25;
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
  gap: 6px;
  padding: 3.5px 9px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #0284c7;
  white-space: nowrap;
  flex-shrink: 0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
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
.change-details-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 2px;
}

.week-numbers-inline,
.stream-inline {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-secondary, #64748b);
}

.subject-icon {
  font-size: 14px;
  margin-right: 4px;
}

.room-chip.room-remote {
  background: #f0f9ff;
  border: 1px solid #bae6fd;
}

.room-chip.room-remote .room-name {
  color: #0284c7;
  font-weight: 600;
}

.sub-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 4px;
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: var(--blue-600, #2563eb);
  color: #fff;
  padding: 3px 8px;
  border-radius: 999px;
  width: fit-content;
}

.sub-badge.moved {
  background: #d49b13 !important;
  color: #ffffff !important;
}

.sub-badge.cancel {
  background: #c94a4a !important;
  color: #ffffff !important;
}

.sub-badge.replacement {
  background: #d49b13 !important;
  color: #ffffff !important;
}

.sub-badge.assignment {
  background: #8d5d24 !important;
  color: #ffffff !important;
}

.sub-badge.pending {
  background: #d49b13 !important;
  color: #ffffff !important;
}

.sub-badge.room-change {
  background: #0284c7 !important;
  color: #ffffff !important;
}
</style>
