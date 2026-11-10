<template>
  <div class="drill-down-container">
    <!-- Фоновый центральный оранжевый круг-градиент, эффектно выбивающийся из всей палитры -->
    <div class="ambient-orange-orb" aria-hidden="true"></div>
    <div class="ambient-orange-glow" aria-hidden="true"></div>

    <!-- Хлебные крошки и заголовок шага -->
    <div class="drill-down-header">
      <nav class="drill-crumbs" aria-label="Навигация выбора">
        <button 
          v-if="step !== 'specialty'" 
          type="button" 
          class="crumb-btn active"
          @click="goToSpecialties"
        >
          ← Все направления
        </button>
        <span v-else class="crumb-current">Выбор направления подготовки</span>

        <template v-if="step === 'year' || step === 'group'">
          <span class="crumb-separator">/</span>
          <button 
            type="button" 
            class="crumb-btn" 
            :class="{ active: step === 'group' }"
            @click="goToYears"
          >
            {{ currentSpecialtyTitle }}
          </button>
        </template>

        <template v-if="step === 'group' && chosenYear">
          <span class="crumb-separator">/</span>
          <span class="crumb-current">{{ chosenYear }} год</span>
        </template>
      </nav>

      <h2 class="drill-title">{{ stepHeading }}</h2>
      <p class="drill-subtitle">{{ stepSubtitle }}</p>
    </div>

    <!-- ШАГ 1: Выбор специальности / направления -->
    <div v-if="step === 'specialty'" class="drill-grid specialty-grid">
      <div 
        v-for="sp in activeSpecialties" 
        :key="sp.id"
        class="drill-card specialty-card"
        role="button"
        tabindex="0"
        :aria-label="`Специальность ${parseSpecialtyTitle(sp.name)}, ${sp.groupCount} групп`"
        @click="selectSpecialty(sp.id)"
        @keydown.enter="selectSpecialty(sp.id)"
        @keydown.space.prevent="selectSpecialty(sp.id)"
      >
        <div class="card-top">
          <span v-if="parseSpecialtyCode(sp.name)" class="specialty-code-badge">
            {{ parseSpecialtyCode(sp.name) }}
          </span>
          <span class="group-count-pill">{{ sp.groupCount }} групп</span>
        </div>
        <h3 class="specialty-title">{{ parseSpecialtyTitle(sp.name) }}</h3>
        <div class="card-action">
          <span>Выбрать направление</span>
          <span class="arrow-icon">→</span>
        </div>
      </div>

      <div 
        v-if="unassignedCount > 0"
        class="drill-card specialty-card unassigned"
        role="button"
        tabindex="0"
        aria-label="Группы без привязки к специальности"
        @click="selectSpecialty('')"
        @keydown.enter="selectSpecialty('')"
        @keydown.space.prevent="selectSpecialty('')"
      >
        <div class="card-top">
          <span class="specialty-code-badge general">ОБЩИЕ</span>
          <span class="group-count-pill">{{ unassignedCount }} групп</span>
        </div>
        <h3 class="specialty-title">Группы без привязки к специальности</h3>
        <div class="card-action">
          <span>Выбрать</span>
          <span class="arrow-icon">→</span>
        </div>
      </div>
    </div>

    <!-- ШАГ 2: Выбор года поступления (курса) -->
    <div v-else-if="step === 'year'" class="drill-grid year-grid">
      <div 
        v-for="year in availableYears" 
        :key="year"
        class="drill-card year-card"
        role="button"
        tabindex="0"
        :aria-label="`${year} год поступления, ${calculateCourse(year)}`"
        @click="selectYear(year)"
        @keydown.enter="selectYear(year)"
        @keydown.space.prevent="selectYear(year)"
      >
        <div class="year-circle">
          <span class="year-num">{{ year === '__none' ? 'Заоч.' : year }}</span>
          <span class="year-label">{{ year === '__none' ? 'курс' : 'год' }}</span>
        </div>
        <div class="year-info">
          <h3 class="year-title">{{ year === '__none' ? 'Заочное отделение / без года' : `${year} год поступления` }}</h3>
          <span class="course-badge">{{ calculateCourse(year) }}</span>
        </div>
        <span class="arrow-icon large">→</span>
      </div>
    </div>

    <!-- ШАГ 3: Выбор студенческой группы -->
    <div v-else-if="step === 'group'" class="drill-grid group-grid">
      <div 
        v-for="grp in availableGroups" 
        :key="grp.id"
        class="drill-card group-card"
        role="button"
        tabindex="0"
        :aria-label="`Группа ${grp.name}, показать расписание`"
        @click="selectGroup(grp.id)"
        @keydown.enter="selectGroup(grp.id)"
        @keydown.space.prevent="selectGroup(grp.id)"
      >
        <span class="group-code-pill">Группа</span>
        <h3 class="group-name">{{ grp.name }}</h3>
        <span class="open-schedule-btn">Показать расписание</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';

