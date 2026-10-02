<template>
  <section class="page" data-module="traffic_facility">
    <header class="page-head">
      <div>
        <h2>交安设施管理</h2>
        <p class="page-desc">维护交安设施台账，围绕设施编号、所属路段、责任组与桩号位置做登记、筛选与状态流转。</p>
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
        <input v-model="filters.section" placeholder="如 ROAD-0001" />
      </label>
      <label class="filter-item">
        <span>责任组</span>
        <input v-model="filters.group" placeholder="如 交安一组" />
      </label>
      <label class="filter-item">
        <span>设施状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
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
            <RouterLink class="link" :to="`/traffic_facility/${row.id}`">详情</RouterLink>
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
        <tr v-if="loading">
          <td :colspan="columns.length + 1" class="empty-state">交安设施列表加载中…</td>
        </tr>
        <tr v-else-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">
            {{ errorMessage ? '列表读取失败，请调整条件后重试' : '当前条件下暂无交安设施，可切换路段或先登记交安设施' }}
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条交安设施记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/traffic_facility'
const columns = ["设施编号", "设施类型", "所属路段", "桩号位置", "责任组", "迁移标注", "设施状态"]
const actions = ["登记污损", "登记缺失", "更换设施"]
const statuses = ["完好", "污损", "缺失", "已更换"]

const rows = ref<Row[]>([])
const total = ref(0)
const loading = ref(false)
const errorMessage = ref('')
const filters = reactive({ keyword: '', section: '', group: '', status: '' })

const stats = computed(() => [
  { label: '完好设施', value: rows.value.filter((row) => row['设施状态'] === '完好').length },
  { label: '污损设施', value: rows.value.filter((row) => row['设施状态'] === '污损').length },
  { label: '缺失设施', value: rows.value.filter((row) => row['设施状态'] === '缺失').length },
  { label: '待迁移标注', value: rows.value.filter((row) => row['责任组'] === '未分配').length },
])

function resetFilters() {
  filters.keyword = ''
  filters.section = ''
  filters.group = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '交安设施登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
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
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.section) query.set('section', filters.section)
  if (filters.group) query.set('group', filters.group)
  if (filters.status) query.set('status', filters.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('交安设施列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    // 无数据或指向失败时清空上一路段内容，避免列表残留与状态错位
    rows.value = []
    total.value = 0
    errorMessage.value = error instanceof Error ? error.message : '交安设施列表读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>
