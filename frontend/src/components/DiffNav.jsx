import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { IconSearch } from './Icons'

/** Differences are rendered in batches so a huge result stays responsive. */
const BATCH_SIZE = 200

const TYPE_LABELS = {
  added: '新增',
  deleted: '删除',
  modified: '修改',
  moved: '移动',
  unchanged: '未变更',
}

const FILTERS = [
  ['all', '全部'],
  ['added', '新增'],
  ['deleted', '删除'],
  ['modified', '修改'],
  ['moved', '移动'],
]

/**
 * Left hand side navigation of the result page: filter the differences by
 * type, search them by keyword and jump to a specific one.
 */
function DiffNav({
  changes,
  emptyText,
  stats,
  filter,
  onFilterChange,
  sections,
  sheets,
  selectedChange,
  onSelect,
}) {
  const listRef = useRef(null)
  const [visibleCount, setVisibleCount] = useState(BATCH_SIZE)

  useEffect(() => {
    setVisibleCount(BATCH_SIZE)
  }, [filter.type, filter.sheet, filter.section, filter.keyword])

  // Group the differences by chapter / worksheet / page, mirroring the
  // structure of the compared documents.
  const groups = useMemo(() => {
    const result = []
    let current = null
    changes.slice(0, visibleCount).forEach((change) => {
      const location = change.document_b?.location || change.document_a?.location || {}
      const title =
        location.section ||
        location.sheet_name ||
        (location.page ? `第 ${location.page} 页` : '其他位置')
      if (!current || current.title !== title) {
        current = { title, items: [] }
        result.push(current)
      }
      current.items.push(change)
    })
    return result
  }, [changes, visibleCount])

  // Keep the selected difference visible when it is driven from the keyboard.
  useEffect(() => {
    if (!selectedChange) return
    const node = listRef.current?.querySelector(
      `[data-change-id="${CSS.escape(selectedChange)}"]`,
    )
    node?.scrollIntoView({ block: 'nearest' })
  }, [selectedChange, groups])

  const filtersActive =
    filter.type !== 'all' || filter.keyword || filter.sheet || filter.section

  return (
    <div className="diff-nav-inner">
      <div className="diff-nav-header">
        <div className="diff-nav-filter" role="group" aria-label="按差异类型筛选">
          {FILTERS.map(([value, label]) => (
            <button
              key={value}
              className={filter.type === value ? 'active' : ''}
              aria-pressed={filter.type === value}
              onClick={() => onFilterChange({ ...filter, type: value })}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="diff-nav-search">
          <IconSearch size={14} />
          <input
            className="search"
            type="search"
            aria-label="搜索差异内容"
            placeholder="搜索差异内容..."
            value={filter.keyword}
            onChange={(event) => onFilterChange({ ...filter, keyword: event.target.value })}
          />
        </div>

        {(sections.length > 0 || sheets.length > 0) && (
          <div className="diff-nav-selects">
            {sections.length > 0 && (
              <select
                aria-label="按章节筛选"
                value={filter.section}
                onChange={(event) => onFilterChange({ ...filter, section: event.target.value })}
              >
                <option value="">全部章节</option>
                {sections.map((section) => (
                  <option key={section} value={section}>
                    {section}
                  </option>
                ))}
              </select>
            )}
            {sheets.length > 0 && (
              <select
                aria-label="按工作表筛选"
                value={filter.sheet}
                onChange={(event) => onFilterChange({ ...filter, sheet: event.target.value })}
              >
                <option value="">全部工作表</option>
                {sheets.map((sheet) => (
                  <option key={sheet} value={sheet}>
                    {sheet}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        {filtersActive && (
          <button
            className="ghost"
            onClick={() => onFilterChange({ type: 'all', keyword: '', sheet: '', section: '' })}
          >
            清除筛选
          </button>
        )}
      </div>

      <div className="diff-nav-list" role="listbox" aria-label="差异清单" ref={listRef}>
        {changes.length === 0 && (
          <div className="history-empty">
            {filtersActive
              ? '没有符合筛选条件的差异'
              : emptyText || '未发现差异，两份文件内容一致'}
          </div>
        )}

        {groups.map((group) => (
          <div className="diff-group" key={group.title}>
            <div className="diff-group-title">{group.title}</div>
            {group.items.map((change) => (
              <button
                key={change.change_id}
                className={`diff-item ${
                  change.change_id === selectedChange ? 'active' : ''
                }`}
                data-change-id={change.change_id}
                role="option"
                aria-selected={change.change_id === selectedChange}
                onClick={() => onSelect(change)}
              >
                <span className="di-type">
                  <span className={`badge badge-${change.primary_type}`}>
                    {TYPE_LABELS[change.primary_type] || change.primary_type}
                  </span>
                </span>
                <span className="di-text">{change.summary}</span>
                {change.document_a?.location?.page ||
                change.document_b?.location?.page ? (
                  <span className="di-pages">
                    {pageLabel(change.document_a)} ↔ {pageLabel(change.document_b)}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ))}

        {visibleCount < changes.length && (
          <button
            className="ghost"
            style={{ width: '100%' }}
            onClick={() => setVisibleCount((count) => count + BATCH_SIZE)}
          >
            显示更多（还有 {changes.length - visibleCount} 项）
          </button>
        )}
      </div>
    </div>
  )
}

function pageLabel(side) {
  const page = side?.location?.page
  return page ? `P${page}` : '—'
}

export default memo(DiffNav)
