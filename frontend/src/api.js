const BASE = import.meta.env.VITE_API_BASE || ''

async function requestText(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, options)
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      /* keep the default message */
    }
    throw new Error(detail)
  }
  return response.text()
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, options)
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      /* keep the default message */
    }
    throw new Error(detail)
  }
  const type = response.headers.get('content-type') || ''
  if (type.includes('application/json')) return response.json()
  return response.text()
}

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return request('/api/files/upload', { method: 'POST', body: form })
}

export function getFile(fileId) {
  return request(`/api/files/${fileId}`)
}

export function getPreview(fileId, signal) {
  return request(`/api/files/${fileId}/preview`, { signal })
}

/** The DOCX container rendered by docx-preview (original layout preview). */
export async function getRenderBlob(fileId, signal) {
  const response = await fetch(`${BASE}/api/files/${fileId}/render`, { signal })
  if (!response.ok) {
    let detail = `原貌预览加载失败 (${response.status})`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      /* keep the default message */
    }
    throw new Error(detail)
  }
  return response.blob()
}

export function createComparison(fileAId, fileBId, config, wait = false) {
  return request(`/api/comparisons?wait=${wait}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      file_a_id: fileAId,
      file_b_id: fileBId,
      config,
    }),
  })
}

export function getComparison(id) {
  return request(`/api/comparisons/${id}`)
}

export function listComparisons({ limit = 100, offset = 0, keyword = '', status = '' } = {}) {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (keyword) params.set('keyword', keyword)
  if (status) params.set('status', status)
  return request(`/api/comparisons?${params.toString()}`)
}

export function getResult(id) {
  return request(`/api/comparisons/${id}/result`)
}

export function updateConfig(id, config, wait = true) {
  return request(`/api/comparisons/${id}/config?wait=${wait}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config }),
  })
}

/**
 * Returns the raw report body. JSON reports come back as text so the caller can
 * hand them to the browser verbatim instead of re-serialising a parsed object.
 */
export function exportReport(id, format = 'markdown') {
  return requestText(`/api/comparisons/${id}/export?format=${format}`, { method: 'POST' })
}

export const EXPORT_FORMATS = [
  { value: 'markdown', label: 'Markdown', extension: 'md', mime: 'text/markdown;charset=utf-8' },
  { value: 'html', label: 'HTML', extension: 'html', mime: 'text/html;charset=utf-8' },
  { value: 'json', label: 'JSON', extension: 'json', mime: 'application/json;charset=utf-8' },
]

/** The OCR settings the user changed in this browser. */
export function getOcrSettings() {
  return request('/api/settings/ocr')
}

export function updateOcrSettings(payload) {
  return request('/api/settings/ocr', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** Check a configuration before saving it - does not use any quota. */
export function testOcrSettings(payload) {
  return request('/api/settings/ocr/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
