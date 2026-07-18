import { request } from '@/utils/request'

const toLegacyTerm = (term: any) => ({
  ...term,
  create_time: term.created_at,
  word: term.name,
  other_words: term.alias || [],
  specific_ds: Boolean(term.related_datasets?.length),
  dataset_ids: term.related_datasets || [],
  mapped_assets: [
    ...(term.related_metrics || []).map((id: number) => ({ asset_type: 'METRIC', asset_id: id })),
    ...(term.related_dimensions || []).map((id: number) => ({
      asset_type: 'DIMENSION',
      asset_id: id,
    })),
  ],
  enabled: term.status === 1,
})

const toSemanticPayload = (term: any) => ({
  domain_id: Number(term.domain_id),
  name: term.word,
  alias: (term.other_words || []).filter(Boolean),
  description: term.description,
  related_datasets: term.specific_ds ? term.dataset_ids || [] : [],
  related_metrics: (term.mapped_assets || [])
    .filter((asset: any) => asset.asset_type === 'METRIC')
    .map((asset: any) => asset.asset_id),
  related_dimensions: (term.mapped_assets || [])
    .filter((asset: any) => asset.asset_type === 'DIMENSION')
    .map((asset: any) => asset.asset_id),
})

const parsedFilters = (params: string) => {
  const filters = new URLSearchParams(params.startsWith('?') ? params.slice(1) : params)
  return {
    word: filters.get('word')?.trim().toLocaleLowerCase() || '',
    domainId: filters.get('domain_id') || '',
    datasetIds: filters.getAll('dslist').map((id) => Number(id)),
  }
}

export const professionalApi = {
  getList: async (pageNum: number, pageSize: number, params: string) => {
    const filters = parsedFilters(params)
    const response = await request.get('/semantic/terms', {
      params: filters.domainId ? { domain_id: filters.domainId } : undefined,
    })
    const terms = (Array.isArray(response) ? response : []).filter((term: any) => {
      const matchesWord =
        !filters.word ||
        String(term.name || '')
          .toLocaleLowerCase()
          .includes(filters.word) ||
        (term.alias || []).some((alias: string) => alias.toLocaleLowerCase().includes(filters.word))
      const matchesDataset =
        !filters.datasetIds.length ||
        !term.related_datasets?.length ||
        term.related_datasets.some((id: number) => filters.datasetIds.includes(id))
      return matchesWord && matchesDataset
    })
    const totalPages = terms.length ? Math.ceil(terms.length / pageSize) : 0
    const currentPage = totalPages ? Math.min(Math.max(pageNum, 1), totalPages) : 1
    const start = (currentPage - 1) * pageSize
    return {
      current_page: currentPage,
      page_size: pageSize,
      total_count: terms.length,
      total_pages: totalPages,
      data: terms.slice(start, start + pageSize).map(toLegacyTerm),
    }
  },
  updateEmbedded: (data: any) =>
    data.id
      ? request.put(`/semantic/terms/${data.id}`, toSemanticPayload(data))
      : request.post('/semantic/terms', toSemanticPayload(data)),
  deleteEmbedded: (ids: number[]) => request.delete('/semantic/terms', { data: ids }),
  enable: (id: number | string, enabled: boolean) =>
    request.patch(`/semantic/terms/${id}/enabled`, undefined, { params: { enabled } }),
  export2Excel: (params: any) =>
    request.get('/semantic/terms/export', {
      params,
      paramsSerializer: { indexes: null },
      responseType: 'blob',
      requestOptions: { customError: true },
    }),
}
