<template>
  <div v-if="facilityId !== null" class="modal-mask" @click.self="emit('close')">
    <div class="modal-card">
      <header class="modal-head">
        <div>
          <h3>交安设施处置 · {{ detail?.['设施编号'] ?? `#${facilityId}` }}</h3>
          <p class="modal-sub">固定流程：核对责任组 → 确认人工桩号 → 提交回写</p>
        </div>
        <button class="btn ghost" type="button" @click="emit('close')">关闭</button>
      </header>

      <div v-if="loading" class="modal-body muted">设施明细加载中…</div>
      <div v-else-if="loadError" class="modal-body">
        <p class="error-text">{{ loadError }}</p>
        <button class="btn" type="button" @click="load">重新加载</button>
      </div>
      <template v-else-if="detail">
        <div v-if="detail['待迁移']" class="banner warn">
          存量迁移标注：该设施没有责任组（{{ detail['迁移状态'] }}），请先核对责任组完成归属迁移。
        </div>
        <div v-else-if="detail['重复标记']" class="banner info">
          该设施编号下有 {{ 1 + duplicateRows.length }} 条重复登记，已按设施编号归并显示，重复记录不重复处置。
        </div>
        <div v-if="detail['已回写']" class="banner ok">
          已完成回写（{{ detail['回写时间'] }}），结论已同步设施台账、路段清单与巡查待办。
        </div>

        <div class="modal-body">
          <dl class="detail-grid">
            <div><dt>设施类型</dt><dd>{{ detail['设施类型'] }}</dd></div>
            <div><dt>所属路段</dt><dd>{{ detail['所属路段'] }}</dd></div>
            <div><dt>路段桩号范围</dt><dd>{{ detail['路段桩号范围'] ?? '—' }}</dd></div>
            <div><dt>当前设施状态</dt><dd>{{ detail['status'] ?? detail['设施状态'] }}</dd></div>
            <div><dt>当前桩号</dt><dd>{{ detail['桩号位置'] || '—' }}</dd></div>
            <div><dt>历史桩号（原施工记录）</dt><dd>{{ detail['历史桩号'] || '—' }}</dd></div>
            <div><dt>责任组</dt><dd>{{ detail['责任组'] || '待核对' }}</dd></div>
            <div><dt>迁移状态</dt><dd>{{ detail['迁移状态'] ?? '已归属' }}</dd></div>
            <div><dt>设置日期</dt><dd>{{ detail['设置日期'] ?? '—' }}</dd></div>
            <div><dt>反光等级</dt><dd>{{ detail['反光等级'] ?? '—' }}</dd></div>
            <div><dt>完好程度</dt><dd>{{ detail['完好程度'] ?? '—' }}</dd></div>
          </dl>

          <ol class="step-track">
            <li v-for="(step, index) in steps" :key="step" :class="stepClass(index)">
              <span class="step-no">{{ index + 1 }}</span>{{ step }}
            </li>
          </ol>

          <!-- 第一步：核对责任组 -->
          <section v-if="activeStep === 0" class="step-panel">
            <h4>第一步 · 核对责任组</h4>
            <p class="muted">
              路段「{{ detail['所属路段'] }}」可选责任组：
              {{ (detail['可选责任组'] ?? []).join('、') || '未配置' }}
            </p>
            <div class="choice-row">
              <button
                v-for="team in (detail['可选责任组'] ?? [])"
                :key="team"
                class="btn"
                :class="{ primary: pickedTeam === team }"
                type="button"
                :disabled="busy"
                @click="pickedTeam = team"
              >
                {{ team }}
              </button>
            </div>
            <div class="panel-actions">
              <button class="btn primary" type="button" :disabled="busy || !pickedTeam" @click="verifyTeam">
                {{ busy ? '提交中…' : '确认责任组' }}
              </button>
            </div>
          </section>

          <!-- 第二步：确认人工桩号 -->
          <section v-else-if="activeStep === 1" class="step-panel">
            <h4>第二步 · 确认人工桩号</h4>
            <p class="muted">
              桩号必须落在 {{ detail['路段桩号范围'] }} 之内，越界将被拒绝；
              历史桩号按原施工记录保留。
            </p>
            <label class="form-line">
              <span>人工桩号</span>
              <input v-model="pickedStake" placeholder="例如 K6+800" :disabled="busy" />
            </label>
            <div class="panel-actions">
              <button class="btn primary" type="button" :disabled="busy || !pickedStake" @click="confirmStake">
                {{ busy ? '提交中…' : '确认人工桩号' }}
              </button>
            </div>
          </section>

          <!-- 第三步：提交回写 -->
          <section v-else-if="activeStep === 2" class="step-panel">
            <h4>第三步 · 提交回写</h4>
            <label class="form-line">
              <span>处置措施</span>
              <select v-model="resolveAction" :disabled="busy">
                <option value="更换设施">更换设施（状态置为已更换）</option>
                <option value="现场修复">现场修复（状态置为完好）</option>
              </select>
            </label>
            <p class="muted">回写后结论同步：设施台账状态、路段清单设施数、巡查待办关闭。</p>
            <div class="panel-actions">
              <button class="btn primary" type="button" :disabled="busy" @click="submitWriteback">
                {{ busy ? '回写中…' : '提交回写' }}
              </button>
              <span v-if="retryHint" class="muted">请求失败可重试，幂等键保证只落一条记录。</span>
            </div>
          </section>

          <section v-if="stakeChanges.length" class="history-block">
            <h4>人工桩号变更记录</h4>
            <ul>
              <li v-for="(item, index) in stakeChanges" :key="index">
                {{ item['原桩号'] }} → {{ item['人工桩号'] }}（{{ item['操作人'] }} · {{ item['时间'] }}）
              </li>
            </ul>
          </section>

          <section v-if="duplicateRows.length" class="history-block">
            <h4>归并的重复登记（{{ duplicateRows.length }} 条）</h4>
            <ul>
              <li v-for="(item, index) in duplicateRows" :key="index">
                重复ID {{ item['重复ID'] }} · 桩号 {{ item['桩号位置'] }} · {{ item['完好程度'] }}
              </li>
            </ul>
          </section>
        </div>

        <footer v-if="actionError" class="modal-foot">
          <span class="error-text">{{ actionError }}</span>
        </footer>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { request } from '@/api/client'

