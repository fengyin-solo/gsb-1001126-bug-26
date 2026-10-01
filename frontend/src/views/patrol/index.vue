<template>
  <section class="page" data-module="patrol">
    <header class="page-head">
      <div>
        <h2>日常巡查管理</h2>
        <p class="page-desc">维护巡查记录，围绕巡查编号、巡查路段、巡查日期、巡查人员做登记、筛选与状态流转；工作台按「核对责任组 → 确认人工桩号 → 提交回写」处置交安设施。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记巡查记录</button>
        <button class="btn" type="button" @click="exportRows">导出日常巡查清单</button>
      </div>
    </header>

    <!-- 巡查工作台：设施待办与巡查待办分开列出，处置走固定三步 -->
    <div class="workbench">
      <div class="workbench-head">
        <h3>巡查工作台</h3>
        <label class="filter-item">
          <span>按路段过滤</span>
          <input v-model="workbenchRoad" placeholder="输入路段名称" @change="loadWorkbench" />
        </label>
        <button class="btn" type="button" @click="loadWorkbench">刷新待办</button>
        <div class="workbench-stats">
          <span>设施待办 <strong>{{ workbench.facility_todos?.length ?? 0 }}</strong></span>
          <span>待迁移 <strong>{{ workbench['待迁移'] ?? 0 }}</strong></span>
          <span>待核对责任组 <strong>{{ workbench['待核对责任组'] ?? 0 }}</strong></span>
          <span>待确认桩号 <strong>{{ workbench['待确认桩号'] ?? 0 }}</strong></span>
          <span>待回写 <strong>{{ workbench['待回写'] ?? 0 }}</strong></span>
          <span>已处置 <strong>{{ workbench['已处置'] ?? 0 }}</strong></span>
        </div>
      </div>
      <div class="workbench-columns">
        <div class="workbench-column">
          <h4>交安设施处置待办</h4>
          <p v-if="workbenchError" class="error-text">{{ workbenchError }}</p>
          <div
            v-for="todo in workbench.facility_todos ?? []"
            :key="`f-${todo.id}`"
            class="todo-item"
          >
            <div>
              <strong>{{ todo['设施编号'] }}</strong> · {{ todo['设施类型'] }} · {{ todo['所属路段'] }}
              <div class="todo-meta">
                桩号 {{ todo['桩号位置'] }} · 责任组 {{ todo['责任组'] }} · 状态 {{ todo['状态'] }}
                <span v-if="todo['待迁移']" class="tag tag-warn">无责任组待迁移</span>
                <span v-else-if="!todo['责任组已核对']" class="tag tag-warn">待核对责任组</span>
                <span v-else-if="!todo['桩号已确认']" class="tag tag-info">待确认人工桩号</span>
                <span v-else-if="!todo['已回写']" class="tag tag-info">待提交回写</span>
              </div>
            </div>
            <button class="btn primary" type="button" @click="activeId = Number(todo.id)">去处置</button>
          </div>
          <div v-if="!workbenchError && !(workbench.facility_todos ?? []).length" class="empty-block">
            暂无交安设施处置待办
          </div>
        </div>
        <div class="workbench-column">
          <h4>巡查记录待办</h4>
          <div v-for="todo in workbench.patrol_todos ?? []" :key="`p-${todo.id}`" class="todo-item">
            <div>
              <strong>{{ todo['巡查编号'] }}</strong> · {{ todo['所属路段'] }}
              <div class="todo-meta">{{ todo['发现问题'] }} · 状态 {{ todo['状态'] }}</div>
            </div>
            <span class="tag tag-info">巡查 {{ todo['状态'] }}</span>
          </div>
          <div v-if="!(workbench.patrol_todos ?? []).length" class="empty-block">暂无巡查记录待办</div>
        </div>
      </div>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!loading && !rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无日常巡查数据，可先登记巡查记录</td>
        </tr>
        <tr v-if="loading && !rows.length">
          <td :colspan="columns.length + 1" class="empty-state">巡查记录加载中…</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条日常巡查记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <FacilityWorkflow :facility-id="activeId" @close="activeId = null" @done="onWorkflowDone" />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import FacilityWorkflow from '@/components/FacilityWorkflow.vue'

type Row = Record<string, any>

const ENDPOINT = '/api/patrol'
const FACILITY_ENDPOINT = '/api/traffic_facility'
const columns = ['巡查编号', '巡查路段', '巡查日期', '巡查人员', '巡查车辆', '发现问题', '处置措施', '巡查状态']
const actions = ['开始巡查', '完成巡查', '复核确认']
const stats = [
  { label: '今日巡查', value: 0 },
  { label: '待巡查路段', value: 0 },
  { label: '发现问题', value: 0 },
]

const rows = ref<Row[]>([])
const total = ref(0)
const loading = ref(false)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const workbench = ref<Record<string, any>>({})
const workbenchRoad = ref('')
const workbenchError = ref('')
const activeId = ref<number | null>(null)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '巡查记录登记入口尚未接入审批流'
}

async function loadWorkbench() {
  workbenchError.value = ''
  const params = new URLSearchParams()
  if (workbenchRoad.value.trim()) params.set('road', workbenchRoad.value.trim())
  const query = params.toString()
  try {
    const response = await request(`${FACILITY_ENDPOINT}/workbench/summary${query ? `?${query}` : ''}`)
    if (!response.ok) {
      throw new Error('巡查工作台读取失败')
    }
    workbench.value = await response.json()
  } catch (error) {
    // 指向失败时工作台也不保留旧待办，避免处置过期任务
    workbench.value = {}
    workbenchError.value = error instanceof Error ? error.message : '巡查工作台读取失败'
  }
}

function onWorkflowDone() {
  activeId.value = null
  void loadWorkbench()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('日常巡查动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日常巡查操作失败'
  }
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  rows.value = []
  total.value = 0
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('巡查记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = Array.isArray(payload.items) ? payload.items : []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    rows.value = []
    total.value = 0
    errorMessage.value = error instanceof Error ? error.message : '日常巡查列表读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void reload()
  void loadWorkbench()
})
</script>
