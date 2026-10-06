<template>
  <div>
    <div class="row" style="margin-bottom:12px">
      <h2 style="margin:0;font-size:17px">班组长复核台</h2>
      <button class="btn ghost sm" @click="load">刷新</button>
      <span v-if="auto" class="muted">每 10 秒自动刷新</span>
    </div>

    <!-- 实时无人监护空档 -->
    <div class="card">
      <h3>无人监护时段（停工中的作业）</h3>
      <div v-if="!data.active_gaps.length" class="muted">当前没有停工交接中的作业。</div>
      <table v-else>
        <thead>
          <tr><th>许可</th><th>交接单</th><th>原监护人</th><th>接班人</th><th>空档</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="g in data.active_gaps" :key="g.handover_id">
            <td>#{{ g.permit_id }}</td>
            <td>#{{ g.handover_id }}</td>
            <td>{{ g.outgoing_name }}</td>
            <td>{{ g.incoming_name }}</td>
            <td>
              <span :class="g.ongoing ? 'gap-danger' : 'gap-warn'">
                {{ fmtDuration(g.gap_seconds) }}
              </span>
              <span v-if="g.ongoing" class="badge danger" style="margin-left:6px">持续中</span>
              <span v-else class="badge warn" style="margin-left:6px">已闭合</span>
            </td>
            <td>{{ HANDOVER_STATUS_LABELS[g.status] || g.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 复工审批 -->
    <div class="card">
      <h3>复工申请</h3>
      <div v-if="!data.pending_requests.length" class="muted">暂无待审批的复工申请。</div>
      <div v-for="q in data.pending_requests" :key="q.id" class="req-card">
        <div class="row">
          <b>申请 #{{ q.id }}</b>
          <span class="badge info">许可 #{{ q.permit_id }} / 交接单 #{{ q.handover_id }}</span>
          <span class="muted">申请人：{{ q.requested_by_name }} · {{ fmtDt(q.requested_at) }}</span>
          <span v-if="q.gap_seconds != null" class="badge warn">
            无人监护 {{ fmtDuration(q.gap_seconds) }}
          </span>
        </div>
        <div class="alert info" style="margin:8px 0">
          批准瞬间系统会再次硬校验：接班人资质有效、许可未过期、接班人现场在场、无未完成事项。
        </div>
        <div class="row">
          <input v-model="comments[q.id]" placeholder="审批意见（可选）" class="comment-input" />
          <button class="btn" @click="decide(q, true)">批准复工</button>
          <button class="btn ghost-danger" @click="decide(q, false)">驳回</button>
        </div>
      </div>
    </div>

    <!-- 作业一览 -->
    <div class="card">
      <h3>全部作业</h3>
      <table>
        <thead>
          <tr><th>许可</th><th>内容</th><th>地点</th><th>许可状态</th><th>作业状态</th><th>监护人</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in data.permits" :key="p.id">
            <td class="mono">{{ p.permit_no }}</td>
            <td>{{ p.title }}</td>
            <td>{{ p.location }}</td>
            <td><span class="badge" :class="p.valid ? 'ok' : 'danger'">{{ p.valid ? '有效' : '已过期' }}</span></td>
            <td>{{ WORK_STATUS_LABELS[p.work_status] || p.work_status }}</td>
            <td>{{ p.current_guardian ? p.current_guardian.name : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="error" class="alert danger">{{ error }}</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { fmtDt, fmtDuration, HANDOVER_STATUS_LABELS, WORK_STATUS_LABELS } from '../util'

const props = defineProps({ auto: { type: Boolean, default: true } })
const data = reactive({ pending_requests: [], active_gaps: [], permits: [] })
const comments = reactive({})
const error = ref('')
let timer = null

async function load() {
  error.value = ''
  try {
    const d = await api.get('/api/leader/overview')
    data.pending_requests = d.pending_requests
    data.active_gaps = d.active_gaps
    data.permits = d.permits
  } catch (e) { error.value = e.message }
}

async function decide(q, approve) {
  error.value = ''
  try {
    const r = await api.post(`/api/resume-requests/${q.id}/decision`, {
      approve, comment: comments[q.id] || null,
    })
    if (!approve || r.status === 'REJECTED') {
      error.value = r.status === 'REJECTED'
        ? '系统复核仍不通过，已自动驳回：' + (r.reject_reasons || []).join('；')
        : '已驳回'
    }
    await load()
  } catch (e) { error.value = `[${e.code}] ${e.message}` }
}

onMounted(() => {
  load()
  if (props.auto) timer = setInterval(load, 10000)
})
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<style scoped>
.req-card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; background: #fcfdff; }
.comment-input { flex: 1; min-width: 220px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; }
.gap-danger { color: var(--danger); font-weight: 700; }
.gap-warn { color: var(--warn); font-weight: 700; }
</style>
