<!--
  HeroScene3D.vue
  Интерактивная 3D-сцена Chrono-Orb в шапке публичного портала.
  Размещает текстовый блок слева, а 3D-сферу с гироскопическими кольцами — справа
  в квадратном контейнере без перспективных искажений и сплющивания.
-->
<template>
  <section class="scene-container" ref="containerRef" @mousemove="onMouseMove" @mouseenter="updateCachedRect">
    <div class="glass-overlay">
      <div class="hero-layout-row">
        <!-- Текстовый блок слева: бейдж, крупный заголовок и описание -->
        <div class="hero-text-col">
          <div class="scene-badge">
            <span class="badge-dot" aria-hidden="true"></span>
            <span>ОФИЦИАЛЬНОЕ РАСПИСАНИЕ КТМУ</span>
          </div>
          <h1 class="scene-title">Расписание занятий</h1>
          <p class="scene-subtitle">
            Актуальные пары, расписание звонков и оперативные замены колледжа
          </p>
        </div>

        <!-- 3D Сцена справа: квадратная область под сферу без эллиптических деформаций -->
        <div class="hero-orb-col" ref="orbColRef">
          <div class="ambient-fallback-orb" aria-hidden="true"></div>
          <canvas ref="canvasRef" class="webgl-canvas"></canvas>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import * as THREE from 'three';

const containerRef = ref(null);
const orbColRef = ref(null);
const canvasRef = ref(null);

let renderer = null;
let scene = null;
let camera = null;
let animationFrameId = null;
let chronoGroup = null;
let coreMesh = null;
let coronaSprite = null;
let ring1 = null;
let ring2 = null;
let satellitesGroup = null;
let isVisible = true;
let observer = null;
let resizeObserver = null;
let lastFrameTime = 0;
let cachedWidth = 1;
let cachedHeight = 1;
let cachedLeft = 0;
let cachedTop = 0;

const mouse = { x: 0, y: 0, targetX: 0, targetY: 0 };
const FPS_INTERVAL = 1000 / 35;

/**
 * Создаёт радиальную градиентную текстуру тёплого янтарно-оранжевого ядра.
 */
function createOrangeGradientTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');
  const grad = ctx.createRadialGradient(95, 85, 8, 128, 128, 125);
  grad.addColorStop(0, '#FFFBEB');
  grad.addColorStop(0.2, '#FED7AA');
  grad.addColorStop(0.48, '#F97316');
  grad.addColorStop(0.8, '#EA580C');
  grad.addColorStop(1, '#9A3412');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 256, 256);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/**
 * Инициализирует меш центральной сферы Chrono-Orb с самосвечением.
 */
function createCoreMesh() {
  const coreGeo = new THREE.SphereGeometry(1.25, 36, 36);
  const tex = createOrangeGradientTexture();
  const coreMat = new THREE.MeshStandardMaterial({
    map: tex,
    roughness: 0.22,
    metalness: 0.05,
    emissive: 0xf97316,
    emissiveIntensity: 0.38
  });
  coreMesh = new THREE.Mesh(coreGeo, coreMat);
  return coreMesh;
}

/**
 * Создаёт мягкий световой ореол (корону) вокруг сферы.
 */
function createCoronaSprite() {
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  const grad = ctx.createRadialGradient(64, 64, 6, 64, 64, 63);
  grad.addColorStop(0, 'rgba(249, 115, 22, 0.45)');
  grad.addColorStop(0.45, 'rgba(234, 88, 12, 0.15)');
  grad.addColorStop(1, 'rgba(249, 115, 22, 0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  coronaSprite = new THREE.Sprite(mat);
  coronaSprite.scale.set(3.8, 3.8, 1);
  return coronaSprite;
}

/**
 * Создаёт пару гироскопических орбит (колец), вращающихся вокруг сферы.
 */
function createGyroRings() {
  const rings = new THREE.Group();
  const ring1Geo = new THREE.TorusGeometry(1.95, 0.035, 12, 40);
  const ring1Mat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.25, metalness: 0.6 });
  ring1 = new THREE.Mesh(ring1Geo, ring1Mat);
  ring1.rotation.x = Math.PI / 3;
  ring1.rotation.y = Math.PI / 6;

  const ring2Geo = new THREE.TorusGeometry(2.35, 0.03, 12, 40);
  const ring2Mat = new THREE.MeshStandardMaterial({ color: 0x38bdf8, roughness: 0.3, metalness: 0.4 });
  ring2 = new THREE.Mesh(ring2Geo, ring2Mat);
  ring2.rotation.x = -Math.PI / 3.2;
  ring2.rotation.y = Math.PI / 4;

  rings.add(ring1);
  rings.add(ring2);
  return rings;
}

/**
 * Создаёт 4 микро-спутника, вращающихся по внешней траектории.
 */
