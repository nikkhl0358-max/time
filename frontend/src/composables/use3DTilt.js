/**
 * Оптимизированный физический 3D-наклон карточки при наведении мыши.
 * Кэширует геометрию элемента для предотвращения принудительного пересчёта макета (Reflow).
 */
import { ref } from 'vue';

export function use3DTilt(maxTilt = 6) {
  const cardRef = ref(null);
  const transformStyle = ref('');
  const lightReflectionStyle = ref('');
  let cachedRect = null;

  function handleMouseEnter() {
    if (!cardRef.value) return;
    if (typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(hover: none)').matches) return;
    cachedRect = cardRef.value.getBoundingClientRect();
  }

  function handleMouseMove(event) {
    if (!cachedRect) return;
    const x = event.clientX - cachedRect.left;
    const y = event.clientY - cachedRect.top;
    
    const rotX = ((y / (cachedRect.height || 1)) - 0.5) * -maxTilt;
    const rotY = ((x / (cachedRect.width || 1)) - 0.5) * maxTilt;
    
    transformStyle.value = `perspective(800px) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg) translateZ(4px)`;
    lightReflectionStyle.value = `radial-gradient(circle at ${x}px ${y}px, rgba(255,255,255,0.6) 0%, rgba(255,255,255,0) 65%)`;
  }

  function handleMouseLeave() {
    cachedRect = null;
    transformStyle.value = 'perspective(800px) rotateX(0deg) rotateY(0deg) translateZ(0px)';
    lightReflectionStyle.value = 'none';
  }

  return {
    cardRef,
    transformStyle,
    lightReflectionStyle,
    handleMouseEnter,
    handleMouseMove,
    handleMouseLeave
  };
}