type Detail = Record<string, any>

const props = defineProps<{ facilityId: number | null }>()
const emit = defineEmits<{ close: []; done: [] }>()

const ENDPOINT = '/api/traffic_facility'
const steps = ['核对责任组', '确认人工桩号', '提交回写']

const detail = ref<Detail | null>(null)
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const busy = ref(false)
const pickedTeam = ref('')
const pickedStake = ref('')
const resolveAction = ref('更换设施')
const idempotencyKey = ref('')
const retryHint = ref(false)

const duplicateRows = computed<Detail[]>(() => detail.value?.['重复记录'] ?? [])
const stakeChanges = computed<Detail[]>(() => detail.value?.['桩号变更记录'] ?? [])

const activeStep = computed(() => {
  const item = detail.value
  if (!item || item['已回写']) return 3
  if (!item['责任组已核对']) return 0
  if (!item['桩号已确认']) return 1
  return 2
})

function stepClass(index: number) {
  if (activeStep.value === 3 || index < activeStep.value) return 'done'
  if (index === activeStep.value) return 'current'
  return 'todo'
}

function buildIdempotencyKey(id: number) {
  const random = Math.random().toString(36).slice(2, 10)
  return `facility-${id}-${Date.now()}-${random}`
}

watch(
  () => props.facilityId,
  (id) => {
    actionError.value = ''
    loadError.value = ''
    detail.value = null
    pickedTeam.value = ''
    pickedStake.value = ''
    resolveAction.value = '更换设施'
    retryHint.value = false
    if (id !== null) {
      idempotencyKey.value = buildIdempotencyKey(id)
      void load()
    }
  },
  { immediate: true },
)

async function load() {
  if (props.facilityId === null) return
  loading.value = true
  loadError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${props.facilityId}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail ?? '设施详情读取失败')
    }
    detail.value = (await response.json()) as Detail
    pickedTeam.value = String(detail.value['责任组'] ?? '')
    pickedStake.value = String(detail.value['桩号位置'] ?? '')
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '设施详情读取失败'
  } finally {
    loading.value = false
  }
}

async function postStep(path: string, body: Record<string, unknown>) {
  busy.value = true
  actionError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${props.facilityId}/${path}`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail ?? '操作未生效，请稍后重试')
    }
    detail.value = (payload?.entry ?? detail.value) as Detail
    if (path === 'writeback') {
      emit('done')
    }
  } catch (error) {
    retryHint.value = path === 'writeback'
    actionError.value = error instanceof Error ? error.message : '操作未生效，请稍后重试'
  } finally {
    busy.value = false
  }
}

function verifyTeam() {
  void postStep('verify-team', { values: { 责任组: pickedTeam.value, 操作人: '值班管理员' } })
}

function confirmStake() {
  void postStep('confirm-stake', { values: { 桩号位置: pickedStake.value, 操作人: '值班管理员' } })
}

function submitWriteback() {
  // 幂等键在弹窗打开时生成一次：网络重试沿用同一个键，后端只落一条记录
  void postStep('writeback', {
    values: {
      处置措施: resolveAction.value,
      操作人: '值班管理员',
      idempotency_key: idempotencyKey.value,
    },
  })
}
</script>