function createSatellites() {
  satellitesGroup = new THREE.Group();
  const chipGeo = new THREE.BoxGeometry(0.24, 0.16, 0.04);
  const colors = [0x0284c7, 0xf97316, 0x0284c7, 0xffedd5];
  for (let i = 0; i < 4; i++) {
    const mat = new THREE.MeshStandardMaterial({ color: colors[i], roughness: 0.2, metalness: 0.3 });
    const chip = new THREE.Mesh(chipGeo, mat);
    const angle = (i / 4) * Math.PI * 2;
    chip.position.set(Math.cos(angle) * 2.7, Math.sin(angle * 2) * 0.4, Math.sin(angle) * 2.7);
    chip.rotation.y = -angle;
    satellitesGroup.add(chip);
  }
  return satellitesGroup;
}

/**
 * Собирает все компоненты Chrono-Orb в единую 3D-группу.
 */
function setupChronoScene() {
  chronoGroup = new THREE.Group();
  chronoGroup.add(createCoronaSprite());
  chronoGroup.add(createCoreMesh());
  chronoGroup.add(createGyroRings());
  chronoGroup.add(createSatellites());
  scene.add(chronoGroup);
}

/**
 * Настраивает направленное и рассеянное освещение сцены.
 */
function setupLights() {
  const dirLight = new THREE.DirectionalLight(0xffffff, 2.0);
  dirLight.position.set(5, 8, 7);
  scene.add(dirLight);

  const ambLight = new THREE.AmbientLight(0xffffff, 1.4);
  scene.add(ambLight);
}

/**
 * Создаёт сцену, симметричную камеру и легковесный WebGL рендерер.
 */
function initScene(width, height) {
  const safeW = Math.max(width || orbColRef.value?.clientWidth || 280, 1);
  const safeH = Math.max(height || orbColRef.value?.clientHeight || 280, 1);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, safeW / safeH, 0.1, 100);
  camera.position.set(0, 0, 7.8);

  try {
    renderer = new THREE.WebGLRenderer({
      canvas: canvasRef.value,
      alpha: true,
      antialias: false,
      powerPreference: 'low-power'
    });
    renderer.setSize(safeW, safeH);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.25));
  } catch (err) {
    console.warn('WebGL init fallback:', err);
  }
}

/**
 * Обновляет вращение сферы, колец и спутников с лёгким покачиванием.
 */
function updateChronoPositions(time) {
  if (chronoGroup) {
    chronoGroup.rotation.y = time * 0.22 + mouse.x * 0.35;
    chronoGroup.rotation.x = Math.sin(time * 0.15) * 0.08 + mouse.y * 0.2;
    chronoGroup.position.set(0, Math.sin(time * 0.8) * 0.08, 0);
  }
  if (ring1) ring1.rotation.z = time * 0.32;
  if (ring2) ring2.rotation.z = -time * 0.25;
  if (satellitesGroup) satellitesGroup.rotation.y = -time * 0.28;
}

/**
 * Запускает цикл анимации с ограничением до 35 кадров/с для экономии батареи.
 */
function animate(now = 0) {
  if (!isVisible || !renderer || !scene || !camera) return;
  animationFrameId = requestAnimationFrame(animate);

  const delta = now - lastFrameTime;
  if (delta < FPS_INTERVAL) return;
  lastFrameTime = now - (delta % FPS_INTERVAL);

  mouse.x += (mouse.targetX - mouse.x) * 0.06;
  mouse.y += (mouse.targetY - mouse.y) * 0.06;

  updateChronoPositions(now * 0.001);
  renderer.render(scene, camera);
}

/**
 * Кэширует геометрию контейнера для плавного расчёта параллакса курсора.
 */
function updateCachedRect() {
  if (!containerRef.value) return;
  const rect = containerRef.value.getBoundingClientRect();
  cachedWidth = rect.width || 1;
  cachedHeight = rect.height || 1;
  cachedLeft = rect.left;
  cachedTop = rect.top;
}

/**
 * Обрабатывает движение курсора для наклона 3D-сферы.
 */
function onMouseMove(event) {
  mouse.targetX = ((event.clientX - cachedLeft) / cachedWidth) * 2 - 1;
  mouse.targetY = -(((event.clientY - cachedTop) / cachedHeight) * 2 - 1);
}

/**
 * Обновляет проекцию камеры и размер рендерера при изменении размеров колонки.
 */
function handleResize() {
  if (!orbColRef.value || !renderer || !camera) return;
  updateCachedRect();
  const width = Math.max(orbColRef.value.clientWidth || 0, 1);
  const height = Math.max(orbColRef.value.clientHeight || 0, 1);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
}

/**
 * Отключает рендеринг при уходе со страницы или сворачивании вкладки.
 */
