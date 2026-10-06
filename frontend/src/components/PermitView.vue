<template>
  <div class="permit-view">
    <div class="row" style="margin-bottom:12px">
      <select v-model="permitId" @change="load" class="permit-select">
        <option v-for="p in permits" :key="p.id" :value="p.id">
          {{ p.permit_no }} · {{ p.title }}（{{ workLabel(p.work_status) }}）
        </option>
      </select>
      <button class="btn ghost sm" @click="load">刷新</button>
    </div>

    <template v-if="page">
      <!-- 许可与作业状态 -->
      <div class="card">
        <h3>
          作业许可
          <span class="badge" :class="page.permit.valid ? 'ok' : 'danger'">
            {{ page.permit.valid ? '许可有效' : '许可已过期/关闭' }}
          </span>
          <span class="badge info">{{ workLabel(page.permit.work_status) }}</span>
        </h3>
        <dl class="kv">
          <dt>许可编号</dt><dd class="mono">{{ page.permit.permit_no }}</dd>
          <dt>作业内容</dt><dd>{{ page.permit.title }}</dd>
          <dt>作业类型</dt><dd>{{ page.permit.work_type }}</dd>
          <dt>作业地点</dt><dd>{{ page.permit.location }}</dd>
          <dt>有效期限</dt>
          <dd>{{ fmtDt(page.permit.valid_from) }} 至 {{ fmtDt(page.permit.valid_until) }}</dd>
          <dt>当前监护人</dt>
          <dd>
            {{ page.permit.current_guardian ? page.permit.current_guardian.name : '无' }}
            <span v-if="page.permit.current_guardian"
                  class="badge" :class="page.permit.current_guardian.qualification_valid ? 'ok' : 'danger'">
              {{ page.permit.current_guardian.qualification_type }}
              {{ page.permit.current_guardian.qualification_valid ? '（有效）' : '（已过期）' }}
            </span>
          </dd>
        </dl>
      </div>

      <!-- 发起交接（停工） -->
      <div v-if="!page.open_handover && page.permit.work_status === 'IN_PROGRESS'" class="card">
        <h3>更换监护人 · 停工交接</h3>
        <p class="muted">更换监护人须先停工；停工后把风险与隔离措施交接清楚，双方分别签认，再申请复工。</p>
        <div class="row">
          <label>接班监护人：</label>
          <select v-model="incomingId">
            <option v-for="g in guardians" :key="g.id" :value="g.id">
              {{ g.name }}（{{ g.qualification_type || '无资质' }}
              {{ g.qualification_valid ? '· 有效' : '· 已过期' }}）
            </option>
          </select>
          <button class="btn" :disabled="!incomingId || busy" @click="pause">停工并发起交接</button>
        </div>
        <div v-if="incomingGuardian && !incomingGuardian.qualification_valid" class="alert warn">
          所选接班人资质不符或已过期：可以完成现场交接，但复工时系统将不予放行。
        </div>
      </div>

      <!-- 进行中的交接单 -->
      <div v-if="ho" class="card">
        <h3>
          交接单 #{{ ho.id }}
          <span class="badge info">{{ handoverLabel(ho.status) }}</span>
        </h3>

        <div class="guard-cols">
          <div class="guard-col">
            <div class="gc-title">原监护人</div>
            <div class="gc-name">{{ ho.outgoing_guardian.name }}</div>
            <div class="badge" :class="ho.outgoing_guardian.qualification_valid ? 'ok' : 'danger'">
              {{ ho.outgoing_guardian.qualification_type }}
            </div>
            <div class="badge" :class="ho.outgoing_on_site ? 'ok' : 'gray'">
              {{ ho.outgoing_on_site ? '门禁在场' : '无在场记录' }}
            </div>
            <div class="sign-time">
              签认：{{ ho.outgoing_signed_at ? fmtDt(ho.outgoing_signed_at) : '未签认' }}
            </div>
          </div>
          <div class="guard-arrow">→</div>
          <div class="guard-col">
            <div class="gc-title">接班监护人</div>
            <div class="gc-name">{{ ho.incoming_guardian.name }}</div>
            <div class="badge" :class="ho.incoming_guardian.qualification_valid ? 'ok' : 'danger'">
              {{ ho.incoming_guardian.qualification_type || '无资质' }}
              {{ ho.incoming_guardian.qualification_valid ? '（有效）' : '（已过期）' }}
            </div>
            <div class="badge" :class="ho.incoming_on_site ? 'ok' : 'danger'">
              {{ ho.incoming_on_site ? '门禁在场' : '无在场记录' }}
            </div>
            <div class="sign-time">
              签认：{{ ho.incoming_signed_at ? fmtDt(ho.incoming_signed_at) : '未签认' }}
            </div>
          </div>
        </div>

        <!-- 无人监护时段 -->
        <GapPanel :gap="ho.gap" :status="ho.status" :locked="ho.locked_gap_seconds" />

        <!-- 风险与隔离措施逐条核对 -->
        <h3 style="margin-top:16px">风险与隔离措施 · 逐条核对</h3>
        <ChecklistSign
          title="原监护人核对"
          :items="page.items"
          :checks="outChecks"
          :disabled="ho.status !== 'PENDING' || me.id !== ho.outgoing_guardian.id"
          :signed="!!ho.outgoing_signed_at"
          @toggle="(id,v)=>outChecks[id]=v"
          @confirm-item="confirmItem"
        />
        <ChecklistSign
          title="接班人核对"
          :items="page.items"
          :checks="inChecks"
          :disabled="ho.status !== 'OUTGOING_SIGNED' || me.id !== ho.incoming_guardian.id"
          :signed="!!ho.incoming_signed_at"
          @toggle="(id,v)=>inChecks[id]=v"
          @confirm-item="confirmItem"
        />

        <!-- 操作按钮 -->
        <div class="row" style="margin-top:12px">
          <button v-if="ho.status==='PENDING'" class="btn"
                  :disabled="busy || me.id !== ho.outgoing_guardian.id"
                  @click="sign('OUTGOING', outChecks)">
            原监护人签认（需本人账号、本人在场）
          </button>
          <button v-if="ho.status==='OUTGOING_SIGNED'" class="btn"
                  :disabled="busy || me.id !== ho.incoming_guardian.id"
                  @click="sign('INCOMING', inChecks)">
            接班人签认（需本人账号、本人在场）
          </button>
          <button v-if="ho.status==='FULLY_SIGNED'" class="btn" @click="requestResume"
                  :disabled="busy">
            申请复工（系统复核 + 班组长审批）
          </button>
          <span v-if="me.id !== ho.outgoing_guardian.id && ho.status==='PENDING'" class="muted">
            需由原监护人 {{ ho.outgoing_guardian.name }} 本人账号签认
          </span>
          <span v-if="me.id !== ho.incoming_guardian.id && ho.status==='OUTGOING_SIGNED'" class="muted">
            需由接班人 {{ ho.incoming_guardian.name }} 本人账号签认
          </span>
        </div>
      </div>

      <!-- 门禁模拟 -->
      <GateSimulator :guardians="gateTargets" @changed="load" />

      <!-- 近期门禁回执 -->
      <div class="card">
        <h3>门禁回执（按物理过闸时间）</h3>
        <table>
          <thead><tr><th>人员</th><th>方向</th><th>过闸时间</th><th>回执到达</th><th>备注</th></tr></thead>
          <tbody>
            <tr v-for="e in page.access_events" :key="e.id">
              <td>{{ e.person_name }}</td>
              <td>{{ e.direction === 'IN' ? '入场' : '离场' }}</td>
              <td class="mono">{{ fmtDt(e.event_time) }}</td>
              <td>
                <span class="mono">{{ fmtDt(e.received_at) }}</span>
                <span v-if="e.delayed" class="badge warn" style="margin-left:6px">延迟</span>
              </td>
              <td class="muted">{{ e.note }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" style="margin-bottom:0">
          规则：系统只依据“已到达”的回执判断在场；延迟到达的回执不能补出回执到达之前的在场时间。
        </p>
      </div>

      <!-- 交接历史 -->
      <div class="card">
        <h3>交接历史时间线</h3>
        <HistoryList :permit-id="page.permit.id" :refresh-key="refreshKey" />
      </div>

      <!-- 通知 -->
      <div class="card">
        <h3>模拟通知记录</h3>
        <ul class="note-list">
          <li v-for="(n, i) in page.notifications" :key="i" class="muted">{{ n }}</li>
        </ul>
      </div>
    </template>

    <div v-if="error" class="alert danger">{{ error }}</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api, nonce } from '../api'
