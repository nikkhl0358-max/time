<template>
  <div class="app-root">
    <!-- Навигационная панель -->
    <Navbar
      :role="selectedKind"
      :activePair="activePair"
      :substitutionsCount="substitutions.length"
      @update:role="handleSelectRole"
      @open-bells="isBellsOpen = true"
      @open-subs="isSubsOpen = true"
      @reset-view="handleResetView"
    />

    <!-- Глобальный баннер ошибок -->
    <div v-if="error" class="global-error-banner" role="alert">
      <span>⚠️ {{ error }}</span>
      <button class="retry-load-btn" @click="loadPublicIndex">Повторить</button>
    </div>

    <!-- Режим администратора -->
    <template v-if="isAdminMode">
      <AdminLayout
        :user="currentUser"
        :substitutions="substitutions"
        :groups="groups"
        :teachers="teachers"
        :rooms="rooms"
        :subjects="subjects"
        :getGroupName="getGroupName"
        :getTeacherName="getTeacherName"
        :getRoomName="getRoomName"
        :getSubjectName="getSubjectName"
        @exit-admin="isAdminMode = false"
        @update-subs="handleUpdateSubs"
      />
    </template>

    <!-- Публичный портал -->
    <template v-else>
      <main class="main-content">
        <!-- Режим 1: Просмотр сетки пар (когда сущность выбрана) -->
        <template v-if="selectedId">
          <FilterBar
            :role="selectedKind"
            :selectedId="selectedId"
            :selectedName="currentEntityName"
            v-model:selectedWeek="selectedWeek"
            @change-selection="handleChangeSelection"
            @export-csv="handleExportCSV"
            @print="handlePrint"
          />

          <ScheduleViewer
            :instances="currentSchedule.instances"
            :loading="loading"
            :getSubjectName="getSubjectName"
            :getTeacherName="getTeacherName"
            :getRoomName="getRoomName"
            :getRoomLocation="getRoomLocation"
            :getLessonTypeName="getLessonTypeName"
            :getPeriodTime="getPeriodTime"
            :findSub="findSubstitutionForLesson"
            @select-teacher="handleSelectTeacher"
          />
        </template>

        <!-- Режим 2: Пошаговый выбор группы (Специальность -> Год -> Группа) -->
        <template v-else-if="selectedKind === 'group'">
          <GroupDrillDown
            :specialties="specialties"
            :groups="groups"
            @select-group="handleSelectGroup"
          />
        </template>

        <!-- Режим 3: Выбор преподавателя или аудитории по карточкам -->
        <template v-else>
          <TeacherRoomPicker
            :role="selectedKind"
            :items="currentItemsList"
            @select="handleSelectEntity"
          />
        </template>
      </main>

      <!-- Подвал с кнопкой входа для составителя -->
      <AppFooter
        :isAdmin="Boolean(currentUser)"
        @open-admin="handleOpenAdmin"
      />
    </template>

    <!-- Модальное окно: Звонки -->
    <Modal :isOpen="isBellsOpen" title="Расписание звонков КТМУ" @close="isBellsOpen = false">
      <BellsSchedule 
        :bells="DEFAULT_BELLS" 
        :activePeriodNumber="activePair?.period"
      />
    </Modal>

    <!-- Модальное окно: Замены -->
    <Modal :isOpen="isSubsOpen" title="Оперативные замены в расписании" @close="isSubsOpen = false">
      <SubstitutionsFeed
        :substitutions="substitutions"
        :getGroupName="getGroupName"
        :getTeacherName="getTeacherName"
        :getRoomName="getRoomName"
        :getSubjectName="getSubjectName"
      />
    </Modal>

    <!-- Модальное окно: Вход в админку -->
    <AdminAuthModal
      :isOpen="isAuthModalOpen"
      @close="isAuthModalOpen = false"
      @login-success="isAdminMode = true"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import Navbar from './components/common/Navbar.vue';
import AppFooter from './components/common/AppFooter.vue';
import Modal from './components/common/Modal.vue';
import HeroScene3D from './components/visual3d/HeroScene3D.vue';
import FilterBar from './components/public/FilterBar.vue';
import GroupDrillDown from './components/public/GroupDrillDown.vue';
import TeacherRoomPicker from './components/public/TeacherRoomPicker.vue';
import ScheduleViewer from './components/public/ScheduleViewer.vue';
import BellsSchedule from './components/public/BellsSchedule.vue';
import SubstitutionsFeed from './components/public/SubstitutionsFeed.vue';
import AdminAuthModal from './components/admin/AdminAuthModal.vue';
import AdminLayout from './components/admin/AdminLayout.vue';

import { useScheduleData } from './composables/useScheduleData.js';
import { useCurrentTime } from './composables/useCurrentTime.js';
import { useAuth } from './composables/useAuth.js';
import { useScheduleExport } from './composables/useScheduleExport.js';
import { parseCurrentRoute } from './composables/useRouteSync.js';

const isBellsOpen = ref(false);
const isSubsOpen = ref(false);
const isAuthModalOpen = ref(false);
const isAdminMode = ref(false);

const { exportScheduleCSV, printSchedule } = useScheduleExport();