function setupVisibilityObserver() {
  if (!window.IntersectionObserver || !containerRef.value) return;
  observer = new IntersectionObserver(([entry]) => {
    const visible = entry.isIntersecting && !document.hidden;
    if (visible && !isVisible) {
      isVisible = true;
      lastFrameTime = performance.now();
      animate(lastFrameTime);
    } else {
      isVisible = visible;
    }
  }, { threshold: 0.05 });
  observer.observe(containerRef.value);
}

/**
 * Отслеживает изменения размеров правой колонки со сферой.
 */
function setupResizeObserver() {
  if (!window.ResizeObserver || !orbColRef.value) return;
  resizeObserver = new ResizeObserver(() => handleResize());
  resizeObserver.observe(orbColRef.value);
}

/**
 * Освобождает видеопамять Three.js объектов при демонтировании.
 */
function disposeThreeObject(obj) {
  if (!obj) return;
  if (obj.geometry) obj.geometry.dispose();
  if (obj.material) {
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    mats.forEach(m => {
      if (m.map) m.map.dispose();
      m.dispose();
    });
  }
}

/**
 * Очищает ресурсы WebGL, таймеры анимации и слушатели событий.
 */
function cleanupWebGL() {
  if (animationFrameId) cancelAnimationFrame(animationFrameId);
  if (observer) observer.disconnect();
  if (resizeObserver) resizeObserver.disconnect();
  window.removeEventListener('resize', handleResize);
  if (scene) {
    scene.traverse(disposeThreeObject);
    scene.clear();
  }
  if (renderer) {
    renderer.dispose();
    renderer = null;
  }
  scene = null;
  camera = null;
  isVisible = false;
}

onMounted(() => {
  updateCachedRect();
  const colW = orbColRef.value?.clientWidth || 280;
  const colH = orbColRef.value?.clientHeight || 280;
  initScene(colW, colH);
  setupLights();
  setupChronoScene();
  setupVisibilityObserver();
  setupResizeObserver();
  lastFrameTime = performance.now();
  animate(lastFrameTime);
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  cleanupWebGL();
});
</script>

<style scoped>
.scene-container {
  position: relative;
  width: 100%;
  max-width: 100vw;
  box-sizing: border-box;
  min-height: 240px;
  background: linear-gradient(135deg, #ffffff 0%, #f0f9ff 48%, #fff7ed 100%);
  border-bottom: 1px solid var(--border-light);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 36px 32px;
}

.glass-overlay {
  position: relative;
  z-index: 2;
  width: 100%;
  max-width: 1440px;
  margin: 0 auto;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  pointer-events: auto;
}

.hero-layout-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 48px;
  width: 100%;
}

.hero-text-col {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  text-align: left;
  flex: 1 1 auto;
  min-width: 0;
  max-width: 820px;
}

.scene-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: rgba(2, 132, 199, 0.08);
  border: 1px solid rgba(2, 132, 199, 0.2);
  border-radius: 20px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--blue-700);
  margin-bottom: 14px;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.badge-dot {
  width: 7px;
  height: 7px;
  background: var(--orange-500);
  border-radius: 50%;
  box-shadow: 0 0 8px var(--orange-500);
}

.scene-title {
  font-size: 44px;
  font-weight: 850;
  line-height: 1.15;
  color: var(--ink-primary);
  margin-bottom: 14px;
  letter-spacing: -0.02em;
}

.scene-subtitle {
  font-size: 16px;
  color: var(--ink-muted);
  line-height: 1.5;
  max-width: 640px;
  text-align: left;
  margin: 0;
}

.hero-orb-col {
  position: relative;
  width: 280px;
  height: 280px;
  flex: 0 0 280px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ambient-fallback-orb {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 220px;
  height: 220px;
  border-radius: 50%;
  background: radial-gradient(circle, #f97316 0%, #ea580c 45%, rgba(249, 115, 22, 0.12) 70%, transparent 85%);
  filter: blur(34px);
  opacity: 0.45;
  pointer-events: none;
  z-index: 0;
}

.webgl-canvas {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}

@media (max-width: 860px) {
  .scene-container {
    padding: 28px 20px;
    min-height: 200px;
  }
  .hero-layout-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 20px;
  }
  .hero-orb-col {
    width: 200px;
    height: 200px;
    flex: 0 0 200px;
    align-self: center;
  }
  .scene-title {
    font-size: 30px;
  }
  .scene-subtitle {
    font-size: 14px;
  }
}

@media (max-width: 480px) {
  .scene-container {
    padding: 20px 14px;
  }
  .hero-orb-col {
    width: 160px;
    height: 160px;
    flex: 0 0 160px;
  }
  .scene-title {
    font-size: 24px;
    margin-bottom: 8px;
  }
  .scene-subtitle {
    font-size: 13px;
  }
}
</style>
