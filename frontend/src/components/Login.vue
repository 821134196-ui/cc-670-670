<template>
  <div class="login-wrap">
    <div class="card login-card">
      <h2>危险作业监护人替换与现场复核系统</h2>
      <p class="muted">作业进行中更换监护人 · 停工交接 · 现场复核 · 复工审批</p>
      <div class="form-row">
        <label>账号</label>
        <input v-model="username" placeholder="如 zhang" @keyup.enter="doLogin" />
      </div>
      <div class="form-row">
        <label>密码</label>
        <input v-model="password" type="password" placeholder="演示密码 123456" @keyup.enter="doLogin" />
      </div>
      <div v-if="error" class="alert danger">{{ error }}</div>
      <button class="btn" style="width:100%" @click="doLogin">登录</button>

      <div class="demo-accounts">
        <div class="muted" style="margin:12px 0 6px">演示账号（点击填充，密码均 123456）：</div>
        <div class="acct-list">
          <button v-for="a in accounts" :key="a.u" class="acct" @click="username=a.u;password='123456'">
            <b>{{ a.name }}</b><span>{{ a.label }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['logged-in'])
const username = ref('zhang')
const password = ref('123456')
const error = ref('')

const accounts = [
  { u: 'zhang', name: '张建国', label: '现任监护人' },
  { u: 'li', name: '李文斌', label: '接班监护人' },
  { u: 'leader', name: '王海涛', label: '班组长' },
  { u: 'zhao_expired', name: '赵德柱', label: '资质已过期' },
  { u: 'sun_none', name: '孙小年', label: '无资质工人' },
]

async function doLogin() {
  error.value = ''
  try {
    const data = await api.post('/api/auth/login', {
      username: username.value.trim(),
      password: password.value,
    })
    emit('logged-in', data)
  } catch (e) {
    error.value = e.message
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card { width: 440px; max-width: 100%; }
.login-card h2 { font-size: 18px; margin: 0 0 6px; }
.form-row { margin: 12px 0; }
.form-row label { display: block; color: var(--ink-2); margin-bottom: 4px; }
.form-row input {
  width: 100%; padding: 9px 12px; border: 1px solid var(--line);
  border-radius: 8px; font-size: 14px;
}
.acct-list { display: flex; flex-wrap: wrap; gap: 8px; }
.acct {
  border: 1px solid var(--line); background: #fafbfd; border-radius: 8px;
  padding: 6px 10px; cursor: pointer; text-align: left;
}
.acct b { display: block; font-size: 13px; }
.acct span { font-size: 12px; color: var(--ink-2); }
</style>
