<template>
  <div class="substitutions-feed">
    <div v-if="!substitutions.length" class="empty-subs">
      <svg class="empty-icon-svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="#16a34a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
        <polyline points="22 4 12 14.01 9 11.01"></polyline>
      </svg>
      <p>Активных замен нет — занятия проводятся по основному расписанию.</p>
    </div>

    <div v-else class="subs-grid">
      <div 
        v-for="sub in substitutions" 
        :key="sub.id || `${sub.groupId}-${sub.day}-${sub.period}`"
        :class="['sub-card', { 'is-cancelled': sub.type === 'cancel' }]"
      >
        <div class="sub-header">
          <div class="sub-header-left">
            <span class="group-tag">{{ resolveSubGroup(sub) }}</span>
            <span :class="['sub-tag-status', resolveStatusClass(sub.type)]">
              {{ resolveStatusLabel(sub.type) }}
            </span>
          </div>
          <div class="sub-meta-right">
            <span v-if="sub.date" class="sub-date-badge">{{ sub.date }}</span>
            <span v-if="sub.period" class="period-tag">Пара {{ sub.period }}</span>
          </div>
        </div>

        <div class="sub-change-body">
          <div v-if="sub.targetDate" class="change-row moved-row">
            <span class="change-label">Перенос:</span>
            <span class="change-value">на {{ sub.targetDate }} (пара {{ (Number(sub.targetPeriod) || 0) + 1 }})</span>
          </div>
          <div v-if="sub.newTeacherId" class="change-row">
            <span class="change-label">Преподаватель:</span>
            <span class="change-value">{{ getTeacherName(sub.newTeacherId) }}</span>
          </div>

          <div v-if="sub.newRoomId" class="change-row">
            <span class="change-label">Аудитория:</span>
            <span class="change-value">{{ getRoomName(sub.newRoomId) }}</span>
          </div>

          <div v-if="sub.newSubjectId" class="change-row">
            <span class="change-label">Предмет:</span>
            <span class="change-value">{{ getSubjectName(sub.newSubjectId) }}</span>
          </div>

          <div v-if="sub.type === 'cancel' && (!sub.note || !sub.note.trim())" class="sub-note-text cancel-note">
            Занятие отменено
          </div>
          <div v-else-if="sub.note && sub.note.trim()" :class="['sub-note-text', { 'cancel-note': sub.type === 'cancel' }]">
            {{ sub.note.trim() }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  substitutions: { type: Array, default: () => [] },
  getGroupName: { type: Function, required: true },
  getTeacherName: { type: Function, required: true },
  getRoomName: { type: Function, required: true },
  getSubjectName: { type: Function, required: true }
});

function resolveSubGroup(sub) {
  if (sub.groupId && props.getGroupName) {
    const name = props.getGroupName(sub.groupId);
    if (name) return name;
  }
  return sub.importGroupName || 'Все группы';
}

function resolveStatusLabel(type) {
  if (type === 'cancel') return 'ОТМЕНА';
  if (type === 'cancel_assignment') return 'ВЫДАЧА ЗАДАНИЯ';
  if (type === 'pending_move') return 'ОТЛОЖЕННЫЙ ПЕРЕНОС';
  if (type === 'moved') return 'ПЕРЕНОС';
  if (type === 'excursion') return 'ЭКСКУРСИЯ';
  return 'ЗАМЕНА';
}

function resolveStatusClass(type) {
  if (type === 'cancel' || type === 'cancel_assignment' || type === 'pending_move') return 'cancel';
  if (type === 'moved') return 'moved';
  if (type === 'excursion') return 'excursion';
  return 'change';
}
</script>

<style scoped>
.substitutions-feed {
  display: flex;
  flex-direction: column;
}

.empty-subs {
  text-align: center;
  padding: 30px 16px;
  background: var(--blue-50);
  border-radius: var(--radius-md);
  border: 1px dashed var(--blue-200);
  color: var(--blue-700);
  font-size: 13px;
}

.empty-icon {
  font-size: 24px;
  display: block;
  margin-bottom: 6px;
}

.subs-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sub-card {
  background: #ffffff;
  border-left: 4px solid var(--orange-500);
  border: 1px solid var(--border-light);
  border-left-width: 4px;
  border-radius: var(--radius-md);
  padding: 14px 16px;
  box-shadow: var(--shadow-subtle);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.sub-card.is-cancelled {
  border-left-color: #ef4444;
  background: #fffafa;
}

.sub-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.sub-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.group-tag {
  font-size: 13px;
  font-weight: 800;
  color: var(--orange-700);
  background: var(--orange-100);
  padding: 3px 8px;
  border-radius: 6px;
}

.sub-card.is-cancelled .group-tag {
  color: #b91c1c;
  background: #fee2e2;
}

.sub-tag-status {
  font-size: 10px;
  font-weight: 800;
  padding: 2px 7px;
  border-radius: 6px;
  letter-spacing: 0.04em;
}

.sub-tag-status.cancel {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.sub-tag-status.change {
  background: var(--orange-100);
  color: var(--orange-700);
  border: 1px solid var(--orange-200);
}

.sub-tag-status.moved {
  background: #fef3c7;
  color: #b45309;
  border: 1px solid #fde68a;
}

.sub-tag-status.excursion {
  background: #f3e8ff;
  color: #7e22ce;
  border: 1px solid #e9d5ff;
}

.sub-meta-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sub-date-badge {
  font-size: 11px;
  font-weight: 700;
  color: var(--blue-700);
  background: var(--blue-50);
  padding: 2px 6px;
  border-radius: 4px;
}

.period-tag {
  font-size: 11px;
  font-weight: 700;
  color: var(--ink-secondary);
}

.sub-change-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}

.change-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.change-label {
  color: var(--ink-muted);
  font-size: 12px;
}

.change-value {
  font-weight: 700;
  color: var(--ink-primary);
}

.sub-note-text {
  margin-top: 4px;
  font-size: 11px;
  color: var(--ink-muted);
  background: var(--bg-app);
  padding: 6px 10px;
  border-radius: 6px;
  line-height: 1.4;
}

.sub-note-text.cancel-note {
  color: #b91c1c;
  background: #fef2f2;
  font-weight: 700;
  border: 1px solid #fee2e2;
}
</style>
