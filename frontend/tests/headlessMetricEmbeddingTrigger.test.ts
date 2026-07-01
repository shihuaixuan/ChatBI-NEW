import * as assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const semanticView = readFileSync(resolve(__dirname, '../src/views/system/semantic/index.vue'), 'utf8')

const datasetSection = semanticView.match(/<div v-if="activeTab === 'datasets'"[\s\S]*?<div v-if="activeTab === 'terms'"/)?.[0] || ''
const datasetActionColumn = datasetSection.match(/<el-table-column label="操作"[\s\S]*?<\/el-table-column>/)?.[0] || ''

assert.match(datasetActionColumn, /向量化指标/)
assert.match(datasetActionColumn, /@click="rebuildMetricEmbeddings\(row\.id\)"/)
