/**
 * Status helpers for the background tasks.
 *
 * The backend parses a file and runs a comparison in the background, so the
 * frontend has to follow the state of both.
 */

/** Parse states that will not change any more. */
export const TERMINAL_PARSE_STATUS = ['success', 'partial', 'failed', 'unsupported_ocr']

/** Comparison states that will not change any more. */
export const TERMINAL_COMPARE_STATUS = ['completed', 'partial_completed', 'failed']

export function isParseFinished(file) {
  if (!file) return false
  return TERMINAL_PARSE_STATUS.includes(file.parse_status)
}

export function isParseFailed(file) {
  if (!file) return false
  return file.parse_status === 'failed' || file.parse_status === 'unsupported_ocr'
}

export function isTaskRunning(status) {
  return !TERMINAL_COMPARE_STATUS.includes(status)
}

/** The backend stage of a comparison -> the step of the progress page. */
export const STAGE_STEPS = {
  waiting_files: 2,
  parsing: 2,
  normalizing: 3,
  aligning: 3,
  comparing: 4,
  analyzing: 4,
  persisting: 5,
  completed: 5,
}

/** The backend stage -> the text shown under the progress bar. */
export const STAGE_TEXT = {
  waiting_files: '等待文件解析完成',
  parsing: '正在解析文档结构',
  normalizing: '正在规范化文本',
  aligning: '正在对齐段落与单元格',
  comparing: '正在计算文本差异',
  analyzing: '正在分析差异',
  persisting: '正在保存结果',
  completed: '比对完成',
}

/** Fallback when the backend does not report a stage (old tasks). */
export const COMPARE_STATUS_STEPS = {
  uploaded: 1,
  validating: 2,
  parsing: 2,
  normalizing: 3,
  aligning: 3,
  comparing: 4,
  analyzing: 4,
  completed: 5,
  partial_completed: 5,
}

/**
 * The step of the progress page for a running comparison.  The backend stage
 * is the primary signal, the file states are the fallback.
 */
export function stepForTask(detail, fileA, fileB) {
  const stage = detail?.stage
  if (stage && STAGE_STEPS[stage]) return STAGE_STEPS[stage]
  const status = detail?.status
  if (status && COMPARE_STATUS_STEPS[status]) return COMPARE_STATUS_STEPS[status]
  const parsing = [fileA, fileB].some((file) => file?.parse_status === 'parsing')
  if (parsing) return 2
  const parsed = [fileA, fileB].every(
    (file) => file && ['success', 'partial'].includes(file.parse_status),
  )
  return parsed ? 4 : 2
}

/** The text under the progress bar for a running comparison. */
export function textForTask(detail) {
  const stage = detail?.stage
  if (stage && STAGE_TEXT[stage]) return STAGE_TEXT[stage]
  return null
}

export function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}
