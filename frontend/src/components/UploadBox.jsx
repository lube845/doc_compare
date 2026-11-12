import { useRef, useState } from 'react'
import { ACCEPT_ATTRIBUTE, extensionOf, isAcceptedFile } from '../config'
import { IconUpload } from './Icons'

const SPREADSHEET_TYPES = ['et', 'xls', 'xlsx']

/** Badge colour per extension — mirrors the reference prototype. */
const TYPE_COLORS = {
  doc: '#3b82f6',
  docx: '#3b82f6',
  wps: '#f59e0b',
  et: '#22c55e',
  xls: '#22c55e',
  xlsx: '#22c55e',
  pdf: '#ef4444',
}

const STATUS_LABELS = {
  uploaded: '排队解析',
  validating: '校验中',
  parsing: '解析中',
  normalizing: '整理中',
  success: '解析成功',
  partial: '部分解析',
  failed: '解析失败',
  unsupported_ocr: '扫描件·未识别',
}

/**
 * One half of the upload row: a drop target that turns into a file card once
 * a document has been selected.
 */
export default function UploadBox({ file, side, label, onUpload, onRemove }) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState('')
  const dragDepth = useRef(0)

  const handleFiles = (fileList) => {
    const picked = fileList?.[0]
    if (!picked) return
    if (!isAcceptedFile(picked.name)) {
      setLocalError(
        `不支持的文件格式：.${extensionOf(picked.name) || '未知'}，请上传 DOC / DOCX / WPS / ET / XLS / XLSX / PDF`,
      )
      return
    }
    setLocalError('')
    onUpload(picked, side)
  }

  const openPicker = () => inputRef.current?.click()

  const extension = extensionOf(file?.filename)
  const hasFile = Boolean(file)

  return (
    <div
      className={`upload-box ${dragging ? 'dragging' : ''} ${hasFile ? 'has-file' : ''}`}
      role="button"
      tabIndex={0}
      aria-label={`文件 ${side.toUpperCase()}：点击或拖拽上传`}
      onDragEnter={(event) => {
        event.preventDefault()
        dragDepth.current += 1
        setDragging(true)
      }}
      onDragOver={(event) => {
        event.preventDefault()
        event.dataTransfer.dropEffect = 'copy'
      }}
      onDragLeave={(event) => {
        event.preventDefault()
        dragDepth.current -= 1
        if (dragDepth.current <= 0) {
          dragDepth.current = 0
          setDragging(false)
        }
      }}
      onDrop={(event) => {
        event.preventDefault()
        dragDepth.current = 0
        setDragging(false)
        handleFiles(event.dataTransfer.files)
      }}
      onClick={openPicker}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          openPicker()
        }
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTRIBUTE}
        hidden
        onChange={(event) => {
          handleFiles(event.target.files)
          // Reset so picking the very same file again still fires `change`.
          event.target.value = ''
        }}
      />

      {hasFile ? (
        <div className="file-info">
          <div
            className="file-icon"
            style={{ background: TYPE_COLORS[extension] || '#64748b' }}
            aria-hidden="true"
          >
            {(extension || '?').toUpperCase()}
          </div>
          <div className="file-name" title={file.filename}>
            {file.filename}
          </div>
          <div className="file-meta">
            <span>
              {[
                formatSize(file.size),
                file.page_count ? `${file.page_count} 页` : null,
                file.sheet_count ? `${file.sheet_count} 个工作表` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
            <span className={`status status-${file.parse_status}`}>
              {STATUS_LABELS[file.parse_status] || file.parse_status}
              {isParsing(file) && file.parse_progress ? ` ${file.parse_progress}%` : ''}
            </span>
          </div>
          {isParsing(file) && (
            <div className="file-progress" aria-hidden="true">
              <div
                className="file-progress-fill"
                style={{ width: `${Math.max(4, file.parse_progress || 0)}%` }}
              />
            </div>
          )}
          {file.format_mismatch && (
            <div className="hint">⚠ 文件后缀与实际格式不一致，已按实际格式解析</div>
          )}
          {/* the page shows a dedicated reminder for a scan that could not be read */}
          {file.message &&
            file.parse_status !== 'success' &&
            file.parse_status !== 'unsupported_ocr' && (
            <div className="hint">{file.message}</div>
          )}
          {file.uncovered?.length > 0 && (
            <div className="hint">未覆盖：{file.uncovered.join('、')}</div>
          )}
          <button
            className="file-remove"
            onClick={(event) => {
              event.stopPropagation()
              onRemove(side)
            }}
          >
            移除文件
          </button>
        </div>
      ) : (
        <div className="upload-placeholder">
          <span className="upload-icon">
            <IconUpload size={24} />
          </span>
          <span className="upload-title">{label}</span>
          <span className="upload-hint">点击或拖拽上传</span>
        </div>
      )}

      {localError && <div className="slot-error">{localError}</div>}
    </div>
  )
}

export function isSpreadsheet(filename) {
  return SPREADSHEET_TYPES.includes(extensionOf(filename))
}

/** True while the server is still parsing the file. */
function isParsing(file) {
  return Boolean(file) && ['uploaded', 'validating', 'parsing', 'normalizing'].includes(file.parse_status)
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}