const {
  currentSchedule,
  loading,
  error,
  selectedKind,
  selectedId,
  selectedWeek,
  groups,
  teachers,
  rooms,
  subjects,
  specialties,
  lessonTypes,
  substitutions,
  loadPublicIndex,
  loadScheduleShard,
  getTeacherName,
  getSubjectName,
  getRoomName,
  getRoomLocation,
  getGroupName,
  getLessonTypeName,
  getPeriodTime,
  findSubstitutionForLesson,
  updateSubstitutions,
  saveSelection,
  syncBrowserUrl
} = useScheduleData();

const { activePair, DEFAULT_BELLS } = useCurrentTime();
const { currentUser, checkSession } = useAuth();

// 1. Текущий список элементов в зависимости от роли
const currentItemsList = computed(() => {
  if (selectedKind.value === 'teacher') return teachers.value;
  if (selectedKind.value === 'room') return rooms.value;
  return groups.value;
});

// 2. Имя выбранной сущности
const currentEntityName = computed(() => {
  if (!selectedId.value) return '';
  if (selectedKind.value === 'teacher') return getTeacherName(selectedId.value);
  if (selectedKind.value === 'room') return getRoomName(selectedId.value);
  return getGroupName(selectedId.value);
});

// 3. Сброс к главному выбору
function handleResetView() {
  selectedKind.value = 'group';
  selectedId.value = '';
  isAdminMode.value = false;
  saveSelection('group', '', selectedWeek.value);
  syncBrowserUrl('group', '', selectedWeek.value);
}

// 4. Переключение роли (Студент / Преподаватель / Аудитории)
function handleSelectRole(role) {
  selectedKind.value = role;
  selectedId.value = '';
  syncBrowserUrl(role, '', selectedWeek.value);
}

// 5. Выбор студенческой группы через Drill-Down
function handleSelectGroup(groupId) {
  selectedKind.value = 'group';
  selectedId.value = groupId;
}

// 6. Выбор преподавателя или кабинета
function handleSelectEntity(entityId) {
  selectedId.value = entityId;
}

// 7. Смена сущности (возврат к пошаговому выбору)
function handleChangeSelection() {
  selectedId.value = '';
  syncBrowserUrl(selectedKind.value, '', selectedWeek.value);
}

// 8. Переход к расписанию преподавателя из карточки пары
function handleSelectTeacher(teacherId) {
  if (!teacherId) return;
  selectedKind.value = 'teacher';
  selectedId.value = teacherId;
}

// 9. Открытие панели администратора
function handleOpenAdmin() {
  window.location.href = '/admin';
}

// 10. Обновление замен
function handleUpdateSubs(updatedSubs) {
  updateSubstitutions(updatedSubs);
}

// 11. Экспорт в CSV
function handleExportCSV() {
  exportScheduleCSV(currentSchedule.value.instances, currentEntityName.value, getSubjectName, getTeacherName, getRoomName);
}

// 12. Печать расписания
function handlePrint() {
  printSchedule();
}

// 13. Обработка навигации в истории браузера
function handlePopState() {
  const route = parseCurrentRoute();
  selectedKind.value = route.kind || 'group';
  selectedId.value = route.id || '';
  if (route.week) selectedWeek.value = route.week;
}

// Синхронизация URL и загрузка шарда при изменении сущности
watch([selectedKind, selectedId, selectedWeek], () => {
  saveSelection(selectedKind.value, selectedId.value, selectedWeek.value);
  syncBrowserUrl(selectedKind.value, selectedId.value, selectedWeek.value);
  if (selectedId.value) {
    loadScheduleShard(selectedKind.value, selectedId.value, selectedWeek.value);
  }
});

onMounted(async () => {
  window.addEventListener('popstate', handlePopState);
  await loadPublicIndex();
  await checkSession();
  if (window.location.pathname.startsWith('/admin') && currentUser.value) {
    isAdminMode.value = true;
  }
});

onUnmounted(() => {
  window.removeEventListener('popstate', handlePopState);
});
</script>

<style scoped>
.app-root {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background-color: #f8fafc;
  background-image: 
    radial-gradient(circle at 50% 45%, rgba(249, 115, 22, 0.22) 0%, rgba(251, 146, 60, 0.12) 30%, rgba(254, 215, 170, 0.04) 55%, transparent 72%),
    radial-gradient(circle at 88% 12%, rgba(2, 132, 199, 0.08) 0%, transparent 45%);
  background-attachment: fixed;
}

.global-error-banner {
  background: #fef2f2;
  border-bottom: 1px solid #fecaca;
  color: #991b1b;
  padding: 10px 24px;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.retry-load-btn {
  padding: 4px 12px;
  background: #ef4444;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.main-content {
  flex: 1;
  max-width: 1440px;
  width: 100%;
  margin: 0 auto;
  padding: 24px 32px 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  overflow-x: hidden;
  box-sizing: border-box;
}

@media (max-width: 768px) {
  .main-content {
    padding: 16px 14px 36px;
  }
}

@media (max-width: 480px) {
  .main-content {
    padding: 12px 10px 32px;
  }
}
</style>
