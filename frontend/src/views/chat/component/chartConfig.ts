import type { ChartAxis, ChartTypes } from '@/views/chat/component/BaseChart.ts'

type RawAxis = string | Partial<ChartAxis> | undefined

export interface ChartConfig {
  type?: ChartTypes
  title?: string
  columns?: Array<ChartAxis>
  axis: {
    x?: ChartAxis
    y?: ChartAxis | Array<ChartAxis>
    series?: ChartAxis
    'multi-quota'?: {
      name: string
      value: Array<string>
    }
  }
}

interface RawChartConfig {
  type?: ChartTypes
  title?: string
  columns?: Array<ChartAxis>
  axis?: ChartConfig['axis']
  x?: RawAxis
  y?: RawAxis | Array<RawAxis>
  series?: RawAxis
}

function toAxis(value: RawAxis, type: ChartAxis['type']): ChartAxis | undefined {
  if (typeof value === 'string' && value) {
    return { name: value, value, type }
  }
  if (!value || typeof value !== 'object' || !value.value) {
    return undefined
  }
  return {
    ...value,
    name: value.name || value.value,
    value: value.value,
    type: value.type || type,
  } as ChartAxis
}

export function normalizeChartConfig(raw: RawChartConfig): ChartConfig {
  const rawAxis = raw.axis || {}
  // 兼容 Agent 链路的扁平配置和旧 Chat 链路的 axis 配置。
  const x = toAxis(rawAxis.x || raw.x, 'x')
  const rawY = rawAxis.y || raw.y
  const yValues: Array<RawAxis> = rawY ? (Array.isArray(rawY) ? rawY : [rawY]) : []
  const y = yValues
    .map((item) => toAxis(item as RawAxis, 'y'))
    .filter((item): item is ChartAxis => Boolean(item))
  let series = toAxis(rawAxis.series || raw.series, 'series')

  // 饼图的类别字段在 Agent 配置中位于 x，需要转换为 series。
  if (raw.type === 'pie' && !series && x) {
    series = { ...x, type: 'series' }
  }

  return {
    type: raw.type,
    title: raw.title,
    columns: raw.columns || [],
    axis: {
      ...rawAxis,
      x,
      y: y.length === 1 ? y[0] : y,
      series,
    },
  }
}