import { fmtDt, fmtDuration, HANDOVER_STATUS_LABELS, WORK_STATUS_LABELS } from '../util'
import GapPanel from './GapPanel.vue'
import ChecklistSign from './ChecklistSign.vue'
import GateSimulator from './GateSimulator.vue'
import HistoryList from './HistoryList.vue'

const props = defineProps({
  me: { type: Object, required: true },
  permits: { type: Array, default: () => [] },
  guardians: { type: Array, default: () => [] },
})

const permitId = ref(props.permits[0]?.id ?? null)
const page = ref(null)
const busy = ref(false)
const error = ref('')
const incomingId = ref(null)
const outChecks = ref({})
const inChecks = ref({})
const refreshKey = ref(0)

const ho = computed(() => page.value?.open_handover || null)
const incomingGuardian = computed(() =>
  props.guardians.find((g) => g.id === incomingId.value))
const gateTargets = computed(() => {
  if (!page.value) return props.guardians
  const ids = new Set()
  if (ho.value) { ids.add(ho.value.outgoing_guardian.id); ids.add(ho.value.incoming_guardian.id) }
  if (page.value.permit.current_guardian) ids.add(page.value.permit.current_guardian.id)
  return props.guardians.filter((g) => ids.has(g.id))
})

function workLabel(s) { return WORK_STATUS_LABELS[s] || s }
function handoverLabel(s) { return HANDOVER_STATUS_LABELS[s] || s }

