import { memo } from 'react'
import { IconDocument, IconRefresh, IconSheet } from './Icons'

const MODE_LABELS = {
  document_structure: '文档结构比对',
  spreadsheet_structure: '表格结构比对',
  // 跨格式比对已下线，历史记录只保留一个占位标签
  legacy: '历史比对',
}

const STATUS_LABELS = {
  uploaded: '待处理',
  validating: '校验中',
  waiting_files: '等待文件解析',
  parsing: '解析中',
  normalizing: '规范化中',
  aligning: '对齐中',
  comparing: '比对中',
  analyzing: '分析中',
  completed: '已完成',
  partial_completed: '部分完成',
  failed: '失败',
}

const SPREADSHEET_TYPES = ['et', 'xls', 'xlsx']

/** Task states that are still running on the server. */
const RUNNING_STATUS = [
  'uploaded',
  'validating',
  'waiting_files',
  'parsing',
  'normalizing',
  'aligning',
  'comparing',
  'analyzing',
]

function isRunning(status) {
  return RUNNING_STATUS.includes(status)
}

/** List of the previously created comparison tasks. */
function HistoryPage({ items, loading, error, activeId, onSelect, onRefresh }) {
  return (
    <div className="page-body">
      <div className="history-container">
        <h2>历史比对任务</h2>

        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button onClick={onRefresh} disabled={loading}>
            <IconRefresh size={15} />
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>

        {error && <div className="error">{error}</div>}
        {!error && loading && !items.length && <Skeleton />}
        {!error && !loading && !items.length && (
          <div className="history-empty">暂无历史比对记录</div>
        )}

        {items.map((item) => (
          <div
            key={item.comparison_id}
            className="history-item"
            role="button"
            tabIndex={0}
            onClick={() => onSelect(item)}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget) return
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onSelect(item)
              }
            }}
            title={isRunning(item.status) ? '查看该次比对进度' : '查看该次比对结果'}
          >
            <span className="hi-icon">
              {isSpreadsheet(item.file_a_name) ? (
                <IconSheet size={20} />
              ) : (
                <IconDocument size={20} />
              )}
            </span>

            <span className="hi-info">
              <span className="hi-title">
                {item.file_a_name || '文件 A'} ↔ {item.file_b_name || '文件 B'}
              </span>
              <span className="hi-meta">
                {formatTime(item.created_at)} · {MODE_LABELS[item.mode] || item.mode} ·{' '}
                {isRunning(item.status)
                  ? `${STATUS_LABELS[item.status] || item.status}${
                      item.progress ? ` ${item.progress}%` : ''
                    }`
                  : STATUS_LABELS[item.status] || item.status}
              </span>
              {isRunning(item.status) && (
                <span className="hi-progress" aria-hidden="true">
                  <span
                    className="hi-progress-fill"
                    style={{ width: `${Math.max(4, item.progress || 0)}%` }}
                  />
                </span>
              )}
            </span>

            <span className="hi-badges">
              {item.stats?.added ? (
                <span className="badge badge-added">新增 {item.stats.added}</span>
              ) : null}
              {item.stats?.deleted ? (
                <span className="badge badge-deleted">删除 {item.stats.deleted}</span>
              ) : null}
              {item.stats?.modified ? (
                <span className="badge badge-modified">修改 {item.stats.modified}</span>
              ) : null}
              {item.stats?.moved ? (
                <span className="badge badge-moved">移动 {item.stats.moved}</span>
              ) : null}
            </span>

            <span className="hi-actions">
              <button
                className="ghost"
                onClick={(event) => {
                  event.stopPropagation()
                  onSelect(item)
                }}
              >
                {isRunning(item.status)
                  ? '查看进度'
                  : activeId === item.comparison_id
                    ? '当前查看'
                    : '查看结果'}
              </button>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function isSpreadsheet(filename) {
  const extension = (filename || '').split('.').pop()?.toLowerCase() || ''
  return SPREADSHEET_TYPES.includes(extension)
}

function Skeleton() {
  return (
    <div className="skeleton" aria-hidden="true">
      <div className="skeleton-line" style={{ width: '60%' }} />
      <div className="skeleton-line" style={{ width: '90%' }} />
      <div className="skeleton-line" style={{ width: '45%' }} />
    </div>
  )
}

function formatTime(value) {
  if (!value) return ''
  // The backend stores UTC; SQLite drops the timezone suffix, so add it back.
  const iso = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

export default memo(HistoryPage)
