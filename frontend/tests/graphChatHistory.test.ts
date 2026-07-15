// 验证 Graph 历史恢复依赖稳定 API 与逐记录执行类型，而不是当前全局开关。
import * as assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const apiSource = readFileSync(resolve(__dirname, '../src/api/graph-workflow.ts'), 'utf8')
const answerSource = readFileSync(
  resolve(__dirname, '../src/views/chat/answer/GraphWorkflowAnswer.vue'),
  'utf8'
)
const indexSource = readFileSync(resolve(__dirname, '../src/views/chat/index.vue'), 'utf8')
const chatApiSource = readFileSync(resolve(__dirname, '../src/api/chat.ts'), 'utf8')

assert.match(apiSource, /streamChatQuery:\s*\(/)
assert.match(apiSource, /`\/graph\/chats\/\$\{chatId\}\/queries\/stream`/)
assert.match(apiSource, /record_id\?: number/)
assert.doesNotMatch(apiSource, /chat_id\?: number/)
assert.match(answerSource, /streamChatQuery\(\s*_currentChatId\.value/)
assert.match(answerSource, /currentRecord\.id = run\.record_id/)
assert.match(answerSource, /currentRecord\.execution_type = 'graph'/)
assert.match(answerSource, /\['running', 'waiting_input'\]\.includes/)
assert.match(chatApiSource, /execution_type\?: 'legacy' \| 'agentic' \| 'graph'/)
assert.match(chatApiSource, /record\.execution_type = data\.execution_type/)
assert.match(indexSource, /answerComponentForRecord/)
assert.match(indexSource, /record\?\.execution_type === 'graph'/)
assert.match(indexSource, /:is="answerComponentForRecord\(message\.record\)"/)
