<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  jobId: { type: Number, default: null },
  persons: { type: Array, default: () => [] },
  currentLogin: { type: String, default: '' },
  refreshKey: { type: Number, default: 0 },
})
const emit = defineEmits(['pick', 'changed'])

const jobs = ref([])
const job = ref(null)
const detail = ref(null) // active handover + gap + blockers
const error = ref('')
const okMsg = ref('')

const incomingId = ref(null)
const swipePersonId = ref(null)
const swipeDelay = ref(0)

const me = computed(() => props.persons.find((p) => p.login === props.currentLogin) || null)
const activeJobId = computed(() => props.jobId || job.value?.id || null)
const h = computed(() => detail.value?.active || null)

const guardians = computed(() => props.persons.filter((p) => p.role === 'guardian'))
function person(id) {
  return props.persons.find((p) => p.id === id)
}
function personByLogin(l) {
  return props.persons.find((p) => p.login === l)
}

const outgoing = computed(() => (h.value ? person(h.value.outgoing_id) : null))
const incoming = computed(() => (h.value ? person(h.value.incoming_id) : null))

const STATUS_LABEL = { signing: '签认中', completed: '双方已签·待复工', resumed: '已复工' }

function fmt(t) {
  return t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '—'
}
function gapText(s) {
  if (!s) return '0 秒'
  if (s < 60) return `${Math.round(s)} 秒`
  return `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`
}

function certState(p, certName) {
  if (!p) return { ok: false, text: '—' }
  if (p.required_cert !== certName) return { ok: false, text: '证书类型不符' }
  const exp = p.cert_expires_at ? new Date(p.cert_expires_at).getTime() : 0
  if (exp < Date.now()) return { ok: false, text: '资质已过期' }
  return { ok: true, text: `有效至 ${fmt(p.cert_expires_at)}` }
}

// ── 数据加载 ──
async function loadJobs() {
  jobs.value = await api.get('/api/jobs')
  if (!props.jobId && jobs.value.length) job.value = jobs.value[0]
}
async function load() {
  if (!activeJobId.value) return
  error.value = ''
  try {
    job.value = await api.get(`/api/jobs/${activeJobId.value}`)
    detail.value = await api.get(`/api/jobs/${activeJobId.value}/handover`)
  } catch (e) {
    error.value = e.message
  }
}

watch(activeJobId, load)
watch(() => props.refreshKey, load)
onMounted(async () => {
  await loadJobs()
  await load()
})

function selectJob(id) {
  emit('pick', Number(id))
}

function flash(err = null, ok = '') {
  error.value = err
  okMsg.value = ok
  setTimeout(() => (okMsg.value = ''), 3500)
}

// ── 动作 ──
async function startHandover() {
  if (!incomingId.value) return flash('请先选择接班人')
  try {
    await api.post('/api/handovers/start', {
      job_id: activeJobId.value,
      outgoing_login: props.currentLogin,
      incoming_id: Number(incomingId.value),
    })
    flash(null, '已停工，交接开始。请双方到现场刷卡并核对签认。')
    emit('changed')
    await load()
  } catch (e) {
    flash(e.message)
  }
}

async function swipe(direction) {
  const pid = swipePersonId.value || me.value?.id
  if (!pid) return flash('请选择刷卡人员')
  const body = {
    person_id: Number(pid),
    direction,
    location: job.value.location,
  }
  if (Number(swipeDelay.value) > 0) {
    const t = new Date(Date.now() - Number(swipeDelay.value) * 1000)
    body.occurred_at = t.toISOString()
  }
  try {
    await api.post('/api/access', body)
    flash(null, `已记录${direction === 'in' ? '进入' : '离开'}刷卡` +
      (Number(swipeDelay.value) > 0 ? `（回执延迟 ${swipeDelay.value} 秒，按实际刷卡时间计）` : ''))
    emit('changed')
    await load()
  } catch (e) {
    flash(e.message)
  }
}

