<template>
  <div class="scene-container" ref="containerRef" @mousemove="onMouseMove" @mouseenter="updateCachedRect">
    <canvas ref="canvasRef" class="webgl-canvas"></canvas>
    <div class="glass-overlay">
      <div class="scene-badge">
        <span class="badge-dot"></span>
        <span>ОФИЦИАЛЬНОЕ РАСПИСАНИЕ КТМУ</span>
      </div>
      <h1 class="scene-title">
        Расписание занятий
      </h1>
      <p class="scene-subtitle">
        Актуальные пары, расписание звонков и оперативные замены колледжа
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import * as THREE from 'three';

const containerRef = ref(null);
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
let lastFrameTime = 0;
let cachedWidth = 1;
let cachedHeight = 1;
let cachedLeft = 0;
let cachedTop = 0;

const mouse = { x: 0, y: 0, targetX: 0, targetY: 0 };
// Ограничение частоты рендеринга для легкости и плавности (35 FPS)
const FPS_INTERVAL = 1000 / 35;

// 1. Создание оптимизированной градиентной текстуры для оранжевого шара
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

// 2. Создание центрального оранжевого шара на быстром MeshStandardMaterial
function createCoreMesh() {
  const coreGeo = new THREE.SphereGeometry(1.22, 32, 32);
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

// 3. Создание лёгкой ореоловой короны вокруг оранжевого круга
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
  coronaSprite.scale.set(3.6, 3.6, 1);
  return coronaSprite;
}

// 4. Тонкие орбитальные кольца времени вокруг оранжевого центра
function createGyroRings() {
  const rings = new THREE.Group();
  const ring1Geo = new THREE.TorusGeometry(1.95, 0.035, 12, 40);
  const ring1Mat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.25, metalness: 0.6 });
  ring1 = new THREE.Mesh(ring1Geo, ring1Mat);
  ring1.rotation.x = Math.PI / 3;
  ring1.rotation.y = Math.PI / 6;

  const ring2Geo = new THREE.TorusGeometry(2.35, 0.03, 12, 40);
  const ring2Mat = new THREE.MeshStandardMaterial({ color: 0xe0f2fe, roughness: 0.3, metalness: 0.4 });
  ring2 = new THREE.Mesh(ring2Geo, ring2Mat);
  ring2.rotation.x = -Math.PI / 3.2;
  ring2.rotation.y = Math.PI / 4;

  rings.add(ring1);
  rings.add(ring2);
  return rings;
}

// 5. Микро-спутники учебных слотов
function createSatellites() {
  satellitesGroup = new THREE.Group();
  const chipGeo = new THREE.BoxGeometry(0.24, 0.16, 0.04);
  const colors = [0x0284c7, 0xf97316, 0x0284c7, 0xffedd5];
  for (let i = 0; i < 4; i++) {
    const mat = new THREE.MeshStandardMaterial({ color: colors[i], roughness: 0.2, metalness: 0.3 });
    const chip = new THREE.Mesh(chipGeo, mat);
    const angle = (i / 4) * Math.PI * 2;
    chip.position.set(Math.cos(angle) * 2.8, Math.sin(angle * 2) * 0.4, Math.sin(angle) * 2.8);
    chip.rotation.y = -angle;
    satellitesGroup.add(chip);
  }
  return satellitesGroup;
}

// 6. Сборка сцены
function setupChronoScene() {
  chronoGroup = new THREE.Group();
  chronoGroup.add(createCoronaSprite());
  chronoGroup.add(createCoreMesh());
  chronoGroup.add(createGyroRings());
  chronoGroup.add(createSatellites());
  scene.add(chronoGroup);
}

// 7. Настройка освещения без перегрузки шейдеров
function setupLights() {
  const dirLight = new THREE.DirectionalLight(0xffffff, 2.0);
  dirLight.position.set(5, 8, 7);
  scene.add(dirLight);

  const ambLight = new THREE.AmbientLight(0xffffff, 1.4);
  scene.add(ambLight);
}

// 8. Инициализация WebGL с защитой от троттлинга на экранах высокого разрешения
function initScene(width, height) {
  const safeW = Math.max(width || 0, 1);
  const safeH = Math.max(height || 0, 1);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, safeW / safeH, 0.1, 100);
  camera.position.set(0, 0, 7.2);

  renderer = new THREE.WebGLRenderer({
    canvas: canvasRef.value,
    alpha: true,
    antialias: false,
    powerPreference: 'low-power'
  });
  renderer.setSize(safeW, safeH);
  // Фиксируем пиксель-рейсио на 1.25x для максимального FPS без размытия
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.25));
}

