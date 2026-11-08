<template>
  <div 
    ref="cardRef"
    class="spatial-card"
    :style="{ transform: transformStyle }"
    @mouseenter="handleMouseEnter"
    @mousemove="handleMouseMove"
    @mouseleave="handleMouseLeave"
  >
    <div 
      class="specular-reflection"
      :style="{ background: lightReflectionStyle }"
    ></div>
    <div class="card-inner">
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
import { use3DTilt } from '../../composables/use3DTilt.js';

const props = defineProps({
  maxTilt: { type: Number, default: 5 }
});

const {
  cardRef,
  transformStyle,
  lightReflectionStyle,
  handleMouseEnter,
  handleMouseMove,
  handleMouseLeave
} = use3DTilt(props.maxTilt);
</script>

<style scoped>
.spatial-card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-subtle);
  transition: transform 0.15s ease-out, box-shadow 0.25s ease, border-color 0.2s ease;
  transform-style: preserve-3d;
  will-change: transform;
  overflow: hidden;
  box-sizing: border-box;
}

.spatial-card:hover {
  border-color: var(--blue-200);
  box-shadow: var(--shadow-hover);
}

.specular-reflection {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 5;
  transition: opacity 0.2s ease;
}

.card-inner {
  position: relative;
  z-index: 2;
  width: 100%;
  box-sizing: border-box;
}

@media (max-width: 768px) {
  .spatial-card {
    transform: none !important;
    transform-style: flat !important;
    will-change: auto !important;
  }
  .specular-reflection {
    display: none !important;
  }
}
</style>
