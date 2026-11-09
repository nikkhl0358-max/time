<template>
  <div class="entity-picker-wrap">
    <!-- Фоновый центральный оранжевый круг-градиент -->
    <div class="ambient-orange-orb" aria-hidden="true"></div>
    <div class="ambient-orange-glow" aria-hidden="true"></div>

    <!-- Выбор преподавателя -->
    <template v-if="role === 'teacher'">
      <div class="picker-header">
        <h2 class="picker-title">Преподаватели колледжа</h2>
        <p class="picker-subtitle">Поиск по фамилии и распределение по цикловым комиссиям (ЦК)</p>
      </div>

      <!-- Панель инструментов: Поиск по фамилии -->
      <div class="teacher-toolbar">
        <div class="search-input-box">
          <svg class="search-icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input
            v-model="searchQuery"
            type="text"
            placeholder="Поиск по фамилии или имени преподавателя..."
            class="teacher-search-input"
            aria-label="Поиск преподавателя"
          />
          <button
            v-if="searchQuery"
            type="button"
            class="clear-search-btn"
            @click="searchQuery = ''"
            title="Очистить поиск"
            aria-label="Очистить"
          >
            ✕
          </button>
        </div>

        <!-- Табы фильтрации по ЦК -->
        <div class="dept-tabs-scroll" aria-label="Фильтр по ЦК">
          <button
            type="button"
            :class="['dept-tab-btn', { active: !activeDepartmentId }]"
            @click="activeDepartmentId = ''"
          >
            Все ЦК ({{ totalTeachersCount }})
          </button>
          <button
            v-for="dept in availableDepartments"
            :key="dept.id"
            type="button"
            :class="['dept-tab-btn', { active: activeDepartmentId === dept.id }]"
            @click="activeDepartmentId = activeDepartmentId === dept.id ? '' : dept.id"
          >
            {{ dept.shortName }} ({{ dept.count }})
          </button>
        </div>

        <!-- Быстрый алфавитный фильтр -->
        <div class="alphabet-bar" aria-label="Алфавитный указатель">
          <button
            v-for="letter in availableLetters"
            :key="letter"
            type="button"
            :class="['letter-btn', { active: activeLetter === letter }]"
            @click="activeLetter = activeLetter === letter ? '' : letter"
          >
            {{ letter }}
          </button>
        </div>
      </div>

      <!-- Режим А: Результаты поиска или выбранный отдельный ЦК -->
      <div v-if="searchQuery || activeDepartmentId || activeLetter" class="filtered-results-wrap">
        <div class="results-stats">
          <span>Найдено преподавателей: <strong>{{ filteredTeachers.length }}</strong></span>
          <button v-if="searchQuery || activeDepartmentId || activeLetter" class="reset-filters-btn" @click="resetAllFilters">
            Сбросить фильтры
          </button>
        </div>

        <div v-if="filteredTeachers.length" class="teachers-grid">
          <div
            v-for="t in filteredTeachers"
            :key="t.id"
            class="teacher-card"
            role="button"
            tabindex="0"
            :aria-label="`Преподаватель ${t.name}`"
            @click="$emit('select', t.id)"
            @keydown.enter="$emit('select', t.id)"
            @keydown.space.prevent="$emit('select', t.id)"
          >
            <div class="teacher-card-main">
              <span class="teacher-card-name">{{ t.name }}</span>
              <span v-if="t.profile" class="teacher-card-profile">{{ t.profile }}</span>
            </div>
            <span class="teacher-dept-tag">{{ resolveDeptBadge(t.departmentId) }}</span>
          </div>
        </div>
        <div v-else class="no-teachers-found">
          <p>По вашему запросу преподавателей не найдено</p>
        </div>
      </div>

      <!-- Режим Б: Группировка по всем ЦК (по умолчанию) -->
      <div v-else class="dept-sections-list">
        <div
          v-for="deptGroup in teachersGroupedByDept"
          :key="deptGroup.id"
          class="dept-section-block"
        >
          <div class="dept-section-header">
            <span class="dept-header-badge">ЦК</span>
            <div class="dept-header-info">
              <h3 class="dept-header-title">{{ deptGroup.name }}</h3>
              <span class="dept-header-count">{{ deptGroup.teachers.length }} преп.</span>
            </div>
          </div>

          <div class="teachers-grid">
            <div
              v-for="t in deptGroup.teachers"
              :key="t.id"
              class="teacher-card"
              role="button"
              tabindex="0"
              :aria-label="`Преподаватель ${t.name}`"
              @click="$emit('select', t.id)"
              @keydown.enter="$emit('select', t.id)"
              @keydown.space.prevent="$emit('select', t.id)"
            >
              <div class="teacher-card-main">
                <span class="teacher-card-name">{{ t.name }}</span>
                <span v-if="t.profile" class="teacher-card-profile">{{ t.profile }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Выбор аудитории -->
    <template v-else-if="role === 'room'">
      <div class="picker-header">
        <h2 class="picker-title">Аудиторный фонд</h2>
        <p class="picker-subtitle">Выберите корпус и кабинет колледжа</p>
      </div>

      <!-- Табы переключения корпусов -->
      <div class="campus-tabs-bar" aria-label="Переключение корпусов">
        <button
          type="button"
          :class="['dept-tab-btn', { active: !activeCampus }]"
          @click="activeCampus = ''"
        >
          Все корпуса ({{ totalRoomsCount }})
        </button>
        <button
          v-for="c in campusGroups"
          :key="c.badge"
          type="button"
          :class="['dept-tab-btn', { active: activeCampus === c.badge }]"
          @click="activeCampus = activeCampus === c.badge ? '' : c.badge"
        >
          {{ c.badge }} ({{ c.rooms.length }})
        </button>
      </div>

      <div class="campuses-list">
        <div 
          v-for="campus in displayedCampusGroups" 
          :key="campus.title" 
          class="campus-block"
        >
          <div class="campus-header">
            <span class="campus-badge">{{ campus.badge }}</span>
            <h3 class="campus-title">{{ campus.title }}</h3>
          </div>
          <div class="rooms-chips-grid">
            <button
              v-for="r in campus.rooms"
              :key="r.id"
              type="button"
              class="room-pick-btn"
              @click="$emit('select', r.id)"
            >
              {{ r.name }}
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';

const props = defineProps({
  role: { type: String, default: 'teacher' },
  items: { type: Array, default: () => [] },
  departments: { type: Array, default: () => [] }
});

defineEmits(['select']);

const searchQuery = ref('');
const activeDepartmentId = ref('');
const activeLetter = ref('');
const activeCampus = ref('');

const totalTeachersCount = computed(() => {
  return props.role === 'teacher' ? props.items.length : 0;
});

const totalRoomsCount = computed(() => {
  return props.role === 'room' ? props.items.length : 0;
});

function extractShortDept(fullName) {
  const name = String(fullName || '');
  const matchNum = name.match(/\((\d{2})\)/);
  if (matchNum) return `ЦК ${matchNum[1]}`;
  if (name.includes('кафедр')) return 'Кафедра';
  if (name.includes('гуманитарных')) return 'ЦК 01 (Гуманитарные)';
  if (name.includes('математических')) return 'ЦК 02 (Математика)';
  if (name.includes('Дизайн')) return 'ЦК Дизайн';
  return name.slice(0, 18);
}

const deptNameMap = computed(() => {
  const map = new Map();
  props.departments.forEach(d => {
    if (d && d.id) map.set(String(d.id), String(d.name || ''));
  });
  return map;
});

function resolveDeptBadge(deptId) {
  if (!deptId) return 'Общие';
  const full = deptNameMap.value.get(String(deptId));
  return full ? extractShortDept(full) : 'ЦК';
}

const availableDepartments = computed(() => {
  if (props.role !== 'teacher') return [];
  const counts = new Map();
  props.items.forEach(t => {
    const dId = (t.departmentId && deptNameMap.value.has(String(t.departmentId))) ? String(t.departmentId) : 'unassigned';
    counts.set(dId, (counts.get(dId) || 0) + 1);
  });
  const list = [];
  props.departments.forEach(d => {
    const c = counts.get(String(d.id)) || 0;
    if (c > 0) list.push({ id: d.id, name: d.name, shortName: extractShortDept(d.name), count: c });
  });
  const unassignedCount = counts.get('unassigned') || 0;
  if (unassignedCount > 0) {
    list.push({ id: 'unassigned', name: 'Общие / Общеколледжные', shortName: 'Общие', count: unassignedCount });
  }
  return list;
});

// Кэшированный отсортированный список преподавателей для устранения повторной сортировки при поиске
const sortedTeachers = computed(() => {
  if (props.role !== 'teacher') return [];
  return [...props.items].sort((a, b) => (a.name || '').localeCompare(b.name || '', 'ru'));
});

const availableLetters = computed(() => {
  if (props.role !== 'teacher') return [];
  const set = new Set();
  sortedTeachers.value.forEach(t => {
    let first = (t.name || '').trim().charAt(0).toUpperCase();
    if (first === 'Ё') first = 'Е';
    if (first && /[А-Я]/.test(first)) set.add(first);
  });
  return Array.from(set).sort((a, b) => a.localeCompare(b, 'ru'));
});

function matchTeacherQuery(t, query) {
  if (!query) return true;
  const q = query.toLowerCase().replace(/ё/g, 'е').trim();
  const name = (t.name || '').toLowerCase().replace(/ё/g, 'е');
  const prof = (t.profile || '').toLowerCase().replace(/ё/g, 'е');
  return name.includes(q) || prof.includes(q);
}

function matchTeacherLetter(t, letter) {
  if (!letter) return true;
  let first = (t.name || '').trim().charAt(0).toUpperCase();
  if (first === 'Ё') first = 'Е';
  return first === letter;
}

function matchTeacherDept(t, deptId) {
  if (!deptId) return true;
  const tDept = (t.departmentId && deptNameMap.value.has(String(t.departmentId))) ? String(t.departmentId) : 'unassigned';
  return tDept === String(deptId);
}

const filteredTeachers = computed(() => {
  if (props.role !== 'teacher') return [];
  const deptId = activeDepartmentId.value;
  const letter = activeLetter.value;
  const query = searchQuery.value;
  if (!deptId && !letter && !query) return sortedTeachers.value;
  return sortedTeachers.value.filter(t => 
    matchTeacherDept(t, deptId) &&
    matchTeacherLetter(t, letter) &&
    matchTeacherQuery(t, query)
  );
});

const teachersGroupedByDept = computed(() => {
  if (props.role !== 'teacher') return [];
  const deptBuckets = new Map();
  sortedTeachers.value.forEach(t => {
    const dId = (t.departmentId && deptNameMap.value.has(String(t.departmentId))) ? String(t.departmentId) : 'unassigned';
    if (!deptBuckets.has(dId)) deptBuckets.set(dId, []);
    deptBuckets.get(dId).push(t);
  });
  const groups = [];
  availableDepartments.value.forEach(dept => {
    const list = deptBuckets.get(String(dept.id));
    if (list && list.length > 0) {
      groups.push({ id: dept.id, name: dept.name, shortName: dept.shortName, teachers: list });
    }
  });
  return groups;
});

function resetAllFilters() {
  searchQuery.value = '';
  activeDepartmentId.value = '';
  activeLetter.value = '';
  activeCampus.value = '';
}

function sortRoomsAlphabetical(a, b) {
  return String(a.name).localeCompare(String(b.name), 'ru', { numeric: true });
}

const campusGroups = computed(() => {
  if (props.role !== 'room') return [];
  const mainRooms = [];
  const voznesenskyRooms = [];
  const sadovayaRooms = [];

  props.items.forEach(r => {
    const name = String(r.name || '').toLowerCase();
    if (r.isExternal || name.startsWith('в-') || name.includes('возн')) {
      voznesenskyRooms.push(r);
    } else if (name.startsWith('с-') || name.includes('садов')) {
      sadovayaRooms.push(r);
    } else {
      mainRooms.push(r);
    }
  });

  return [
    { title: 'ул. Цветочная, 8 (Главный корпус)', badge: 'ЦВЕТОЧНАЯ', rooms: mainRooms.sort(sortRoomsAlphabetical) },
    { title: 'Вознесенский пр., 44', badge: 'ВОЗНЕСЕНСКИЙ', rooms: voznesenskyRooms.sort(sortRoomsAlphabetical) },
    { title: 'ул. Садовая, 54', badge: 'САДОВАЯ', rooms: sadovayaRooms.sort(sortRoomsAlphabetical) }
  ].filter(c => c.rooms.length > 0);
});

const displayedCampusGroups = computed(() => {
  if (!activeCampus.value) return campusGroups.value;
  return campusGroups.value.filter(c => c.badge === activeCampus.value);
});
</script>

<style scoped>
.entity-picker-wrap {
  position: relative;
  width: 100%;
  max-width: 1240px;
  margin: 0 auto;
  min-height: 60vh;
  overflow-x: clip;
}

.ambient-orange-orb {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(640px, 88vw);
  height: min(640px, 88vw);
  border-radius: 50%;
  background: radial-gradient(
    circle,
    #f97316 0%,
    #ea580c 38%,
    rgba(234, 88, 12, 0.45) 58%,
    rgba(249, 115, 22, 0.15) 74%,
    transparent 85%
  );
  filter: blur(52px);
  opacity: 0.45;
  pointer-events: none;
  z-index: 0;
}

.ambient-orange-glow {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(940px, 98vw);
  height: min(940px, 98vw);
  border-radius: 50%;
  background: radial-gradient(
    circle,
    rgba(251, 146, 60, 0.35) 0%,
    rgba(254, 215, 170, 0.16) 45%,
    transparent 75%
  );
  filter: blur(80px);
  opacity: 0.55;
  pointer-events: none;
  z-index: 0;
}

.picker-header {
  position: relative;
  z-index: 2;
  margin-bottom: 20px;
}

.picker-title {
  font-size: 24px;
  font-weight: 800;
  color: var(--ink-primary);
  margin-bottom: 4px;
}

.picker-subtitle {
  font-size: 14px;
  color: var(--ink-muted);
}

.teacher-toolbar {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-bottom: 24px;
}

.search-input-box {
  position: relative;
  width: 100%;
  max-width: 600px;
  display: flex;
  align-items: center;
}

.search-icon-svg {
  position: absolute;
  left: 14px;
  color: var(--ink-muted);
  pointer-events: none;
}

.teacher-search-input {
  width: 100%;
  padding: 12px 38px 12px 42px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-primary);
  background: rgba(255, 255, 255, 0.94);
  -webkit-backdrop-filter: blur(12px);
  backdrop-filter: blur(12px);
  border: 1px solid var(--border-light);
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
  transition: all 0.2s ease;
  box-sizing: border-box;
}

