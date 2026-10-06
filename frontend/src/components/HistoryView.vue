<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const jobs = ref([])
const selected = ref('')
const events = ref([])
const error = ref('')

const KIND_LABEL = {
  paused: '停工交接',
  outgoing_left: '原监护人离场',
  incoming_arrived: '接班人到场接班',
  signed: '签认',
  completed: '双方交接完成',
  resume_requested: '申请复工',
  resume_rejected: '复工被驳回',
  resumed: '批准复工',
}
const KIND_ICON = {
  paused: '⏸️', outgoing_left: '🚪离开', incoming_arrived: '🚪到场',
  signed: '✍️', completed: '🤝', resume_requested: '📤',
  resume_rejected: '⛔', resumed: '▶️',
}

async function loadJobs() {
  jobs.value = await api.get('/api/jobs')
  if (jobs.value.length) {
    selected.value = String(jobs.value[0].id)
    await loadTimeline()
  }
}
async function loadTimeline() {
  if (!selected.value) return
  error.value = ''
  try {
    events.value = await api.get(`/api/jobs/${selected.value}/timeline`)
  } catch (e) {
    error.value = e.message
  }
}
function fmt(t) {
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

onMounted(loadJobs)
</script>

<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin:0">🕑 交接历史时间线</h2>
      <select v-model="selected" @change="loadTimeline" style="width: 300px">
        <option v-for="j in jobs" :key="j.id" :value="String(j.id)">
          {{ j.name }}（{{ j.location }}）
        </option>
      </select>
    </div>

    <div class="alert err" v-if="error">{{ error }}</div>

    <div class="empty" v-if="!events.length && !error">
      该作业暂无交接记录。
    </div>

    <div class="timeline" style="margin-top: 18px">
      <div class="tl-item" :class="'k-' + e.kind" v-for="e in events" :key="e.id">
        <div class="tl-time">{{ fmt(e.at) }} · {{ KIND_ICON[e.kind] || '•' }}</div>
        <div>
          <strong>{{ KIND_LABEL[e.kind] || e.kind }}</strong>
          <span class="muted small" v-if="e.actor_login"> · {{ e.actor_login }}</span>
        </div>
        <div class="small" style="color: var(--text); opacity:.85">{{ e.message }}</div>
      </div>
    </div>

    <p class="small muted" style="margin-top: 16px">
      时间线严格按实际发生时刻排序，可清楚看到「停工 → 离场 → 接班到场 → 签认 → 申请复工 → 复工」的先后；
      门禁延迟回执会标注其延迟秒数，但时间位置仍以实际刷卡时刻为准。
    </p>
  </div>
</template>
