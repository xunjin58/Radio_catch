export type ApiJob = { id: string; status: string; task_type: string; progress: number; error_message?: string }

export type Clip = {
  id: string; clip_id: string; filename: string; dish: string | null; segment_role: string
  review_status: string; summary: string | null; tags: Record<string, unknown>
  confidence: number | null; usable_range: { start: number; end: number } | null; created_at: string
}
export type Render = {
  id: string; video_id: string; experiment_id: string | null; dish: string; title: string | null
  status: string; output_path: string | null; duration_seconds: number | null
  edit_decision_list: Array<{ clip_id: string; start: number; end: number; speed: number; role: string }>
  width: number; height: number; experiment_values: Record<string, unknown>
  published_at: string | null; created_at: string; updated_at: string
}
export type Pattern = { dimension: string; value: string; sample_size: number; experiment_count: number; average_score: number; relative_lift: number; confidence: number; status: string }
export type ModelConfig = { id: string; name: string; provider: string; protocol: string; base_url: string; api_key_masked: string; model_name: string; supports_images: boolean; supports_native_video: boolean; supports_structured_json: boolean; max_native_media_bytes: number; is_default: boolean; is_active: boolean; last_error: string | null }
export type ProjectSettings = { business_context: string }
export type ExperimentRequest = { name: string; dish: string; target_duration_seconds: number; generation_count: number; variables: Record<string, unknown>; variants: Array<{ name: string; clips: Array<{ clip_id: string; start?: number; end?: number; speed?: number }>; values?: Record<string, unknown> }> }
export type RemixStrategy = { id: string; name: string; reason: string; allocation: number }
export type RemixVariant = { id: string; strategy_id: string; name: string; reason: string; substitution_note: string; clips: Array<{ clip_id: string; start: number; end: number; speed: number }> }
export type RemixPlan = { candidate_count: number; included_candidate_count: number; excluded_candidate_count: number; candidate_selection_note: string; requested_count: number; planned_count: number; target_duration_seconds: number; strategies: RemixStrategy[]; variants: RemixVariant[]; shortfall_reason: string | null; planner_model_config_id: string }

const origin = import.meta.env.VITE_API_URL ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${origin}/api${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail ?? '请求失败')
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>('/health'),
  dashboard: () => request<Record<string, number>>('/dashboard'),
  clips: (query = '') => request<Clip[]>(`/clips${query}`),
  clip: (id: string) => request<Clip>(`/clips/${id}`),
  clipVideoUrl: (id: string) => `${origin}/api/media/clips/${encodeURIComponent(id)}/video`,
  jobs: () => request<ApiJob[]>('/jobs'),
  upload: (file: File) => { const data = new FormData(); data.append('file', file); return request<unknown>('/media/imports', { method: 'POST', body: data }) },
  approveClip: (id: string, payload: unknown) => request<unknown>(`/clips/${id}/review`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  updateClipMetadata: (id: string, payload: unknown) => request<Clip>(`/clips/${id}/metadata`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  analyzeClip: (id: string) => request<unknown>(`/clips/${id}/analyze`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ mode: 'auto' }) }),
  experiments: () => request<unknown[]>('/experiments'),
  createExperiment: (payload: ExperimentRequest) => request<{ renders: Render[] }>('/experiments', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  planRemix: (payload: { name: string; dish: string; requested_count: number; target_duration_seconds: number }) => request<RemixPlan>('/remix-plans', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  renders: () => request<Render[]>('/renders'),
  render: (id: string) => request<Render>(`/renders/${id}`),
  runRender: (id: string) => request<Render>(`/renders/${id}/run`, { method: 'POST' }),
  renderVideoUrl: (id: string) => `${origin}/api/renders/${encodeURIComponent(id)}/video`,
  renderThumbnailUrl: (id: string) => `${origin}/api/renders/${encodeURIComponent(id)}/thumbnail`,
  renderDownloadUrl: (id: string) => `${origin}/api/renders/${id}/download`,
  metrics: (file: File) => { const data = new FormData(); data.append('file', file); return request<unknown>('/metrics/import', { method: 'POST', body: data }) },
  patterns: () => request<{ sample_size: number; global_score: number | null; patterns: Pattern[]; message?: string }>('/analysis/patterns'),
  recommendations: () => request<{ recommendations: Array<{ suggestion: string; reason: string }> }>('/analysis/recommendations'),
  projectSettings: () => request<ProjectSettings>('/project-settings'),
  updateProjectSettings: (payload: ProjectSettings) => request<ProjectSettings>('/project-settings', { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  modelConfigs: () => request<ModelConfig[]>('/model-configs'),
  createModelConfig: (payload: Record<string, unknown>) => request<ModelConfig>('/model-configs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  testModelConfig: (id: string) => request<{ ok: boolean; detail: string; latency_ms: number }>(`/model-configs/${id}/test-connection`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }),
}
