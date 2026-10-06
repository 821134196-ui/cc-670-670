<template>
  <div class="card">
    <h3>门禁 / 时钟模拟（本地）</h3>
    <div class="sim-grid">
      <label>人员
        <select v-model="personId">
          <option v-for="g in guardians" :key="g.id" :value="g.id">{{ g.name }}</option>
        </select>
      </label>
      <label>方向
        <select v-model="direction">
          <option value="IN">入场 IN</option>
          <option value="OUT">离场 OUT</option>
        </select>
      </label>
      <label>过闸时间
        <input type="datetime-local" step="1" v-model="eventTime" />
      </label>
      <label>回执延迟（秒）
        <input type="number" min="0" v-model.number="delay" />
      </label>
      <div class="row" style="align-items:flex-end">
        <button class="btn ghost sm" @click="setEventOffset(0)">过闸=现在</button>
        <button class="btn ghost sm" @click="setEventOffset(-300)">5 分钟前过闸</button>
        <button class="btn" @click="submit">发送回执</button>
      </div>
    </div>
    <div class="muted" style="margin-top:8px">
      模拟网络延迟：把“过闸时间”设为过去、“回执延迟”填正数，即可复现回执晚到——
      系统按回执到达时刻才知悉过闸，不会回填此前的在场状态。
    </div>

    <div class="row" style="margin-top:10px">
      <label class="muted">演示时钟快进：
        <input type="number" v-model.number="advanceMin" style="width:90px" /> 分钟
      </label>
      <button class="btn ghost sm" @click="advanceClock">推进系统时间</button>
      <span class="muted">（用于演示许可/资质到期，实际系统使用服务器真实时间）</span>
    </div>
    <div v-if="msg" class="alert ok" style="margin-bottom:0">{{ msg }}</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'

const props = defineProps({ guardians: { type: Array, default: () => [] } })
const emit = defineEmits(['changed'])

const personId = ref(props.guardians[0]?.id ?? null)
const direction = ref('IN')
const delay = ref(0)
const advanceMin = ref(35)
const msg = ref('')

function localInput(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
const eventTime = ref(localInput(new Date()))

function setEventOffset(secondsFromNow) {
  eventTime.value = localInput(new Date(Date.now() + secondsFromNow * 1000))
}

async function submit() {
  msg.value = ''
  try {
    const eventIso = new Date(eventTime.value).toISOString()
    await api.post('/api/access-events', {
      person_id: personId.value,
      direction: direction.value,
      event_time: eventIso,
      delay_seconds: delay.value || 0,
    })
    msg.value = '回执已发送'
    emit('changed')
  } catch (e) {
    msg.value = `[${e.code}] ${e.message}`
  }
}

async function advanceClock() {
  msg.value = ''
  try {
    const r = await api.post('/api/dev/clock', { advance_seconds: advanceMin * 60 })
    msg.value = '系统时间已推进到 ' + r.now.replace('T', ' ').slice(0, 19)
    emit('changed')
  } catch (e) {
    msg.value = e.message
  }
}
</script>

<style scoped>
.sim-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr)) auto;
  gap: 10px;
  align-items: end;
}
label { font-size: 12px; color: var(--ink-2); display: flex; flex-direction: column; gap: 4px; }
input, select { padding: 7px 9px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; }
</style>
