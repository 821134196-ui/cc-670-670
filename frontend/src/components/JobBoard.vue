<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

defineProps({ persons: { type: Array, default: () => [] } })
const emit = defineEmits(['open'])

const jobs = ref([])
const error = ref('')

const STATUS = { running: '进行中', paused: '停工交接中', resumed: '进行中', closed: '已关闭' }
const statusClass = (s) => (s === 'paused' ? 'paused' : 'running')

async function load() {
  error.value = ''
  try {
    jobs.value = await api.get('/api/jobs')
  } catch (e) {
    error.value = e.message
  }
}

function fmt(t) {
  return t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '—'
}
function permitState(job) {
  const now = Date.now()
  const until = new Date(job.permit.valid_until).getTime()
  const from = new Date(job.permit.valid_from).getTime()
  if (job.permit.revoked) return { text: '已撤销', cls: 'danger' }
  if (now > until || now < from) return { text: '已过期', cls: 'danger' }
  return { text: '有效', cls: 'running' }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="alert err" v-if="error">{{ error }}</div>
    <div class="grid cols-2">
      <div class="card" v-for="job in jobs" :key="job.id">
        <div class="row" style="justify-content: space-between">
          <h2 style="margin: 0">{{ job.name }}</h2>
          <span class="tag" :class="statusClass(job.status)">{{ STATUS[job.status] }}</span>
        </div>
        <div class="muted small" style="margin: 6px 0 12px">
          📍 {{ job.location }}
        </div>

        <div class="kv">
          <span class="k">作业许可</span>
          <span>{{ job.permit.code }} · {{ job.permit.title }}</span>
          <span class="k">许可状态</span>
          <span><span class="tag" :class="permitState(job).cls">{{ permitState(job).text }}</span>
            <span class="muted small"> 至 {{ fmt(job.permit.valid_until) }}</span></span>
          <span class="k">当前监护</span>
          <span>{{ persons.find(p => p.id === job.guardian_id)?.name || '—' }}</span>
          <span class="k">要求资质</span>
          <span>{{ job.permit.required_cert }}</span>
        </div>

        <div class="row" style="margin-top: 14px; justify-content: flex-end">
          <button class="btn" @click="emit('open', job.id)">
            {{ job.status === 'paused' ? '查看交接 / 复工' : '停工交接换人' }}
          </button>
        </div>
      </div>
    </div>
    <div class="empty" v-if="!jobs.length && !error">暂无作业</div>
  </div>
</template>
