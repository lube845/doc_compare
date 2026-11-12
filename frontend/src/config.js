/** Shared configuration defaults for the comparison engine (PRD 5.1 - 5.3). */
export const DEFAULT_CONFIG = {
  ignore_whitespace: true,
  ignore_newlines: true,
  ignore_headers_footers: true,
  ignore_punctuation: false,
  ignore_case: false,
  ignore_blank_cells: false,
  ignore_formula: false,
  ignore_full_width: true,
  ignore_numbering: false,
  detect_moved_paragraphs: true,
  compare_formula: true,
  compare_display_value: true,
  compare_cell_format: false,
  compare_row_order: true,
  ignore_blank_rows: true,
  compare_sheet_order: true,
  // PDF 对 PDF 时使用字符流比对（PDF 比对改造实施计划 · 阶段 4）
  pdf_stream_mode: true,
  primary_key_columns: [],
}

/** Extensions accepted by the backend (`settings.allowed_extensions`). */
export const ACCEPTED_EXTENSIONS = [
  'doc',
  'docx',
  'wps',
  'et',
  'xls',
  'xlsx',
  'pdf',
]

export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.map((ext) => `.${ext}`).join(',')

/** Format code → display name. */
export const FORMAT_LABELS = {
  doc: 'DOC',
  docx: 'DOCX',
  wps: 'WPS',
  et: 'ET',
  xls: 'XLS',
  xlsx: 'XLSX',
  pdf: 'PDF',
}

export function extensionOf(filename) {
  if (!filename || !filename.includes('.')) return ''
  return filename.split('.').pop().toLowerCase()
}

export function isAcceptedFile(filename) {
  return ACCEPTED_EXTENSIONS.includes(extensionOf(filename))
}

/**
 * The format of an uploaded file.
 *
 * The server reports the format it detected from the file content, so a
 * `.docx` that really is a PDF is treated as a PDF. The extension is only
 * a fallback for the moment before the upload answers.
 */
export function formatOf(file) {
  if (!file) return ''
  return (file.file_type || extensionOf(file.filename) || '').toLowerCase()
}

export function formatLabel(file) {
  const code = formatOf(file)
  return FORMAT_LABELS[code] || (code || '未知').toUpperCase()
}

/** Only files of the very same format may be compared. */
export function isSameFormat(fileA, fileB) {
  const a = formatOf(fileA)
  const b = formatOf(fileB)
  return Boolean(a) && a === b
}

/** The message shown when two files of different formats are paired. */
export function formatMismatchMessage(fileA, fileB) {
  return `仅支持同格式比对：文件 A 为 ${formatLabel(fileA)}，文件 B 为 ${formatLabel(
    fileB,
  )}，请上传两个格式相同的文件`
}

/** Empty filter state for the difference list (PRD 10.4). */
export const DEFAULT_FILTER = { type: 'all', keyword: '', sheet: '', section: '' }
