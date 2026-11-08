<template>
  <div class="subs-editor">
    <div class="editor-header">
      <h3>Оперативное управление заменами</h3>
      <p>Создание замен преподавателей, аудиторий и предметов на учебные дни.</p>
    </div>

    <!-- Форма добавления замены -->
    <div class="add-sub-box">
      <h4>Назначить новую замену</h4>
      
      <div v-if="validationError" class="validation-error-banner" role="alert">
        {{ validationError }}
      </div>

      <div class="form-grid">
        <div class="field">
          <label for="sub-group-select">Группа</label>
          <select id="sub-group-select" v-model="form.groupId" class="admin-select">
            <option value="">Все группы</option>
            <option v-for="g in groups" :key="g.id" :value="g.id">{{ g.name }}</option>
          </select>
        </div>

        <div class="field">
          <label for="sub-day-select">День недели</label>
          <select id="sub-day-select" v-model.number="form.day" class="admin-select">
            <option v-for="(day, idx) in DAYS" :key="idx" :value="idx">{{ day }}</option>
          </select>
        </div>

        <div class="field">
          <label for="sub-period-select">Номер пары</label>
          <select id="sub-period-select" v-model.number="form.period" class="admin-select">
            <option v-for="p in 6" :key="p" :value="p">Пара {{ p }}</option>
          </select>
        </div>

        <div class="field">
          <label for="sub-teacher-select">Новый преподаватель</label>
          <select id="sub-teacher-select" v-model="form.newTeacherId" class="admin-select">
            <option value="">Без изменений</option>
            <option v-for="t in teachers" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
        </div>

        <div class="field">
          <label for="sub-room-select">Новая аудитория</label>
          <select id="sub-room-select" v-model="form.newRoomId" class="admin-select">
            <option value="">Без изменений</option>
            <option v-for="r in rooms" :key="r.id" :value="r.id">{{ r.name || r.number }}</option>
          </select>
        </div>

        <div class="field">
          <label for="sub-note-input">Примечание к замене</label>
          <input 
            id="sub-note-input"
            v-model.trim="form.note" 
            type="text" 
            placeholder="Например: Больничный преподавателя" 
            class="admin-input"
            @keydown.enter="submitAdd"
            @input="validationError = null"
          />
        </div>
      </div>

      <button type="button" @click="submitAdd" class="add-btn">
        + Сохранить замену
      </button>
    </div>

    <!-- Список существующих замен -->
    <div class="current-subs-list">
      <h4>Действующие замены ({{ substitutions.length }})</h4>
      <div v-if="!substitutions.length" class="empty-hint">
        Замен пока нет.
      </div>
      <div v-else class="subs-table-wrap">
        <table class="subs-table">
          <thead>
            <tr>
              <th>Группа</th>
              <th>День и пара</th>
              <th>Преподаватель</th>
              <th>Аудитория</th>
              <th>Примечание</th>
              <th>Действие</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="sub in substitutions" :key="sub.id">
              <td><b>{{ getGroupName(sub.groupId) }}</b></td>
              <td>{{ DAYS[sub.day] || '—' }}, пара {{ sub.period }}</td>
              <td>{{ getTeacherName(sub.newTeacherId) }}</td>
              <td>{{ getRoomName(sub.newRoomId) }}</td>
              <td>{{ sub.note || '—' }}</td>
              <td>
                <button 
                  type="button" 
                  class="del-btn"
                  title="Удалить замену"
                  @click="onRemove(sub.id)"
                >
                  Удалить
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue';

const DAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];

const props = defineProps({
  substitutions: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] },
  teachers: { type: Array, default: () => [] },
  rooms: { type: Array, default: () => [] },
  subjects: { type: Array, default: () => [] }
});

const emit = defineEmits(['add-sub', 'remove-sub']);

const validationError = ref(null);

const form = reactive({
  groupId: '',
  day: 0,
  period: 1,
  newTeacherId: '',
  newRoomId: '',
  note: ''
});

function submitAdd() {
  if (!form.newTeacherId && !form.newRoomId && !form.note) {
    validationError.value = 'Пожалуйста, укажите нового преподавателя, аудиторию или напишите примечание.';
    return;
  }
  validationError.value = null;
  emit('add-sub', { ...form });
  form.newTeacherId = '';
  form.newRoomId = '';
  form.note = '';
}

function onRemove(id) {
  emit('remove-sub', id);
}

function getGroupName(id) {
  if (!id) return 'Все группы';
  const g = props.groups.find(item => String(item.id) === String(id));
  return g ? g.name : String(id);
}

function getTeacherName(id) {
  if (!id) return 'Без изменений';
  const t = props.teachers.find(item => String(item.id) === String(id));
  return t ? t.name : String(id);
}

function getRoomName(id) {
  if (!id) return 'Без изменений';
  const r = props.rooms.find(item => String(item.id) === String(id));
  return r ? (r.name || r.number || String(id)) : String(id);
}
</script>

<style scoped>
.subs-editor {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.editor-header h3 {
  font-size: 18px;
  font-weight: 800;
  color: var(--ink-primary);
}

.editor-header p {
  font-size: 13px;
  color: var(--ink-muted);
}

.add-sub-box {
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 20px;
  box-shadow: var(--shadow-subtle);
}

.add-sub-box h4, .current-subs-list h4 {
  font-size: 15px;
  font-weight: 700;
  color: var(--blue-700);
  margin-bottom: 14px;
}

.validation-error-banner {
  background: var(--orange-50);
  border: 1px solid var(--orange-200);
  color: var(--orange-700);
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 12px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.field label {
  font-size: 11px;
  font-weight: 700;
  color: var(--ink-secondary);
}

.admin-select, .admin-input {
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-light);
  font-size: 13px;
  outline: none;
}

.admin-select:focus, .admin-input:focus {
  border-color: var(--blue-600);
}

.add-btn {
  padding: 10px 18px;
  background: var(--blue-600);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.2s;
}

.add-btn:hover {
  background: var(--blue-700);
}

.subs-table-wrap {
  overflow-x: auto;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 12px;
}

.subs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.subs-table th, .subs-table td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border-light);
}

.subs-table th {
  background: var(--bg-app);
  font-weight: 700;
  color: var(--ink-muted);
  font-size: 11px;
  text-transform: uppercase;
}

.del-btn {
  padding: 4px 10px;
  background: var(--orange-50);
  color: var(--orange-700);
  border: 1px solid var(--orange-200);
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
}

.del-btn:hover {
  background: var(--orange-500);
  color: #fff;
}
</style>