.teacher-search-input:focus {
  outline: none;
  border-color: #f97316;
  box-shadow: 0 0 0 3px rgba(249, 115, 22, 0.18);
}

.clear-search-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--ink-muted);
  font-size: 16px;
  cursor: pointer;
  padding: 8px;
  min-width: 36px;
  min-height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
}

@media (max-width: 768px) {
  .teacher-search-input {
    font-size: 16px;
  }
}

.dept-tabs-scroll {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 6px;
  scrollbar-width: thin;
}

.dept-tab-btn {
  padding: 7px 14px;
  border: 1px solid var(--border-light);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.9);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-secondary);
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.15s ease;
}

.dept-tab-btn:hover {
  background: var(--blue-50);
  color: var(--blue-700);
  border-color: var(--blue-400);
}

.dept-tab-btn.active {
  background: var(--blue-600);
  color: #ffffff;
  border-color: var(--blue-600);
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.25);
}

.alphabet-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.letter-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  font-weight: 700;
  font-size: 13px;
  color: var(--ink-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.letter-btn:hover {
  border-color: var(--blue-500);
  color: var(--blue-700);
  background: var(--blue-50);
}

.letter-btn.active {
  background: var(--blue-600);
  color: #ffffff;
  border-color: var(--blue-600);
}

.filtered-results-wrap {
  position: relative;
  z-index: 2;
}

.results-stats {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  font-size: 13px;
  color: var(--ink-muted);
}

.reset-filters-btn {
  background: none;
  border: none;
  color: var(--blue-700);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  text-decoration: underline;
}

.teachers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
}