async function sign(role) {
  const expectedLogin = role === 'outgoing' ? outgoing.value?.login : incoming.value?.login
  try {
    await api.post(`/api/handovers/${h.value.id}/sign`, {
      role,
      login: props.currentLogin,
      risks_confirmed: true,
      isolations_confirmed: true,
    })
    flash(null, '签认成功' + (props.currentLogin !== expectedLogin ? '' : ''))
    emit('changed')
    await load()
  } catch (e) {
    flash(e.message)
  }
}

async function requestResume() {
  try {
    await api.post(`/api/handovers/${h.value.id}/resume-request`, { login: props.currentLogin })
    flash(null, '复工申请已提交，等待班组长审批。')
    emit('changed')
    await load()
  } catch (e) {
    flash(e.message)
  }
}

const signOf = (role) => h.value?.signoffs?.find((s) => s.role === role) || null
const canStart = computed(() =>
  job.value?.status === 'running' &&
  me.value?.id === job.value?.guardian_id &&
  incomingId.value &&
  Number(incomingId.value) !== me.value?.id
)
</script>

<template>
  <div v-if="!job">
    <div class="card">
      <h2>选择作业</h2>
      <select @change="selectJob($event.target.value)" style="width: 100%">
        <option value="">— 请选择 —</option>
        <option v-for="j in jobs" :key="j.id" :value="j.id">{{ j.name }}（{{ j.location }}）</option>
      </select>
    </div>
  </div>

  <div v-else class="grid">
    <div class="alert err" v-if="error">{{ error }}</div>
    <div class="alert ok" v-if="okMsg">{{ okMsg }}</div>

    <!-- 作业与许可 -->
    <div class="card">
      <div class="row" style="justify-content: space-between">
        <h2 style="margin: 0">{{ job.name }}</h2>
        <select :value="activeJobId" @change="selectJob($event.target.value)" style="width: 260px">
          <option v-for="j in jobs" :key="j.id" :value="j.id">{{ j.name }}</option>
        </select>
      </div>
      <div class="muted small" style="margin: 6px 0 12px">📍 {{ job.location }} ·
        当前监护：{{ persons.find(p => p.id === job.guardian_id)?.name }}</div>

      <h3>作业许可（交接页带出）</h3>
      <div class="kv">
        <span class="k">许可编号</span><span>{{ job.permit.code }} · {{ job.permit.title }}</span>
        <span class="k">有效期</span>
        <span :class="new Date(job.permit.valid_until).getTime() < Date.now() ? 'tag danger' : ''">
          {{ fmt(job.permit.valid_from) }} ～ {{ fmt(job.permit.valid_until) }}
          <span v-if="job.permit.revoked" class="tag danger">已撤销</span>
        </span>
        <span class="k">所需资质</span><span>{{ job.permit.required_cert }}</span>
      </div>

      <div class="grid cols-2" style="margin-top: 6px">
        <div>
          <h3>⚠️ 风险（须双方核对）</h3>
          <ul class="clean">
            <li v-for="r in job.risks" :key="r.id"><span class="dot warn"></span>{{ r.content }}</li>
          </ul>
        </div>
        <div>
          <h3>🔒 隔离措施（须双方核对）</h3>
          <ul class="clean">
            <li v-for="iso in job.isolations" :key="iso.id">
              <span class="dot" :class="iso.intact ? 'ok' : 'bad'"></span>
              {{ iso.content }}
              <span class="tag" :class="iso.intact ? 'running' : 'danger'" style="margin-left:auto">
                {{ iso.intact ? '有效' : '已失效' }}
              </span>
            </li>
          </ul>
        </div>
      </div>

      <h3>📝 未完成事项</h3>
      <ul class="clean">
        <li v-for="p in job.pending_items" :key="p.id">
          <span class="dot" :class="p.done ? 'ok' : 'mut'"></span>
          {{ p.content }}
          <span class="tag" style="margin-left:auto">{{ p.done ? '已完成' : '未完成' }}</span>
        </li>
      </ul>
    </div>

    <!-- 发起交接（无进行中交接时） -->
    <div class="card" v-if="!h && job.status === 'running'">
      <h2>停工并更换监护人</h2>
      <p class="muted small">
        更换监护人须先停工。请选择接班人；接班人必须持有与许可匹配且在有效期内的资质。
      </p>
      <div class="row">
        <label class="small muted">接班人</label>
        <select v-model="incomingId">
          <option value="">— 选择接班人 —</option>
          <option v-for="g in guardians.filter(x => x.id !== job.guardian_id)" :key="g.id" :value="g.id">
            {{ g.name }}（{{ g.login }}）
          </option>
        </select>
        <span v-if="incomingId" class="tag"
          :class="certState(person(Number(incomingId)), job.permit.required_cert).ok ? 'running' : 'danger'">
          资质：{{ certState(person(Number(incomingId)), job.permit.required_cert).text }}
        </span>
        <div class="spacer"></div>
        <button class="btn" :disabled="!canStart" @click="startHandover">
          {{ me?.id === job.guardian_id ? '停工并开始交接' : `需当前监护人 ${persons.find(p=>p.id===job.guardian_id)?.name} 登录发起` }}
        </button>
      </div>
      <div class="alert warn" v-if="me?.id !== job.guardian_id" style="margin-bottom:0">
        当前登录人不是该作业监护人，不能代其发起停工交接（一人一账号）。
      </div>
    </div>

    <!-- 进行中交接 -->
    <div class="card" v-if="h">
      <div class="row" style="justify-content: space-between">
        <h2 style="margin:0">交接 #{{ h.id }}
          <span class="tag paused">{{ STATUS_LABEL[h.status] }}</span>
        </h2>
        <div>
          <span class="muted small">停工于 {{ fmt(h.pause_time) }}</span>
        </div>
      </div>

      <div class="grid cols-2" style="margin-top: 12px">
        <div class="kv">
          <span class="k">原监护人</span>
          <span>{{ outgoing?.name }}（{{ outgoing?.login }}）</span>
          <span class="k">接班人</span>
          <span>{{ incoming?.name }}（{{ incoming?.login }}）
            <span class="tag"
              :class="certState(incoming, job.permit.required_cert).ok ? 'running' : 'danger'">
              {{ certState(incoming, job.permit.required_cert).text }}
            </span>
          </span>
        </div>
        <div>
          <h3 style="margin-top:0">无人监护时间段</h3>
          <div class="metric" :class="detail.gap.seconds > 0 ? 'warn' : 'ok'">
            {{ gapText(detail.gap.seconds) }}
          </div>
          <div class="small muted">
            <template v-if="detail.gap.open">⚠️ 空档持续中：接班人尚未到场（{{ fmt(detail.gap.start) }} 起）</template>
            <template v-else-if="detail.gap.seconds > 0">
              {{ fmt(detail.gap.start) }} → {{ fmt(detail.gap.end) }}
            </template>
            <template v-else>✅ 当面重叠交接，无无人监护空档</template>
          </div>
        </div>
      </div>

      <!-- 门禁模拟 -->
      <h3>🚪 现场门禁（本地模拟，可模拟延迟回执）</h3>
      <div class="row">
        <select v-model="swipePersonId">
          <option :value="null">当前登录人（{{ me?.name }}）</option>
          <option v-for="p in [outgoing, incoming].filter(Boolean)" :key="p.id" :value="p.id">
            {{ p.name }}（{{ p.login }}）
          </option>
        </select>
        <label class="small muted">回执延迟(秒)</label>
        <input type="text" v-model="swipeDelay" style="width: 90px" placeholder="0" />
        <button class="btn ghost" @click="swipe('in')">刷卡进入</button>
        <button class="btn ghost" @click="swipe('out')">刷卡离开</button>
        <span class="small muted" style="width:100%">延迟回执只影响到达时间，系统始终按实际刷卡时刻判定在场，不会补造在场时间。</span>
      </div>

      <!-- 双方签认 -->
      <h3>✍️ 双方核对签认（各自本人账号，禁止代签）</h3>
      <div class="grid cols-2">
        <div class="signbox" :class="{ done: signOf('outgoing') }">
          <div class="row" style="justify-content: space-between">
            <strong>原监护人：{{ outgoing?.name }}</strong>
            <span class="tag" :class="signOf('outgoing') ? 'running' : 'paused'">
              {{ signOf('outgoing') ? '已签认' : '待签认' }}
            </span>
          </div>
          <div class="small muted" v-if="signOf('outgoing')">
            {{ fmt(signOf('outgoing').signed_at) }} · 账号 {{ signOf('outgoing').login }}
          </div>
          <div class="small" style="margin: 8px 0">我已核对风险与隔离措施，并向接班人交接清楚。</div>
          <button class="btn"
            :disabled="!!signOf('outgoing') || currentLogin !== outgoing?.login"
            @click="sign('outgoing')">
            {{ currentLogin === outgoing?.login ? '原监护人签认' : `需 ${outgoing?.login} 本人登录` }}
          </button>
        </div>

        <div class="signbox" :class="{ done: signOf('incoming') }">
          <div class="row" style="justify-content: space-between">
            <strong>接班人：{{ incoming?.name }}</strong>
            <span class="tag" :class="signOf('incoming') ? 'running' : 'paused'">
              {{ signOf('incoming') ? '已签认' : '待签认' }}
            </span>
          </div>
          <div class="small muted" v-if="signOf('incoming')">
            {{ fmt(signOf('incoming').signed_at) }} · 账号 {{ signOf('incoming').login }}
          </div>
          <div class="small" style="margin: 8px 0">我已到现场，逐项确认风险与隔离措施并接管监护职责。</div>
          <button class="btn"
            :disabled="!!signOf('incoming') || currentLogin !== incoming?.login"
            @click="sign('incoming')">
            {{ currentLogin === incoming?.login ? '接班人签认' : `需 ${incoming?.login} 本人登录` }}
          </button>
        </div>
      </div>

      <!-- 复工 -->
      <template v-if="h.status === 'completed'">
        <h3>🚦 复工申请</h3>
        <div v-if="detail.resume_blockers.length" class="alert err">
          <strong>系统判定不允许复工：</strong>
          <ul class="clean" style="margin-top:6px">
            <li v-for="(b, i) in detail.resume_blockers" :key="i">
              <span class="dot bad"></span>{{ b.message }}
            </li>
          </ul>
        </div>
        <div v-else class="alert ok">✅ 资质、许可、现场监护三项条件均满足，可申请复工。</div>

        <div class="row" style="margin-top:8px">
          <button class="btn ok" :disabled="currentLogin !== incoming?.login" @click="requestResume">
            {{ currentLogin === incoming?.login ? '接班人申请复工' : `需接班人 ${incoming?.name} 登录申请` }}
          </button>
          <span v-if="detail.resume" class="tag"
            :class="{ running: detail.resume.status === 'approved', paused: detail.resume.status === 'pending', danger: detail.resume.status === 'rejected' }">
            申请状态：{{ { pending: '待班组长审批', approved: '已批准', rejected: '已驳回' }[detail.resume.status] }}
          </span>
          <span v-if="detail.resume?.reject_reason" class="small muted">
            驳回原因：{{ detail.resume.reject_reason }}
          </span>
        </div>
        <div class="small muted" style="margin-top:8px">
          复工由「班组长复核」页审批；任一硬条件不满足时，班组长也无法放行。
        </div>
      </template>
    </div>
  </div>
</template>
