<template>
  <div>
    <div v-if="loading" class="muted">加载历史…</div>
    <div v-else-if="!records.length" class="muted">该许可暂无交接记录。</div>
    <div v-for="rec in records" :key="rec.handover.id" class="history-block">
      <div class="row" style="margin-bottom:8px">
        <b>交接单 #{{ rec.handover.id }}</b>
        <span class="badge" :class="statusTone(rec.handover.status)">
          {{ HANDOVER_STATUS_LABELS[rec.handover.status] || rec.handover.status }}
        </span>
        <span class="muted">
          {{ rec.handover.outgoing_guardian.name }} → {{ rec.handover.incoming_guardian.name }}
        </span>
        <span v-if="rec.handover.locked_gap_seconds != null" class="badge warn">
          无人监护 {{ fmtDuration(rec.handover.locked_gap_seconds) }}
        </span>
      </div>
      <Timeline :events="rec.timeline" />
      <div v-for="q in rec.requests" :key="q.id" class="req-line">
        <span class="badge" :class="q.status==='APPROVED'?'ok':q.status==='REJECTED'?'danger':'warn'">
          {{ q.status === 'APPROVED' ? '复工已批准' : q.status === 'REJECTED' ? '复工被驳回' : '待审批' }}
        </span>
        <span class="muted">申请：{{ q.requested_by_name }} · {{ fmtDt(q.requested_at) }}</span>
        <span v-if="q.reject_reasons && q.reject_reasons.length" class="muted">
          原因：{{ q.reject_reasons.join('；') }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { api } from '../api'
import Timeline from './Timeline.vue'
import { fmtDt, fmtDuration, HANDOVER_STATUS_LABELS } from '../util'

const props = defineProps({
  permitId: { type: Number, required: true },
  refreshKey: { type: Number, default: 0 },
})
const records = ref([])
const loading = ref(false)

function statusTone(s) {
  return s === 'RESUMED' ? 'ok' : s === 'CANCELLED' ? 'gray' : 'info'
}

async function load() {
  loading.value = true
  try {
    records.value = await api.get(`/api/permits/${props.permitId}/handovers`)
  } finally {
    loading.value = false
  }
}
watch(() => [props.permitId, props.refreshKey], load, { immediate: true })
</script>

<style scoped>
.history-block { padding: 10px 0 14px; border-bottom: 1px dashed var(--line); }
.history-block:last-child { border-bottom: none; }
.req-line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0 0; }
</style>
