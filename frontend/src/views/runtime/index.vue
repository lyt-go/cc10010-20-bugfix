<template>
  <section class="page" data-module="runtime">
    <header class="page-head">
      <div>
        <h2>运行实例管理</h2>
        <p class="page-desc">记录每次ETL作业的运行实例，追踪执行状态和数据吞吐量；读取缺失分区记录后再决定补跑。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记运行记录</button>
        <button class="btn" type="button" @click="exportRows">导出运行实例清单</button>
      </div>
    </header>

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
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无运行实例数据，可先登记运行记录</td>
        </tr>
      </tbody>
    </table>

    <section class="record-panel">
      <div class="record-head">
        <h3>缺失分区数据记录（来自分区检测模块，作为补跑唯一依据）</h3>
        <button class="btn" type="button" @click="loadMissingPartitions">刷新缺失分区记录</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>检测批次号</th>
            <th>规则编号</th>
            <th>规则版本</th>
            <th>数据表名</th>
            <th>缺失分区数</th>
            <th>分区完整性</th>
            <th>自动补齐</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in missingRows" :key="String(record.id)">
            <td>{{ record['记录编号'] }}</td>
            <td>{{ record['检测批次号'] }}</td>
            <td>{{ record['规则编号'] }}</td>
            <td>v{{ record['规则版本'] }}</td>
            <td>{{ record['数据表名'] }}</td>
            <td>{{ record['缺失分区数'] }}</td>
            <td>{{ record['分区完整性结论'] }}</td>
            <td>{{ record['自动补齐开关'] }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="reviewFill(record)">补跑判定</button>
              <button class="link" type="button" @click="createFillTask(record)">创建补齐任务</button>
            </td>
          </tr>
          <tr v-if="!missingRows.length">
            <td :colspan="9" class="empty-state">暂无生效的缺失分区记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="record-panel">
      <div class="record-head">
        <h3>分区补齐任务（读取记录后生成，可回查规则版本）</h3>
        <button class="btn" type="button" @click="loadFillTasks">刷新补齐任务</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>任务编号</th>
            <th>检测批次号</th>
            <th>规则编号</th>
            <th>规则版本</th>
            <th>数据表名</th>
            <th>待补齐分区</th>
            <th>任务状态</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="task in fillRows" :key="String(task.id)">
            <td>{{ task['任务编号'] }}</td>
            <td>{{ task['检测批次号'] }}</td>
            <td>{{ task['规则编号'] }}</td>
            <td>v{{ task['规则版本'] }}</td>
            <td>{{ task['数据表名'] }}</td>
            <td>{{ formatPartitions(task['待补齐分区']) }}</td>
            <td>{{ task['任务状态'] }}</td>
            <td>{{ task['创建时间'] }}</td>
          </tr>
          <tr v-if="!fillRows.length">
            <td :colspan="8" class="empty-state">暂无补齐任务，需先读取缺失分区记录再创建</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条运行实例记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | unknown[]>

const ENDPOINT = '/api/runtime'
const columns = ['记录编号', '作业名称', '开始时间', '结束时间', '执行状态', '读取行数', '写入行数', '错误信息']
const actions = ['启动作业', '终止作业', '标记成功']
const stats = ref([
  { label: '运行记录总数', value: 0 },
  { label: '今日运行数', value: 0 },
  { label: '失败记录数', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const missingRows = ref<Row[]>([])
const fillRows = ref<Row[]>([])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '运行记录登记入口尚未接入审批流'
}

function formatPartitions(list: unknown): string {
  if (!Array.isArray(list) || !list.length) {
    return '—'
  }
  return list.join('、')
}

async function postJson(path: string, body: Record<string, unknown>): Promise<{ ok: boolean; message: string }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return response.json()
}

async function reviewFill(record: Row) {
  errorMessage.value = ''
  try {
    const payload = await postJson(`${ENDPOINT}/fill-tasks/review`, { missing_record_id: record.id })
    errorMessage.value = payload.ok ? '' : payload.message
    window.alert(payload.message)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补跑判定失败'
  }
}

async function createFillTask(record: Row) {
  errorMessage.value = ''
  try {
    const payload = await postJson(`${ENDPOINT}/fill-tasks`, { missing_record_id: record.id })
    if (!payload.ok) {
      throw new Error(payload.message)
    }
    await loadFillTasks()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补齐任务创建失败'
  }
}

async function loadMissingPartitions() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/missing-partitions?only_effective=true&size=50`)
    if (!response.ok) {
      throw new Error('缺失分区记录读取失败')
    }
    const payload = await response.json()
    missingRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺失分区记录读取失败'
  }
}

async function loadFillTasks() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/fill-tasks?size=50`)
    if (!response.ok) {
      throw new Error('补齐任务读取失败')
    }
    const payload = await response.json()
    fillRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补齐任务读取失败'
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
      throw new Error(payload.message || '运行实例动作未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运行实例操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('运行记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value[0].value = total.value
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运行实例列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadMissingPartitions()
  void loadFillTasks()
})
</script>

<style scoped>
.record-panel {
  margin-top: 16px;
}
.record-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.record-head h3 {
  font-size: 14px;
  margin: 0;
}
</style>