const props = defineProps({
  specialties: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] }
});

const emit = defineEmits(['select-group']);

const step = ref('specialty');
const chosenSpecialtyId = ref('');
const chosenYear = ref(null);

// 1. Извлечение официального шифра специальности (напр. 09.02.11)
function parseSpecialtyCode(name) {
  const match = String(name || '').match(/^(\d{2}\.\d{2}\.\d{2})/);
  return match ? match[1] : '';
}

// 2. Извлечение читаемого названия специальности
function parseSpecialtyTitle(name) {
  return String(name || '').replace(/^\d{2}\.\d{2}\.\d{2}\s*/, '').trim() || name;
}

// 3. Расчёт курса обучения по году поступления
function calculateCourse(admissionYear) {
  if (!admissionYear || admissionYear === '__none') return 'Заочное / без года';
  const now = new Date();
  const academicStartYear = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
  const course = academicStartYear - Number(admissionYear) + 1;
  return course > 0 ? `${course} курс` : `${admissionYear} год`;
}

// 4. Активные специальности колледжа с количеством групп
const activeSpecialties = computed(() => {
  const spMap = new Map();
  props.specialties.forEach(sp => {
    spMap.set(sp.id, { ...sp, groupCount: 0 });
  });
  props.groups.forEach(g => {
    if (g.specialtyId && spMap.has(g.specialtyId)) {
      spMap.get(g.specialtyId).groupCount += 1;
    }
  });
  return Array.from(spMap.values())
    .filter(sp => sp.groupCount > 0)
    .sort((a, b) => a.name.localeCompare(b.name, 'ru'));
});

// 5. Количество групп без специальности
const unassignedCount = computed(() => {
  return props.groups.filter(g => !g.specialtyId).length;
});

// 6. Название текущей выбранной специальности
const currentSpecialtyTitle = computed(() => {
  if (!chosenSpecialtyId.value) return 'Без специальности';
  const sp = props.specialties.find(s => s.id === chosenSpecialtyId.value);
  return sp ? parseSpecialtyTitle(sp.name) : 'Специальность';
});

// 7. Доступные года поступления для выбранной специальности
const availableYears = computed(() => {
  const specGroups = props.groups.filter(g => (g.specialtyId || '') === (chosenSpecialtyId.value || ''));
  const hasNoYear = specGroups.some(g => !g.admissionYear);
  const years = [...new Set(specGroups.map(g => g.admissionYear).filter(Boolean))].sort((a, b) => Number(b) - Number(a));
  if (hasNoYear && years.length > 0) years.push('__none');
  return years;
});

// 8. Список групп для выбранной специальности и года
const availableGroups = computed(() => {
  return props.groups
    .filter(g => {
      const matchSpec = (g.specialtyId || '') === (chosenSpecialtyId.value || '');
      if (chosenYear.value === null) return matchSpec;
      if (chosenYear.value === '__none') return matchSpec && !g.admissionYear;
      return matchSpec && Number(g.admissionYear) === Number(chosenYear.value);
    })
    .sort((a, b) => a.name.localeCompare(b.name, 'ru', { numeric: true }));
});

// 9. Заголовок текущего шага
const stepHeading = computed(() => {
  if (step.value === 'specialty') return 'Направления подготовки';
  if (step.value === 'year') return 'Год поступления (курс)';
  return 'Выберите студенческую группу';
});

// 10. Подзаголовок текущего шага
const stepSubtitle = computed(() => {
  if (step.value === 'specialty') return 'Выберите ваше отделение или программу обучения';
  if (step.value === 'year') return 'Выберите календарный год начала вашего обучения';
  return 'Нажмите на номер группы, чтобы открыть актуальное расписание';
});

// 11. Выбор специальности
function selectSpecialty(specId) {
  chosenSpecialtyId.value = specId;
  if (!availableYears.value.length) {
    chosenYear.value = null;
    step.value = 'group';
  } else {
    step.value = 'year';
  }
}

// 12. Выбор года
function selectYear(year) {
  chosenYear.value = year;
  step.value = 'group';
}

// 13. Выбор группы
function selectGroup(groupId) {
  emit('select-group', groupId);
}

// 14. Возврат к специальностям
function goToSpecialties() {
  chosenSpecialtyId.value = '';
  chosenYear.value = null;
  step.value = 'specialty';
}

