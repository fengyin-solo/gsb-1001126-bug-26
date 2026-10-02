<template>
  <section class="page" data-module="traffic_facility_detail">
    <header class="page-head">
      <div>
        <h2>交安设施详情</h2>
        <p class="page-desc">设施台账明细、历史位置（按原施工记录保留）与按设施编号去重后的同路段相关设施。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/traffic_facility">返回列表</RouterLink>
      </div>
    </header>

    <div v-if="loading" class="panel empty-state">设施详情加载中…</div>
    <div v-else-if="!detail" class="panel empty-state">
      {{ errorMessage || '设施不存在或已归档' }}
    </div>
    <template v-else>
      <dl class="detail-grid">
        <div v-for="field in fields" :key="field" class="detail-item">
          <dt>{{ field }}</dt>
          <dd>{{ detail[field] ?? '—' }}</dd>
        </div>
      </dl>

      <h3 class="section-title">历史位置（原施工记录）</h3>
      <table class="data-table">
        <thead>
          <tr><th>桩号位置</th><th>来源</th><th>日期</th></tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in history" :key="index">
            <td>{{ item['桩号位置'] ?? '—' }}</td>
            <td>{{ item['来源'] ?? '—' }}</td>
            <td>{{ item['日期'] ?? '—' }}</td>
          </tr>
          <tr v-if="!history.length">
            <td colspan="3" class="empty-state">暂无历史位置记录</td>
          </tr>
        </tbody>
      </table>

      <h3 class="section-title">同路段相关设施（按设施编号去重）</h3>
      <table class="data-table">
        <thead>
          <tr><th>设施编号</th><th>设施类型</th><th>桩号位置</th><th>责任组</th><th>设施状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in related" :key="String(item['设施编号'])">
            <td>{{ item['设施编号'] ?? '—' }}</td>
            <td>{{ item['设施类型'] ?? '—' }}</td>
            <td>{{ item['桩号位置'] ?? '—' }}</td>
            <td>{{ item['责任组'] ?? '—' }}</td>
            <td>{{ item['设施状态'] ?? '—' }}</td>
            <td><RouterLink class="link" :to="`/traffic_facility/${item.id}`">查看</RouterLink></td>
          </tr>
          <tr v-if="!related.length">
            <td colspan="6" class="empty-state">同路段暂无其他设施</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Detail = Record<string, string | number | null> & {
  历史位置?: Array<Record<string, string | number | null>>
  related?: Array<Record<string, string | number | null>>
}

const fields = ["设施编号", "设施类型", "所属路段", "桩号位置", "责任组", "迁移标注", "设施状态", "设置日期", "反光等级", "完好程度", "最近回写"]

const route = useRoute()
const detail = ref<Detail | null>(null)
const loading = ref(false)
const errorMessage = ref('')

const history = computed(() => detail.value?.历史位置 ?? [])
const related = computed(() => detail.value?.related ?? [])

async function load(entryId: string) {
  loading.value = true
  errorMessage.value = ''
  // 切换设施时先清空上一设施内容，避免重复设施残留显示
  detail.value = null
  try {
    const response = await request(`/api/traffic_facility/${entryId}`)
    if (response.status === 404) {
      throw new Error('设施不存在或已归档')
    }
    if (!response.ok) {
      throw new Error('设施详情读取失败')
    }
    detail.value = (await response.json()) as Detail
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '设施详情读取失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => String(route.params.id ?? ''),
  (entryId) => {
    if (entryId) {
      void load(entryId)
    }
  },
  { immediate: true },
)
</script>