.teacher-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.92);
  -webkit-backdrop-filter: blur(14px);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.85);
  border-radius: 12px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
  transition: all 0.2s ease;
}

.teacher-card:hover {
  border-color: #f97316;
  box-shadow: 0 8px 22px -4px rgba(234, 88, 12, 0.22);
  transform: translateY(-2px);
}

.teacher-card-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.teacher-card-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink-primary);
  word-break: break-word;
  overflow-wrap: anywhere;
}

.teacher-card-profile {
  font-size: 11.5px;
  color: var(--ink-muted);
  line-height: 1.35;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.teacher-dept-tag {
  align-self: flex-start;
  font-size: 10.5px;
  font-weight: 700;
  color: var(--blue-700);
  background: var(--blue-50);
  padding: 2px 7px;
  border-radius: 6px;
  border: 1px solid var(--blue-200);
}

.dept-sections-list {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.dept-section-block {
  background: rgba(255, 255, 255, 0.84);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.03);
}

.dept-section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.dept-header-badge {
  font-size: 10px;
  font-weight: 800;
  color: #fff;
  background: #f97316;
  padding: 4px 8px;
  border-radius: 6px;
}

.dept-header-info {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.dept-header-title {
  font-size: 15px;
  font-weight: 800;
  color: var(--ink-primary);
  margin: 0;
}

.dept-header-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-muted);
}