// 15. Возврат к годам (с проверкой наличия годов)
function goToYears() {
  if (!availableYears.value.length) {
    goToSpecialties();
    return;
  }
  chosenYear.value = null;
  step.value = 'year';
}
</script>

<style scoped>
.drill-down-container {
  position: relative;
  width: 100%;
  max-width: 1240px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  padding: 10px 0 40px;
  min-height: 60vh;
  overflow-x: clip;
}

/* 1. Насыщенный светящийся центральный оранжевый круг */
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
  opacity: 0.52;
  pointer-events: none;
  z-index: 0;
}

/* 2. Широкий мягкий ореол теплого свечения вокруг центрального круга */
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
  opacity: 0.6;
  pointer-events: none;
  z-index: 0;
}

.drill-down-header {
  position: relative;
  z-index: 2;
  margin-bottom: 24px;
  text-align: left;
}

.drill-crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.crumb-btn {
  background: transparent;
  border: none;
  color: var(--blue-700);
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.crumb-btn:hover {
  background: var(--blue-50);
  color: var(--blue-800);
}

.crumb-separator {
  color: var(--ink-muted);
  font-size: 13px;
}

.crumb-current {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-secondary);
  padding: 4px 0;
}

.drill-title {
  font-size: 24px;
  font-weight: 800;
  color: var(--ink-primary);
  margin-bottom: 6px;
}

.drill-subtitle {
  font-size: 14px;
  color: var(--ink-muted);
}

.drill-grid {
  position: relative;
  z-index: 2;
  display: grid;
  gap: 16px;
  width: 100%;
}

.specialty-grid {
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
}

.year-grid {
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
}

.group-grid {
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
}

.drill-card {
  position: relative;
  z-index: 2;
  background: rgba(255, 255, 255, 0.88);
  -webkit-backdrop-filter: blur(14px);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.85);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.05), 0 2px 6px rgba(0, 0, 0, 0.02);
  cursor: pointer;
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  display: flex;
  flex-direction: column;
}

.drill-card:hover {
  background: rgba(255, 255, 255, 0.98);
  border-color: #f97316;
  box-shadow: 0 12px 30px -4px rgba(234, 88, 12, 0.25), 0 4px 12px rgba(0, 0, 0, 0.04);
  transform: translateY(-2px);
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.specialty-code-badge {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 800;
  color: var(--blue-700);
  background: var(--blue-50);
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid var(--blue-200);
}

.specialty-code-badge.general {
  color: var(--ink-secondary);
  background: var(--border-light);
  border-color: #cbd5e1;
}

.group-count-pill {
  font-size: 11px;
  font-weight: 700;
  color: var(--ink-muted);
  background: #f1f5f9;
  padding: 3px 8px;
  border-radius: 12px;
}

.specialty-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ink-primary);
  line-height: 1.4;
  margin-bottom: 16px;
  flex: 1;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.card-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 700;
  color: var(--blue-600);
  padding-top: 12px;
  border-top: 1px solid var(--border-light);
}

.arrow-icon {
  font-size: 16px;
  transition: transform 0.2s ease;
}

.drill-card:hover .arrow-icon {
  transform: translateX(4px);
}

/* Карточки года */
.year-card {
  flex-direction: row;
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
}

.year-circle {
  width: 54px;
  height: 54px;
  border-radius: 14px;
  background: var(--blue-50);
  border: 1px solid var(--blue-200);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.year-num {
  font-size: 15px;
  font-weight: 800;
  color: var(--blue-700);
  line-height: 1;
}

.year-label {
  font-size: 9px;
  font-weight: 700;
  color: var(--blue-600);
  text-transform: uppercase;
  margin-top: 2px;
}

.year-info {
  flex: 1;
}

.year-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink-primary);
  margin-bottom: 4px;
}

.course-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 800;
  color: var(--orange-700);
  background: var(--orange-50);
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid var(--orange-200);
}

/* Карточки группы */
.group-card {
  align-items: center;
  text-align: center;
  padding: 20px 16px;
}

.group-code-pill {
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  color: var(--blue-700);
  background: var(--blue-50);
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid var(--blue-200);
  margin-bottom: 8px;
}

.group-name {
  font-size: 18px;
  font-weight: 800;
  color: var(--ink-primary);
  margin-bottom: 10px;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.open-schedule-btn {
  font-size: 12px;
  font-weight: 700;
  color: var(--blue-600);
  background: var(--blue-50);
  padding: 6px 12px;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.group-card:hover .open-schedule-btn {
  background: var(--blue-600);
  color: #ffffff;
}

@media (max-width: 640px) {
  .specialty-grid, .year-grid, .group-grid {
    grid-template-columns: 1fr;
  }
  .drill-title {
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
