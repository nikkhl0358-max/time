<template>
  <div 
    v-if="isOpen" 
    class="modal-backdrop" 
    role="dialog"
    aria-modal="true"
    @click.self="$emit('close')"
  >
    <div class="modal-window">
      <div class="modal-header">
        <h3 class="modal-title">{{ title }}</h3>
        <button 
          type="button" 
          class="modal-close-btn" 
          aria-label="Закрыть модальное окно"
          title="Закрыть (Escape)"
          @click="$emit('close')"
        >
          ✕
        </button>
      </div>
      <div class="modal-body">
        <slot></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { watch, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  isOpen: { type: Boolean, default: false },
  title: { type: String, default: '' }
});

const emit = defineEmits(['close']);

function handleKeyDown(event) {
  if (event.key === 'Escape' && props.isOpen) {
    emit('close');
  }
}

watch(() => props.isOpen, (open) => {
  if (open) {
    document.body.style.overflow = 'hidden';
  } else {
    document.body.style.overflow = '';
  }
});

onMounted(() => {
  window.addEventListener('keydown', handleKeyDown);
  if (props.isOpen) {
    document.body.style.overflow = 'hidden';
  }
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown);
  document.body.style.overflow = '';
});
</script>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.modal-window {
  background: #ffffff;
  border-radius: var(--radius-lg);
  box-shadow: 0 20px 45px rgba(15, 23, 42, 0.2);
  width: 100%;
  max-width: 580px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border-light);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 24px;
  border-bottom: 1px solid var(--border-light);
  background: var(--bg-app);
}

.modal-title {
  font-size: 17px;
  font-weight: 800;
  color: var(--ink-primary);
}

.modal-close-btn {
  background: none;
  border: none;
  font-size: 16px;
  color: var(--ink-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  transition: color 0.15s ease;
}

.modal-close-btn:hover {
  color: var(--ink-primary);
}

.modal-body {
  padding: 24px;
  overflow-y: auto;
}

@media (max-width: 600px) {
  .modal-backdrop {
    padding: 8px;
    align-items: flex-end;
  }
  .modal-window {
    max-height: 90vh;
    border-radius: 16px 16px 0 0;
  }
  .modal-header {
    padding: 14px 18px;
  }
  .modal-body {
    padding: 16px;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
