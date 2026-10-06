<template>
  <div>
    <div v-for="e in events" :key="e.id" class="timeline-item" :class="tone(e.type)">
      <div class="t-time">{{ fmtDt(e.occurred_at) }} · {{ e.actor_name || '系统' }}</div>
      <div class="t-title">
        {{ EVENT_LABELS[e.type] || e.type }}
        <span v-if="e.meta && e.meta.delayed" class="badge warn">门禁回执延迟到达</span>
      </div>
      <div v-if="e.detail" class="t-detail">{{ e.detail }}</div>
    </div>
    <div v-if="!events.length" class="muted">暂无事件</div>
  </div>
</template>

<script setup>
import { EVENT_LABELS } from '../util'
import { fmtDt } from '../util'

defineProps({ events: { type: Array, default: () => [] } })

function tone(type) {
  const map = {
    WORK_PAUSED: 'warn',
    GUARDIAN_DEPARTED: 'danger',
    RESUME_REQUESTED: 'warn',
    RESUME_REJECTED: 'danger',
    WORK_RESUMED: 'ok',
  }
  return map[type] || ''
}
</script>