// 9. Анимационный цикл с ограничением кадров (FPS Throttling)
function animate(now = 0) {
  if (!isVisible || !renderer || !scene || !camera) return;
  animationFrameId = requestAnimationFrame(animate);

  const delta = now - lastFrameTime;
  if (delta < FPS_INTERVAL) return;
  lastFrameTime = now - (delta % FPS_INTERVAL);

  mouse.x += (mouse.targetX - mouse.x) * 0.06;
  mouse.y += (mouse.targetY - mouse.y) * 0.06;

  const time = now * 0.001;
  if (chronoGroup) {
    chronoGroup.rotation.y = time * 0.22 + mouse.x * 0.35;
    chronoGroup.rotation.x = Math.sin(time * 0.15) * 0.08 + mouse.y * 0.2;
    chronoGroup.position.y = Math.sin(time * 0.8) * 0.06;
  }
  if (ring1) ring1.rotation.z = time * 0.32;
  if (ring2) ring2.rotation.z = -time * 0.25;
  if (satellitesGroup) satellitesGroup.rotation.y = -time * 0.28;

  renderer.render(scene, camera);
}

// 10. Кэширование размеров контейнера без постоянного вызова getBoundingClientRect
function updateCachedRect() {
  if (!containerRef.value) return;
  const rect = containerRef.value.getBoundingClientRect();
  cachedWidth = rect.width || 1;
  cachedHeight = rect.height || 1;
  cachedLeft = rect.left;
  cachedTop = rect.top;
}

// 11. Плавный параллакс курсора без пересчёта макета
function onMouseMove(event) {
  mouse.targetX = ((event.clientX - cachedLeft) / cachedWidth) * 2 - 1;
  mouse.targetY = -(((event.clientY - cachedTop) / cachedHeight) * 2 - 1);
}

// 12. Адаптивный ресайз
function handleResize() {
  if (!containerRef.value || !renderer || !camera) return;
  updateCachedRect();
  const width = Math.max(containerRef.value.clientWidth || 0, 1);
  const height = Math.max(containerRef.value.clientHeight || 0, 1);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
}

// 13. Остановка анимации вне видимости экрана
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

// 14. Освобождение ресурсов Three.js
function disposeThreeObject(obj) {
  if (!obj) return;
  if (obj.geometry) obj.geometry.dispose();
  if (obj.material) {
    if (Array.isArray(obj.material)) {
      obj.material.forEach(m => m.dispose());
    } else {
      obj.material.dispose();
    }
  }
}

// 15. Полная очистка памяти
function cleanupWebGL() {
  if (animationFrameId) {
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  window.removeEventListener('resize', handleResize);
  if (scene) {
    scene.traverse(disposeThreeObject);
    scene.clear();
    scene = null;
  }
  if (renderer) {
    renderer.dispose();
    renderer.forceContextLoss?.();
    renderer = null;
  }
  camera = null;
  chronoGroup = null;
  coreMesh = null;
  coronaSprite = null;
  ring1 = null;
  ring2 = null;
  satellitesGroup = null;
}

onMounted(() => {
  updateCachedRect();
  initScene(cachedWidth, cachedHeight);
  setupLights();
  setupChronoScene();
  setupVisibilityObserver();
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
  height: 280px;
  background: linear-gradient(135deg, #ffffff 0%, #f0f9ff 50%, #fff7ed 100%);
  border-bottom: 1px solid var(--border-light);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 48px;
}

.webgl-canvas {
  position: absolute;
  right: 0;
  top: 0;
  width: 55%;
  height: 100%;
  pointer-events: none;
}

.glass-overlay {
  position: relative;
  z-index: 2;
  max-width: 640px;
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
}

.badge-dot {
  width: 7px;
  height: 7px;
  background: var(--orange-500);
  border-radius: 50%;
  box-shadow: 0 0 8px var(--orange-500);
}

.scene-title {
  font-size: 32px;
  font-weight: 800;
  line-height: 1.2;
  color: var(--ink-primary);
  margin-bottom: 10px;
}

.scene-subtitle {
  font-size: 14px;
  color: var(--ink-muted);
  line-height: 1.5;
}

@media (max-width: 768px) {
  .scene-container {
    height: auto;
    min-height: 200px;
    padding: 24px 20px;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
  }
  .webgl-canvas {
    position: absolute;
    right: 0;
    top: 0;
    width: 60%;
    height: 100%;
    opacity: 0.35;
  }
  .scene-title {
    font-size: 24px;
  }
}

@media (max-width: 480px) {
  .scene-container {
    padding: 18px 14px;
    min-height: 165px;
  }
  .scene-title {
    font-size: 20px;
    margin-bottom: 6px;
  }
  .scene-subtitle {
    font-size: 12.5px;
  }
  .webgl-canvas {
    width: 75%;
    opacity: 0.28;
  }
  .scene-badge {
    padding: 4px 10px;
    font-size: 10px;
    margin-bottom: 10px;
  }
}
</style>
