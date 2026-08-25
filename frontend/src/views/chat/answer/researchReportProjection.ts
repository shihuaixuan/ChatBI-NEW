type ResearchReportItem = Record<string, unknown>

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item || '').trim()).filter(Boolean)
    : []
}

function objectList(value: unknown): ResearchReportItem[] {
  return Array.isArray(value)
    ? value.filter((item): item is ResearchReportItem => Boolean(item) && typeof item === 'object')
    : []
}

/** 将后端审计用 Research JSON 投影为用户可读 Markdown。 */
export function projectResearchReportAnswer(answer: string): string {
  const source = answer.trim()
  if (!source.startsWith('{')) return answer

  let payload: ResearchReportItem
  try {
    payload = JSON.parse(source) as ResearchReportItem
  } catch {
    return answer
  }
  if (payload.kind !== 'final' && payload.kind !== 'partial') return answer

  const sections: string[] = []
  const summary = String(payload.summary || '').trim()
  if (summary) sections.push(summary)

  const findings = objectList(payload.findings)
    .map((item) => String(item.statement || '').trim())
    .filter(Boolean)
  if (findings.length) {
    sections.push(`主要结论：\n${findings.map((item) => `- ${item}`).join('\n')}`)
  }

  const confirmed = objectList(payload.confirmed)
    .map((item) => String(item.purpose || '').trim())
    .filter(Boolean)
  if (confirmed.length) {
    sections.push(`已完成的验证：\n${confirmed.map((item) => `- ${item}`).join('\n')}`)
  }

  const unconfirmed = stringList(payload.unconfirmed)
  if (unconfirmed.length) {
    sections.push(`尚未确认：\n${unconfirmed.map((item) => `- ${item}`).join('\n')}`)
  }

  const limitations = stringList(payload.limitations)
  if (limitations.length) {
    sections.push(`分析限制：\n${limitations.map((item) => `- ${item}`).join('\n')}`)
  }

  const recommendation = String(payload.recommendation || '').trim()
  if (recommendation) sections.push(`建议：${recommendation}`)
  return sections.join('\n\n') || '研究已结束，但没有生成可展示的结论。'
}

