<template>
  <div v-if="!me">
    <Login @logged-in="onLogin" />
  </div>
  <div v-else class="shell">
    <header class="topbar">
      <div class="brand">🛡️ 危险作业监护人替换与现场复核系统</div>
      <nav class="tabs">
        <button :class="{active: tab==='work'}" @click="tab='work'">作业与交接</button>
        <button v-if="me.role==='LEADER'" :class="{active: tab==='leader'}" @click="tab='leader'">
          班组长复核台
        </button>
      </nav>
      <div class="me">
        <span>{{ me.name }}（{{ roleLabel(me.role) }}）</span>
        <button class="btn ghost sm" @click="logout">退出</button>
      </div>
    </header>

    <main class="content">
      <div v-if="initError" class="alert danger">{{ initError }}</div>
      <PermitView
        v-if="tab==='work'"
        ref="permitView"
        :me="me"
        :permits="permits"
        :guardians="guardians"
      />
      <LeaderView v-else-if="tab==='leader' && me.role==='LEADER'" />
      <div v-else class="card">
        <p>当前账号为{{ roleLabel(me.role) }}，可在“作业与交接”页查看本作业交接信息。</p>
        <p class="muted">
          如需演示完整流程，请在交接单不同阶段使用当事人账号登录签认；
          系统强制本人账号签认，无法共用或代签。
        </p>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import Login from './components/Login.vue'
import PermitView from './components/PermitView.vue'
import LeaderView from './components/LeaderView.vue'
import { api, getToken, setToken } from './api'

const me = ref(null)
const tab = ref('work')
const permits = ref([])
const guardians = ref([])
const initError = ref('')

function roleLabel(r) {
  return { GUARDIAN: '监护人', LEADER: '班组长', WORKER: '作业人员' }[r] || r
}

async function onLogin(data) {
  setToken(data.token)
  me.value = {
    token: data.token, id: data.person_id, name: data.name, role: data.role,
    qualification_type: data.qualification_type,
  }
  await bootstrap()
  if (data.role === 'LEADER') tab.value = 'leader'
}

function logout() {
  setToken(null)
  me.value = null
  permits.value = []
  guardians.value = []
}

async function bootstrap() {
  initError.value = ''
  try {
    const [ps, gs] = await Promise.all([
      api.get('/api/permits'),
      api.get('/api/persons/guardians'),
    ])
    permits.value = ps
    guardians.value = gs
  } catch (e) {
    initError.value = e.message
  }
}

onMounted(async () => {
  // 刷新页面后若本地仍保留令牌，尝试用 /api/persons 校验（简单起见要求重新登录）
  if (getToken()) setToken(null)
})
</script>

<style scoped>
.shell { min-height: 100vh; }
.topbar {
  display: flex; align-items: center; gap: 18px;
  background: #122a4f; color: #fff; padding: 0 22px; height: 56px;
}
.brand { font-weight: 700; white-space: nowrap; }
.tabs { display: flex; gap: 6px; flex: 1; }
.tabs button {
  background: transparent; border: none; color: #cdd9ea; padding: 8px 14px;
  border-radius: 8px; cursor: pointer; font-size: 14px;
}
.tabs button.active { background: #1d5fd1; color: #fff; }
.me { display: flex; align-items: center; gap: 10px; white-space: nowrap; font-size: 13px; }
.content { max-width: 1080px; margin: 0 auto; padding: 18px 20px 40px; }
</style>
