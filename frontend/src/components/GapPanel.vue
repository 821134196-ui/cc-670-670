<template>
  <div v-if="gap || locked != null" class="gap-box" :class="tone">
    <template v-if="locked != null && status === 'RESUMED'">
      <div class="gap-title">本次无人监护时段（复工时已锁定）</div>
      <div class="gap-num">{{ fmtDuration(locked) }}</div>
      <div class="muted">锁定后到达的任何门禁回执都不会改写该记录。</div>
    </template>
    <template v-else-if="gap">
      <div class="gap-title">
        无人监护时段
        <span v-if="gap.ongoing" class="badge danger">空档持续中</span>
        <span v-else class="badge warn">空档已闭合</span>
      </div>
      <div class="gap-num">{{ fmtDuration(gap.gap_seconds) }}</div>
      <div class="muted">
        起：{{ gap.gap_start ? fmtDt(gap.gap_start) : '原监护人仍在场，空档尚未开始' }}
        ；止：{{ gap.gap_end ? fmtDt(gap.gap_end) : '接班人尚未入场' }}
      </div>
      <div v-if="gap.start_basis === 'PAUSED_TIME_NO_RECEIPT'" class="alert warn" style="margin:8px 0 0">
        尚无原监护人离场回执到达，按停工时刻保守计空档；回执到达后以物理过闸时间修正。
      </div>
      <div v-if="gap.start_basis === 'OUTGOING_STILL_ON_SITE'" class="alert info" style="margin:8px 0 0">
        门禁显示原监护人仍在现场，暂不产生无人监护空档。
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtDt, fmtDuration } from '../util'

const props = defineProps({
  gap: { type: Object, default: null },
  locked: { type: Number, default: null },
  status: { type: String, default: '' },
})
const tone = computed(() => (props.gap?.ongoing ? 'danger' : 'ok'))
</script>

<style scoped>
.gap-box { border-radius: 8px; padding: 12px 14px; margin: 10px 0; border: 1px solid; }
.gap-box.danger { background: var(--danger-bg); border-color: #f3c2c2; }
.gap-box.ok { background: var(--ok-bg); border-color: #bfe3cd; }
.gap-title { font-weight: 600; margin-bottom: 4px; }
.gap-num { font-size: 22px; font-weight: 700; margin: 4px 0; }
</style>
