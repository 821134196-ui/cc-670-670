<script setup>
import { ref, onMounted, computed } from 'vue'
import { api, getLogin } from '../api.js'

const overview = ref({ unattended_windows: [], pending_count: 0 })
const requests = ref([])
const persons = ref([])
const error = ref('')
const okMsg = ref('')
const rejectReason = ref({})

const me = computed(() => persons.value.find((p) => p.login === getLogin()) || null)
const isLeader = computed(() => me.value?.role === 'leader')
const leaderLogin = computed(() => me.value?.login || '')

function gapText(s) {
  if (!s) return '0 秒'
  if (s < 60) return `${Math.round(s)} 秒`
  return `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`
}
function fmt(t) {
  return t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '—'
}

async function load() {
  error.value = ''
  try {
    const [ov, reqs, ps] = await Promise.all([
      api.get('/api/leader/overview'),
      api.get('/api/resume-requests'),
      api.get('/api/persons'),
    ])
    overview.value = ov
    requests.value = reqs
    persons.value = ps
  } catch (e) {
    error.value = e.message
  }
}

async function decide(rid, approve) {
  if (!isLeader.value) {
    error.value = '只有班组长本人账号可以审批，请在右上角切换为班组长登录。'
    return
  }
  if (!approve && !rejectReason.value[rid]) {
    error.value = '驳回复工必须填写原因'
    return
  }
  try {
    await api.post(`/api/resume-requests/${rid}/decision`, {
      leader_login: leaderLogin.value,
      approve,
      reject_reason: approve ? null : rejectReason.value[rid],
    })
    okMsg.value = approve ? '已批准复工' : '已驳回复工'
    error.value = ''
    rejectReason.value = {}
    await load()
    setTimeout(() => (okMsg.value = ''), 3000)
  } catch (e) {
    error.value = e.message
  }
}

const STATUS = { pending: '待审批', approved: '已批准', rejected: '已驳回' }

onMounted(load)
</script>

<template>
  <div class="grid">
    <div class="alert err" v-if="error">{{ error }}</div>
    <div class="alert ok" v-if="okMsg">{{ okMsg }}</div>

    <!-- 无人监护时间段总览 -->
    <div class="card">
      <h2>🕓 当前无人监护时间段（停工换班空档）</h2>
      <div class="empty" v-if="!overview.unattended_windows.length">
        当前没有进行中的交接空档。
      </div>
      <ul class="clean">
        <li v-for="w in overview.unattended_windows" :key="w.job_id">
          <span class="dot" :class="w.gap.seconds > 0 ? 'warn' : 'ok'"></span>
          <div style="flex:1">
            <strong>{{ w.job_name }}</strong>
            <span class="muted small">（{{ w.location }}） {{ w.outgoing }} → {{ w.incoming }}</span>
            <div class="small muted">
              <template v-if="w.gap.open">⚠️ 空档持续中，自 {{ fmt(w.gap.start) }} 起；接班人未到场</template>
              <template v-else-if="w.gap.seconds > 0">
                空档 {{ gapText(w.gap.seconds) }}：{{ fmt(w.gap.start) }} → {{ fmt(w.gap.end) }}
              </template>
              <template v-else>无空档（重叠在场/原监护人未离场）</template>
            </div>
          </div>
          <span class="metric" :class="w.gap.seconds > 0 ? 'warn' : 'ok'" style="font-size:18px">
            {{ gapText(w.gap.seconds) }}
          </span>
        </li>
      </ul>
    </div>

    <!-- 复工申请 -->
    <div class="card">
      <h2>📥 复工申请审批</h2>
      <div class="alert warn" v-if="!isLeader">
        当前登录人（{{ me?.name || '未登录' }}）不是班组长，审批按钮不可用。请在右上角切换为班组长本人账号。
      </div>
      <div class="empty" v-if="!requests.length">暂无复工申请。</div>
      <div v-for="r in requests" :key="r.request.id" style="border-bottom:1px solid var(--line); padding:14px 0">
        <div class="row" style="justify-content: space-between">
          <div>
            <strong>{{ r.job_name }}</strong>
            <span class="muted small"> #{{ r.handover_id }} · {{ r.outgoing }} → {{ r.incoming }}</span>
          </div>
          <span class="tag"
            :class="{ paused: r.request.status === 'pending', running: r.request.status === 'approved', danger: r.request.status === 'rejected' }">
            {{ STATUS[r.request.status] }}
          </span>
        </div>

        <div class="small muted" style="margin:6px 0">
          申请于 {{ fmt(r.request.requested_at) }} · 交接空档 {{ gapText(r.gap_seconds) }}
        </div>

        <div v-if="r.blockers.length" class="alert err" style="margin:8px 0">
          <strong>系统拦截（不允许复工）：</strong>
          <ul class="clean">
            <li v-for="(b, i) in r.blockers" :key="i"><span class="dot bad"></span>{{ b.message }}</li>
          </ul>
        </div>
        <div v-else-if="r.request.status === 'pending'" class="alert ok">
          ✅ 资质、许可、现场监护均满足，可批准复工。
        </div>
        <div class="alert err" v-if="r.request.status === 'rejected'">驳回：{{ r.request.reject_reason }}</div>

        <div class="row" v-if="r.request.status === 'pending'">
          <button class="btn ok"
            :disabled="r.blockers.length > 0 || !isLeader"
            @click="decide(r.request.id, true)">
            {{ r.blockers.length ? '条件不满足·禁止批准' : '批准复工' }}
          </button>
          <input type="text" v-model="rejectReason[r.request.id]"
            placeholder="驳回原因（必填）" style="flex:1" />
          <button class="btn danger" :disabled="!isLeader"
            @click="decide(r.request.id, false)">驳回复工</button>
        </div>
      </div>
    </div>
  </div>
</template>
