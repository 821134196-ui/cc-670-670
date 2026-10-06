<template>
  <div class="check-block">
    <div class="row" style="margin:10px 0 6px">
      <b>{{ title }}</b>
      <span v-if="signed" class="badge ok">已签认</span>
      <span v-else-if="disabled" class="badge gray">当前不可签</span>
    </div>
    <div v-for="it in items" :key="it.id" class="check-row">
      <input
        type="checkbox"
        :checked="!!checks[String(it.id)]"
        :disabled="disabled || signed"
        @change="$emit('toggle', String(it.id), $event.target.checked)"
      />
      <div>
        <span class="kind" :class="it.kind">{{ kindLabel(it.kind) }}</span>
        {{ it.content }}
        <span v-if="it.state === 'OPEN'" class="badge warn" style="margin-left:6px">未完成事项</span>
        <span v-else class="badge gray" style="margin-left:6px">已落实</span>
        <button
          v-if="it.state === 'OPEN' && !signed"
          class="btn ghost sm" style="margin-left:8px"
          @click="$emit('confirm-item', it.id)"
        >标记完成</button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: String,
  items: { type: Array, default: () => [] },
  checks: { type: Object, default: () => ({}) },
  disabled: Boolean,
  signed: Boolean,
})
defineEmits(['toggle', 'confirm-item'])

function kindLabel(k) {
  return k === 'RISK' ? '风险' : k === 'ISOLATION' ? '隔离' : '事项'
}
</script>
