export function fmtDt(s) {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function fmtDuration(sec) {
  if (sec == null) return '—'
  if (sec < 60) return `${sec} 秒`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m < 60) return s ? `${m} 分 ${s} 秒` : `${m} 分钟`
  const h = Math.floor(m / 60)
  return `${h} 小时 ${m % 60} 分`
}

export const EVENT_LABELS = {
  WORK_PAUSED: '作业停工',
  GUARDIAN_DEPARTED: '原监护人离场',
  GUARDIAN_ARRIVED: '接班人到场',
  OUTGOING_SIGNED: '原监护人签认',
  INCOMING_SIGNED: '接班人签认',
  RESUME_REQUESTED: '申请复工',
  RESUME_REJECTED: '复工被驳回',
  WORK_RESUMED: '批准复工',
}

export const EVENT_TONE = {
  WORK_PAUSED: 'warn',
  GUARDIAN_DEPARTED: 'danger',
  GUARDIAN_ARRIVED: 'info',
  OUTGOING_SIGNED: '',
  INCOMING_SIGNED: '',
  RESUME_REQUESTED: 'warn',
  RESUME_REJECTED: 'danger',
  WORK_RESUMED: 'ok',
}

export const HANDOVER_STATUS_LABELS = {
  PENDING: '待原监护人签认',
  OUTGOING_SIGNED: '待接班人签认',
  FULLY_SIGNED: '双方已签认',
  RESUMED: '已复工',
  CANCELLED: '已作废',
}

export const WORK_STATUS_LABELS = {
  IN_PROGRESS: '作业中',
  PAUSED: '停工交接中',
  COMPLETED: '已完成',
}
