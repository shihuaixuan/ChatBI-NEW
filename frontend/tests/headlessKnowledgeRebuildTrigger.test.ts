import * as assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const semanticView = readFileSync(resolve(__dirname, '../src/views/system/semantic/index.vue'), 'utf8')
const headlessApi = readFileSync(resolve(__dirname, '../src/api/headless.ts'), 'utf8')

const datasetSection = semanticView.match(/<div v-if="activeTab === 'datasets'"[\s\S]*?<div v-if="activeTab === 'terms'"/)?.[0] || ''
const datasetActionColumn = datasetSection.match(/<el-table-column label="操作"[\s\S]*?<\/el-table-column>/)?.[0] || ''

assert.match(datasetActionColumn, /@click="rebuildKnowledge\(row\.id\)"/)
assert.match(datasetActionColumn, />重建向量索引<\/el-button>/)
assert.match(semanticView, /ElMessage\.success\('向量索引重建任务已提交'\)/)
assert.match(headlessApi, /request\.post\('\/headless\/knowledge\/rebuild'/)
assert.doesNotMatch(semanticView, /rebuildMetricEmbeddings|metricEmbeddingSucceededByDataset/)
assert.doesNotMatch(headlessApi, /metric-embeddings|metricEmbedding/)