watch(() => props.permits, (list) => {
  if (!permitId.value && list.length) permitId.value = list[0].id
})

async function load() {
  error.value = ''
  if (!permitId.value) return
  try {
    const data = await api.get(`/api/permits/${permitId.value}/handover-page`)
    page.value = data
    if (!incomingId.value) {
      const other = props.guardians.find(
        (g) => g.id !== data.permit.current_guardian?.id)
      incomingId.value = other?.id ?? null
    }
    // 已签认方的核对项视为全部确认，用于展示勾选状态
    if (data.open_handover) {
      const ids = Object.fromEntries(data.items
        .filter((i) => i.kind === 'RISK' || i.kind === 'ISOLATION')
        .map((i) => [String(i.id), true]))
      if (data.open_handover.outgoing_signed_at) outChecks.value = { ...ids }
      if (data.open_handover.incoming_signed_at) inChecks.value = { ...ids }
    }
    refreshKey.value++
  } catch (e) {
    error.value = e.message
  }
}

async function pause() {
  busy.value = true
  error.value = ''
  try {
    await api.post(`/api/permits/${permitId.value}/pause`, {
      incoming_guardian_id: incomingId.value,
    })
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}

async function sign(role, checks) {
  const unconfirmed = Object.values(checks).filter((v) => !v).length
  if (unconfirmed) {
    error.value = '风险与隔离措施必须逐条全部核对确认后才能签认'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await api.post(`/api/handovers/${ho.value.id}/sign`, {
      role, item_checks: checks, nonce: nonce(),
    })
    await load()
  } catch (e) {
    error.value = `[${e.code}] ${e.message}`
  } finally { busy.value = false }
}

async function requestResume() {
  busy.value = true
  error.value = ''
  try {
    const r = await api.post(`/api/handovers/${ho.value.id}/request-resume`)
    if (r.status === 'REJECTED') {
      error.value = '系统复核不通过，已拦截复工：' + (r.reject_reasons || []).join('；')
    }
    await load()
  } catch (e) {
    error.value = `[${e.code}] ${e.message}`
  } finally { busy.value = false }
}

async function confirmItem(itemId) {
  error.value = ''
  try {
    await api.post(`/api/checklist-items/${itemId}/confirm`)
    await load()
  } catch (e) { error.value = e.message }
}

defineExpose({ load })
load()
</script>

<style scoped>
.permit-select { min-width: 380px; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); }
.guard-cols { display: flex; align-items: stretch; gap: 12px; margin: 10px 0; }
.guard-col {
  flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px; background: #fcfdff;
}
.guard-arrow { display: flex; align-items: center; font-size: 22px; color: var(--ink-2); }
.gc-title { color: var(--ink-2); font-size: 12px; }
.gc-name { font-size: 17px; font-weight: 700; }
.sign-time { color: var(--ink-2); font-size: 12px; margin-top: auto; }
.note-list { margin: 0; padding-left: 18px; }
.note-list li { margin: 4px 0; }
</style>
