<template>
  <section class="page" data-module="patrol_workbench">
    <header class="page-head">
      <div>
        <h2>巡查工作台</h2>
        <p class="page-desc">按「核对责任组 → 确认人工桩号 → 提交回写」处理巡查待办，结论同步落到设施台账、路段清单和巡查待办。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/patrol">返回巡查列表</RouterLink>
      </div>
    </header>

    <div v-if="loading" class="panel empty-state">巡查待办加载中…</div>
    <div v-else-if="!todos.length" class="panel empty-state">暂无巡查待办</div>

    <article v-for="todo in todos" :key="todo.id" class="todo-card">
      <header class="todo-head">
        <strong>{{ todo['巡查编号'] }}</strong>
        <span>{{ todo['巡查路段'] }} · {{ todo['路段名称'] ?? '—' }}</span>
        <span>关联设施 {{ todo['关联设施'] }}（{{ todo['设施类型'] ?? '—' }}）</span>
        <span v-if="todo['设施迁移标注']" class="badge warn">{{ todo['设施迁移标注'] }}</span>
        <span class="badge">{{ todo['巡查状态'] }}</span>
      </header>

      <ol class="step-flow">
        <li v-for="step in steps" :key="step" :class="stepClass(todo, step)">{{ step }}</li>
      </ol>

      <div class="todo-body">
        <p class="todo-meta">
          路段责任组：{{ todo['路段责任组'] ?? '—' }}　设施责任组：{{ todo['设施责任组'] ?? '—' }}
          　当前桩号：{{ todo['设施桩号'] ?? '—' }}　路段范围：{{ todo['起止桩号'] ?? '—' }}
        </p>

        <form v-if="todo.step === '待核对责任组'" class="step-form" @submit.prevent="verifyGroup(todo)">
          <input
            v-model="ensureForm(todo.id).group"
            :placeholder="`核对责任组（路段责任组：${todo['路段责任组'] ?? '未知'}）`"
          />
          <button class="btn primary" type="submit">核对责任组</button>
        </form>

        <form v-else-if="todo.step === '待确认桩号'" class="step-form" @submit.prevent="confirmStake(todo)">
          <input
            v-model="ensureForm(todo.id).stake"
            :placeholder="`人工桩号（范围 ${todo['起止桩号'] ?? '未知'}，如 K1+200）`"
          />
          <button class="btn primary" type="submit">确认人工桩号</button>
        </form>

        <div v-else-if="todo.step === '待提交回写'" class="step-form">
          <span>核对责任组 {{ todo['核对责任组'] }} → 人工桩号 {{ todo['人工桩号'] }}</span>
          <button class="btn primary" type="button" @click="submitWriteback(todo)">提交回写</button>
        </div>

        <div v-else class="step-form">
          <span>已于 {{ todo['回写时间'] ?? '—' }} 回写：{{ todo['处置措施'] }}</span>
          <button class="btn" type="button" @click="submitWriteback(todo)">重试提交（验证幂等）</button>
        </div>

        <p v-if="ensureForm(todo.id).message" :class="ensureForm(todo.id).ok ? 'ok-text' : 'error-text'">
          {{ ensureForm(todo.id).message }}
        </p>
      </div>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Todo = Record<string, string | number | null> & { id: number; step: string }

interface TodoForm {
  group: string
  stake: string
  key: string
  message: string
  ok: boolean
}

const ENDPOINT = '/api/patrol'
const steps = ["待核对责任组", "待确认桩号", "待提交回写", "已回写"]

const todos = ref<Todo[]>([])
const loading = ref(false)
const forms = reactive<Record<number, TodoForm>>({})

function newIdempotencyKey() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? `WB-${crypto.randomUUID()}`
    : `WB-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function ensureForm(id: number): TodoForm {
  if (!forms[id]) {
    // 幂等键在待办维度只生成一次，重试沿用同一键，保证只落一条记录
    forms[id] = { group: '', stake: '', key: newIdempotencyKey(), message: '', ok: true }
  }
  return forms[id]
}

function stepClass(todo: Todo, step: string) {
  const current = steps.indexOf(String(todo.step))
  const mine = steps.indexOf(step)
  return { done: mine < current, active: mine === current }
}

async function post(path: string, values: Record<string, unknown>, idempotencyKey?: string) {
  const headers: Record<string, string> = {}
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey
  }
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify({ values }),
    headers,
  })
  const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string; detail?: string } | null
  if (!response.ok) {
    throw new Error(payload?.detail ?? '操作未生效，请稍后重试')
  }
  return payload
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

async function verifyGroup(todo: Todo) {
  const form = ensureForm(todo.id)
  form.message = ''
  try {
    const payload = await post(`${ENDPOINT}/${todo.id}/verify_group`, { 责任组: form.group })
    form.ok = true
    form.message = payload?.message ?? '责任组已核对'
    await reload()
  } catch (error) {
    form.ok = false
    form.message = errorText(error)
  }
}

async function confirmStake(todo: Todo) {
  const form = ensureForm(todo.id)
  form.message = ''
  try {
    const payload = await post(`${ENDPOINT}/${todo.id}/confirm_stake`, { 人工桩号: form.stake })
    form.ok = true
    form.message = payload?.message ?? '人工桩号已确认'
    await reload()
  } catch (error) {
    form.ok = false
    form.message = errorText(error)
  }
}

async function submitWriteback(todo: Todo) {
  const form = ensureForm(todo.id)
  form.message = ''
  try {
    const payload = await post(`${ENDPOINT}/${todo.id}/writeback`, {}, form.key)
    form.ok = true
    form.message = payload?.message ?? '回写完成'
    await reload()
  } catch (error) {
    form.ok = false
    form.message = errorText(error)
  }
}

async function reload() {
  loading.value = true
  try {
    const response = await request(`${ENDPOINT}/workbench/todos`)
    if (!response.ok) {
      throw new Error('巡查待办读取失败')
    }
    const payload = await response.json()
    todos.value = payload.items ?? []
  } catch {
    todos.value = []
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>