.no-teachers-found {
  padding: 32px 0;
  text-align: center;
  color: var(--ink-muted);
  font-size: 14px;
}

.campus-tabs-bar {
  position: relative;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 24px;
}

.campuses-list {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.campus-block {
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.85);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.04);
}

.campus-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.campus-badge {
  font-size: 10px;
  font-weight: 800;
  color: var(--blue-700);
  background: var(--blue-50);
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid var(--blue-200);
}

.campus-title {
  font-size: 16px;
  font-weight: 800;
  color: var(--ink-primary);
  margin: 0;
}

.rooms-chips-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.room-pick-btn {
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid var(--border-light);
  background: #f8fafc;
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--blue-800);
  cursor: pointer;
  transition: all 0.18s ease;
}

.room-pick-btn:hover {
  background: var(--blue-600);
  color: #ffffff;
  border-color: var(--blue-600);
  transform: translateY(-1px);
}

@media (max-width: 640px) {
  .teachers-grid {
    grid-template-columns: 1fr;
  }
  .picker-title {
    font-size: 20px;
  }
  .ambient-orange-orb {
    width: min(380px, 86vw);
    height: min(380px, 86vw);
    filter: blur(36px);
  }
  .ambient-orange-glow {
    width: min(520px, 94vw);
    height: min(520px, 94vw);
    filter: blur(48px);
  }
}
</style>
