<script setup>
import { ref, onMounted, computed } from 'vue'
import { api, getLogin, setLogin } from './api.js'
import JobBoard from './components/JobBoard.vue'
import HandoverView from './components/HandoverView.vue'
import LeaderView from './components/LeaderView.vue'
import HistoryView from './components/HistoryView.vue'

const ROLE_LABELS = { guardian: '监护人', leader: '班组长', worker: '作业人员' }
const roleLabel = (r) => ROLE_LABELS[r] || r

const persons = ref([])
const login = ref(getLogin())
const tab = ref('board')
const selectedJobId = ref(null)
const handoverRefreshKey = ref(0)

async function loadPersons() {
  persons.value = await api.get('/api/persons')
  if (!login.value && persons.value.length) {
    const g = persons.value.find((p) => p.role === 'guardian')
    login.value = g ? g.login : persons.value[0].login
    setLogin(login.value)
  }
}

function switchLogin(l) {
  login.value = l
  setLogin(l)
  handoverRefreshKey.value++
}
function openHandover(jobId) {
  selectedJobId.value = jobId
  tab.value = 'handover'
  handoverRefreshKey.value++
}
function bump() {
  handoverRefreshKey.value++
}

onMounted(loadPersons)
</script>

<template>
  <div>
    <div class="topbar">
      <h1>🏭 危险作业监护人替换与现场复核</h1>
      <span class="badge">停工交接 · 双方签认 · 复工复核</span>
      <div class="spacer"></div>

      <div class="login-pill">
        <span class="muted small">当前登录（本人账号）</span>
        <select :value="login" @change="switchLogin($event.target.value)">
          <option v-for="p in persons" :key="p.id" :value="p.login">
            {{ p.name }} · {{ p.login }}（{{ roleLabel(p.role) }}）
          </option>
        </select>
      </div>
    </div>

    <div class="container">
      <div class="tabs" style="margin-bottom: 20px">
        <button :class="{ active: tab === 'board' }" @click="tab = 'board'">作业与交接</button>
        <button :class="{ active: tab === 'handover' }" @click="tab = 'handover'">
          交接工作台
        </button>
        <button :class="{ active: tab === 'leader' }" @click="tab = 'leader'; bump()">
          班组长复核
        </button>
        <button :class="{ active: tab === 'history' }" @click="tab = 'history'">交接历史</button>
      </div>

      <JobBoard v-if="tab === 'board'" :persons="persons" @open="openHandover" />

      <HandoverView
        v-else-if="tab === 'handover'"
        :key="handoverRefreshKey"
        :job-id="selectedJobId"
        :persons="persons"
        :current-login="login"
        @pick="openHandover"
        @changed="bump"
      />

      <LeaderView v-else-if="tab === 'leader'" :key="'l' + handoverRefreshKey" />
      <HistoryView v-else />
    </div>
  </div>
</template>
