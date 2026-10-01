<template>
  <section class="page" data-module="traffic_facility">
    <header class="page-head">
      <div>
        <h2>交安设施管理</h2>
        <p class="page-desc">维护交安设施，围绕设施编号、设施类型、所属路段、桩号位置做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记交安设施</button>
        <button class="btn" type="button" @click="exportRows">导出交安设施清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>设施编号</span>
        <input v-model="filters.keyword" placeholder="按设施编号检索" />
      </label>
      <label class="filter-item">
        <span>所属路段</span>
        <input v-model="filters.road" placeholder="按所属路段检索" />
      </label>
      <label class="filter-item">
        <span>设施状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>迁移标注</span>
        <select v-model="filters.pending_migration">
          <option value="">全部</option>
          <option value="true">仅无责任组待迁移</option>
          <option value="false">仅已归属</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>标注</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row['待迁移']" class="tag tag-warn">待迁移</span>
            <span v-if="row['重复标记']" class="tag tag-info">重复已归并</span>
            <span v-if="row['已回写']" class="tag tag-ok">已回写</span>
            <span v-if="!row['待迁移'] && !row['重复标记'] && !row['已回写']">—</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openWorkflow(row)">查看详情/处置</button>
            <button class="link" type="button" @click="runAction('登记污损', row)">登记污损</button>
            <button class="link" type="button" @click="runAction('登记缺失', row)">登记缺失</button>
          </td>
        </tr>
        <tr v-if="!loading && !rows.length">
          <td :colspan="columns.length + 2" class="empty-state">
            {{ errorMessage ? '列表读取失败，已清空旧路段内容' : '当前筛选条件下暂无交安设施数据' }}
          </td>
        </tr>
        <tr v-if="loading && !rows.length">
          <td :colspan="columns.length + 2" class="empty-state">交安设施加载中…</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条交安设施记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <FacilityWorkflow :facility-id="activeId" @close="activeId = null" @done="onWorkflowDone" />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import FacilityWorkflow from '@/components/FacilityWorkflow.vue'

type Row = Record<string, any>

const ENDPOINT = '/api/traffic_facility'
const columns = ['设施编号', '设施类型', '所属路段', '桩号位置', '设置日期', '反光等级', '完好程度', '设施状态']
const statuses = ['完好', '污损', '缺失', '已更换']

const rows = ref<Row[]>([])
const total = ref(0)
const loading = ref(false)
const errorMessage = ref('')
const filters = reactive({ keyword: '', road: '', status: '', pending_migration: '' })
const activeId = ref<number | null>(null)

const stats = computed(() => [
  { label: '完好设施', value: rows.value.filter((row) => row.status === '完好').length },
  { label: '污损设施', value: rows.value.filter((row) => row.status === '污损').length },
  { label: '缺失设施', value: rows.value.filter((row) => row.status === '缺失').length },
])

function buildQuery() {
  const params = new URLSearchParams()
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  if (filters.road.trim()) params.set('road', filters.road.trim())
  if (filters.status) params.set('status', filters.status)
  if (filters.pending_migration) params.set('pending_migration', filters.pending_migration)
  const query = params.toString()
  return query ? `?${query}` : ''
}

function resetFilters() {
  filters.keyword = ''
  filters.road = ''
  filters.status = ''
  filters.pending_migration = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '交安设施登记入口尚未接入审批流'
}

function openWorkflow(row: Row) {
  errorMessage.value = ''
  activeId.value = Number(row.id)
}

function onWorkflowDone() {
  activeId.value = null
  void reload()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('交安设施动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交安设施操作失败'
  }
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  // 空态转换：发起请求前先清空上一路段内容，避免无数据或指向失败时残留与状态错位
  rows.value = []
  total.value = 0
  try {
    const response = await request(`${ENDPOINT}${buildQuery()}`)
    if (!response.ok) {
      throw new Error('交安设施列表读取失败')
    }
    const payload = await response.json()
    // 以本次响应为准：无数据就是空表，绝不保留上一路段内容
    rows.value = Array.isArray(payload.items) ? payload.items : []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    rows.value = []
    total.value = 0
    errorMessage.value = error instanceof Error ? error.message : '交安设施列表读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>
