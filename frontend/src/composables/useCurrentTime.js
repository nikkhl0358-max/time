/**
 * Вычисляет текущее время, активную пару и обратный отсчет до звонка.
 */
import { ref, onMounted, onUnmounted } from 'vue';

export const STANDARD_BELLS = [
  { period: 1, start: '08:30', end: '10:00' },
  { period: 2, start: '10:10', end: '11:40' },
  { period: 3, start: '11:50', end: '13:20' },
  { period: 4, start: '14:00', end: '15:30' },
  { period: 5, start: '15:40', end: '17:10' },
  { period: 6, start: '17:20', end: '18:50' }
];

export const VOZNESENSKY_BELLS = [
  { period: 1, start: '08:30', end: '09:55' },
  { period: 2, start: '10:05', end: '11:30' },
  { period: 3, start: '11:40', end: '13:05' },
  { period: 4, start: '13:45', end: '15:10' },
  { period: 5, start: '15:20', end: '16:45' },
  { period: 6, start: '16:55', end: '18:20' }
];

export const SHORTENED_BELLS = [
  { period: 1, start: '09:00', end: '10:00' },
  { period: 2, start: '10:10', end: '11:10' },
  { period: 3, start: '11:30', end: '12:30' },
  { period: 4, start: '12:50', end: '13:50' },
  { period: 5, start: '14:05', end: '15:05' },
  { period: 6, start: '15:15', end: '16:15' }
];

const DEFAULT_BELLS = STANDARD_BELLS;

function timeToMinutes(timeStr) {
  const [hours, minutes] = timeStr.split(':').map(Number);
  return (hours * 60) + minutes;
}

function findActivePeriod(currentMinutes, bells) {
  for (const bell of bells) {
    const startMin = timeToMinutes(bell.start);
    const endMin = timeToMinutes(bell.end);
    if (currentMinutes >= startMin && currentMinutes <= endMin) {
      return { period: bell.period, minutesLeft: endMin - currentMinutes };
    }
  }
  return null;
}

export function useCurrentTime() {
  const now = ref(new Date());
  const activePair = ref(null);
  let timerId = null;

  function updateStatus() {
    now.value = new Date();
    const currentMins = (now.value.getHours() * 60) + now.value.getMinutes();
    activePair.value = findActivePeriod(currentMins, DEFAULT_BELLS);
  }

  onMounted(() => {
    updateStatus();
    timerId = setInterval(updateStatus, 30000);
  });

  onUnmounted(() => {
    if (timerId) clearInterval(timerId);
  });

  return { now, activePair, DEFAULT_BELLS };
}
