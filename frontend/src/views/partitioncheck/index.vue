<template>
  <section class="page" data-module="partitioncheck">
    <header class="page-head">
      <div>
        <h2>分区检测管理</h2>
        <p class="page-desc">管理数据表分区的完整性检测规则，自动发现和补齐缺失分区。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = !showCreate">
          {{ showCreate ? '收起登记表单' : '登记检测规则' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出分区检测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="showCreate" class="filter-bar create-panel" @submit.prevent="submitCreate">
      <label class="filter-item">
        <span>规则编号 *</span>
        <input v-model="createForm.规则编号" placeholder="如 PART-0010" />
      </label>
      <label class="filter-item">
        <span>数据表名 *</span>
        <input v-model="createForm.数据表名" placeholder="如 dwd_order_detail" />
      </label>
      <label class="filter-item">
        <span>期望分区数 *（正整数）</span>
        <input v-model.number="createForm.期望分区数" type="number" min="1" step="1" placeholder="如 3" />
      </label>
      <label class="filter-item">
        <span>分区延迟容忍（分钟）</span>
        <input v-model.number="createForm.分区延迟容忍" type="number" min="0" step="1" placeholder="如 60" />
      </label>
      <label class="filter-item">
        <span>检测间隔分钟</span>
        <input v-model.number="createForm.检测间隔分钟" type="number" min="0" step="1" placeholder="如 60" />
      </label>
      <label class="filter-item">
        <span>自动补齐开关</span>
        <select v-model="createForm.自动补齐开关">
          <option value="关闭">关闭</option>
          <option value="开启">开启</option>
        </select>
      </label>
      <label class="filter-item">
        <span>补齐优先级</span>
        <input v-model="createForm.补齐优先级" placeholder="高 / 中 / 低" />
      </label>
      <button class="btn primary" type="submit">提交登记</button>
    </form>

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
          <th>规则版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>v{{ row['规则版本'] ?? 1 }}</td>
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
            <button class="link" type="button" @click="runDetect(row)">执行检测</button>
            <button class="link" type="button" @click="loadRecords(row)">检测记录</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无分区检测数据，可先登记检测规则</td>
        </tr>
      </tbody>
    </table>

    <section v-if="recordRows.length" class="record-panel">
      <h3>缺失分区数据记录（规则 {{ activeRuleNo }}，可回查规则版本与快照）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>检测批次号</th>
            <th>规则版本</th>
            <th>检测时间</th>
            <th>期望/缺失</th>
            <th>容忍中</th>
            <th>分区完整性</th>
            <th>是否生效</th>
            <th>缺失分区清单</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in recordRows" :key="String(record.id)">
            <td>{{ record['记录编号'] }}</td>
            <td>{{ record['检测批次号'] }}</td>
            <td>v{{ record['规则版本'] }}</td>
            <td>{{ record['检测时间'] }}</td>
            <td>{{ record['期望分区数'] }} / {{ record['缺失分区数'] }}</td>
            <td>{{ Array.isArray(record['容忍中分区']) ? record['容忍中分区'].length : 0 }}</td>
            <td>{{ record['分区完整性结论'] }}</td>
            <td>{{ record['是否生效'] ? '生效' : '未生效' }}</td>
            <td>{{ formatPartitions(record['缺失分区清单']) }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条分区检测记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | Record<string, unknown> | unknown[]>

const ENDPOINT = '/api/partitioncheck'
const columns = ['规则编号', '数据表名', '期望分区数', '分区延迟容忍', '缺失分区告警', '检测间隔分钟', '自动补齐开关', '补齐优先级']
const actions = ['提交规则评审', '启用规则', '暂停规则']
const stats = ref([
  { label: '检测规则总数', value: 0 },
  { label: '已启用规则', value: 0 },
  { label: '评审中规则', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const createForm = reactive<Record<string, string | number>>({
  规则编号: '',
  数据表名: '',
  期望分区数: '',
  分区延迟容忍: 0,
  检测间隔分钟: 60,
  自动补齐开关: '关闭',
  补齐优先级: '中',
})

const recordRows = ref<Row[]>([])
const activeRuleNo = ref('')

const enabledCount = computed(() => rows.value.filter((row) => row.status === '已启用').length)
const reviewingCount = computed(() => rows.value.filter((row) => row.status === '规则评审中').length)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function formatPartitions(list: unknown): string {
  if (!Array.isArray(list) || !list.length) {
    return '—'
  }
  return list.map((item) => (item as { 分区标识?: string })['分区标识']).join('、')
}

async function submitCreate() {
  errorMessage.value = ''
  const expected = Number(createForm.期望分区数)
  if (!Number.isInteger(expected) || expected <= 0) {
    errorMessage.value = '期望分区数必须是正整数，不允许负数或零'
    return
  }
  const tolerance = Number(createForm.分区延迟容忍 || 0)
  const interval = Number(createForm.检测间隔分钟 || 0)
  if (tolerance < 0 || interval < 0) {
    errorMessage.value = '分区延迟容忍与检测间隔分钟不允许为负数'
    return
  }
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm, 期望分区数: expected, 分区延迟容忍: tolerance, 检测间隔分钟: interval } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '检测规则登记失败')
    }
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测规则登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '分区检测动作未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分区检测操作失败'
  }
}

async function runDetect(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/detect`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '检测未生效')
    }
    await loadRecords(row)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分区检测执行失败'
  }
}

async function loadRecords(row: Row) {
  errorMessage.value = ''
  activeRuleNo.value = String(row['规则编号'])
  try {
    const response = await request(`${ENDPOINT}/missing-partitions?rule_id=${row.id}&size=50`)
    if (!response.ok) {
      throw new Error('缺失分区记录读取失败')
    }
    const payload = await response.json()
    recordRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺失分区记录读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('检测规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '检测规则总数', value: total.value },
      { label: '已启用规则', value: enabledCount.value },
      { label: '评审中规则', value: reviewingCount.value },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分区检测列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.create-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
.record-panel {
  margin-top: 16px;
}
.record-panel h3 {
  font-size: 14px;
  margin: 0 0 8px;
}
</style>
