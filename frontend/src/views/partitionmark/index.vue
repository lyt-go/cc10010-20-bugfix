<template>
  <section class="page" data-module="partitionmark">
    <header class="page-head">
      <div>
        <h2>分区标识管理</h2>
        <p class="page-desc">登记数据表实际到达的分区标识，是分区检测重算缺失分区的唯一事实来源。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = !showCreate">
          {{ showCreate ? '收起登记表单' : '登记分区标识' }}
        </button>
      </div>
    </header>

    <form v-if="showCreate" class="filter-bar create-panel" @submit.prevent="submitCreate">
      <label class="filter-item">
        <span>标识编号 *</span>
        <input v-model="createForm.标识编号" placeholder="如 PMK-0010" />
      </label>
      <label class="filter-item">
        <span>数据表名 *</span>
        <input v-model="createForm.数据表名" placeholder="如 dwd_order_detail" />
      </label>
      <label class="filter-item">
        <span>分区标识 *</span>
        <input v-model="createForm.分区标识" placeholder="如 dt=2026-11-16" />
      </label>
      <label class="filter-item">
        <span>到达时间 *</span>
        <input v-model="createForm.到达时间" placeholder="2026-11-16T02:30:00" />
      </label>
      <button class="btn primary" type="submit">提交登记</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>数据表名</span>
        <input v-model="tableFilter" placeholder="按数据表名过滤" />
      </label>
      <label class="filter-item">
        <span>关键字</span>
        <input v-model="keyword" placeholder="按标识编号/分区标识检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length" class="empty-state">暂无分区标识数据，可先登记实际到达分区</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条分区标识记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/partitionmark'
const columns = ['标识编号', '数据表名', '分区标识', '到达时间']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const tableFilter = ref('')
const keyword = ref('')
const showCreate = ref(false)
const createForm = reactive({ 标识编号: '', 数据表名: '', 分区标识: '', 到达时间: '' })

function resetFilters() {
  tableFilter.value = ''
  keyword.value = ''
  void reload()
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '分区标识登记失败')
    }
    showCreate.value = false
    Object.assign(createForm, { 标识编号: '', 数据表名: '', 分区标识: '', 到达时间: '' })
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分区标识登记失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (tableFilter.value) {
    params.set('table', tableFilter.value)
  }
  if (keyword.value) {
    params.set('keyword', keyword.value)
  }
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('分区标识列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分区标识列表读取失败'
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
</style>
