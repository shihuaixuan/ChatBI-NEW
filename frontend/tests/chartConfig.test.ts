import * as assert from 'node:assert/strict'
import { normalizeChartConfig } from '../src/views/chat/component/chartConfig.ts'

const normalized = normalizeChartConfig({
  type: 'bar',
  x: 'stall_id',
  y: ['order_customer_cnt_total'],
})

assert.deepEqual(normalized.axis.x, {
  name: 'stall_id',
  value: 'stall_id',
  type: 'x',
})
assert.deepEqual(normalized.axis.y, {
  name: 'order_customer_cnt_total',
  value: 'order_customer_cnt_total',
  type: 'y',
})

const pie = normalizeChartConfig({ type: 'pie', x: 'stall_id', y: 'orders' })
assert.equal(pie.axis.series?.value, 'stall_id')
assert.equal(pie.axis.y && !Array.isArray(pie.axis.y) ? pie.axis.y.value : undefined, 'orders')

const legacy = normalizeChartConfig({
  type: 'line',
  axis: {
    x: { name: '日期', value: 'date' },
    y: { name: '销售额', value: 'amount' },
  },
})
assert.equal(legacy.axis.x?.name, '日期')
assert.equal(
  legacy.axis.y && !Array.isArray(legacy.axis.y) ? legacy.axis.y.name : undefined,
  '销售额'
)
