import { useCallback, useEffect, useRef, useState } from 'react'
import { EXPORT_FORMATS } from '../api'
import { alignScroll } from '../lib/panes'
import ConfigSection from './ConfigSection'
import DiffNav from './DiffNav'
import DocxPane from './DocxPane'
import DocumentPane from './DocumentPane'
import NoticeBar from './NoticeBar'
import PdfCompareView from './PdfCompareView'
import SpreadsheetPane from './SpreadsheetPane'
import {
  IconArrowLeft,
  IconArrowRight,
  IconChevronDown,
  IconChevronLeft,
  IconChevronRight,
  IconDownload,
  IconRefresh,
  IconSliders,
} from './Icons'

/**
 * Result page — the header summarises the run, the left column navigates the
 * differences and the two panes show the compared documents side by side.
 */
export default function ResultPage({
  summary,
  result,
  changes,
  filteredChanges,
  filter,
  onFilterChange,
  sections,
  sheets,
  selectedChange,
  onSelect,
  previews,
  previewStatus,
  pdfMode = false,
  config,
  onConfigChange,
  onApplyConfig,
  configOpen,
  onToggleConfig,
  onBack,
  onRecompare,
  onExport,
  exporting,
}) {
  const paneARef = useRef(null)
  const paneBRef = useRef(null)
  const syncing = useRef(false)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  // Both document panes share the zoom level, like a document viewer.
  const [docZoom, setDocZoom] = useState(1)

  const stats = result?.stats || summary?.stats || {}
  const leftDocument =
    result?.document_a?.raw_url
      ? result.document_a
      : {
          file_id: summary?.file_a_id,
          file_name: summary?.file_a_name,
          raw_url: summary?.file_a_id ? `/api/files/${summary.file_a_id}/raw` : '',
          page_count: previews.a?.page_count,
        }
  const rightDocument =
    result?.document_b?.raw_url
      ? result.document_b
      : {
          file_id: summary?.file_b_id,
          file_name: summary?.file_b_name,
          raw_url: summary?.file_b_id ? `/api/files/${summary.file_b_id}/raw` : '',
          page_count: previews.b?.page_count,
        }
  const selectedIndex = filteredChanges.findIndex(
    (change) => change.change_id === selectedChange,
  )

  // Jump to the difference that is currently selected in the list.
  useEffect(() => {
    if (!selectedChange) return
    requestAnimationFrame(() => {
      scrollToNode(paneARef.current, findSide(result, 'a', selectedChange))
      scrollToNode(paneBRef.current, findSide(result, 'b', selectedChange))
    })
  }, [selectedChange, result])

  const jump = useCallback(
    (delta) => {
      if (!filteredChanges.length) return
      const nextIndex =
        selectedIndex === -1
          ? delta > 0
            ? 0
            : filteredChanges.length - 1
          : Math.min(filteredChanges.length - 1, Math.max(0, selectedIndex + delta))
      const next = filteredChanges[nextIndex]
      if (next) onSelect(next)
    },
    [filteredChanges, selectedIndex, onSelect],
  )

  // Keep both panes aligned while scrolling.  The document panes are aligned
  // by page (the layout view), the text panes proportionally.
  const syncScroll = useCallback((source, target) => {
    if (syncing.current || !target) return
    syncing.current = true
    alignScroll(source, target)
    requestAnimationFrame(() => {
      syncing.current = false
    })
  }, [])

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (event.key === 'Escape') {
        setMenuOpen(false)
        return
      }
      const target = event.target
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      if (typing) return
      if (event.key === '/') {
        const search = document.querySelector('.diff-nav-search input')
        if (search) {
          event.preventDefault()
          search.focus()
          search.select()
        }
        return
      }
      if (event.key === 'ArrowDown' || event.key === 'j') {
        event.preventDefault()
        jump(1)
      } else if (event.key === 'ArrowUp' || event.key === 'k') {
        event.preventDefault()
        jump(-1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [jump])

  return (
    <div className="result-page">
      <header className="result-header">
        <div className="result-header-left">
          <button className="back-btn" onClick={onBack} title="返回上传页" aria-label="返回上传页">
            <IconArrowLeft size={16} />
          </button>
          <div className="file-pair">
            <span className="fp-name" title={summary?.file_a_name}>
              {summary?.file_a_name}
            </span>
            <span className="fp-arrow">
              <IconArrowRight size={14} />
            </span>
            <span className="fp-name" title={summary?.file_b_name}>
              {summary?.file_b_name}
            </span>
          </div>
        </div>

        <div className="result-header-center">
          <div className="diff-summary">
            <span className="ds-total">共 {stats.total || 0} 处差异</span>
            <span className="ds-sep">|</span>
            <span className="badge badge-insert">新增 {stats.added || 0}</span>
            <span className="badge badge-delete">删除 {stats.deleted || 0}</span>
            <span className="badge badge-modify">修改 {stats.modified || 0}</span>
            <span className="badge badge-move">移动 {stats.moved || 0}</span>
          </div>
        </div>

        <div className="result-header-right">
          <div className="nav-jump">
            <button
              className="jump-btn"
              onClick={() => jump(-1)}
              title="上一处差异"
              aria-label="上一处差异"
              disabled={!filteredChanges.length}
            >
              <IconChevronLeft size={12} />
            </button>
            <span className="jump-counter">
              {filteredChanges.length
                ? `${selectedIndex === -1 ? 0 : selectedIndex + 1} / ${filteredChanges.length}`
                : '0 / 0'}
            </span>
            <button
              className="jump-btn"
              onClick={() => jump(1)}
              title="下一处差异"
              aria-label="下一处差异"
              disabled={!filteredChanges.length}
            >
              <IconChevronRight size={12} />
            </button>
          </div>

          <div className="export-menu">
            <button
              className="btn-sm"
              onClick={() => setMenuOpen((open) => !open)}
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              disabled={Boolean(exporting)}
            >
              <IconDownload size={14} />
              {exporting ? '导出中…' : '导出报告'}
              <IconChevronDown size={14} />
            </button>
            {menuOpen && (
              <div className="export-dropdown" role="menu">
                {EXPORT_FORMATS.map((format) => (
                  <button
                    key={format.value}
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false)
                      onExport(format.value)
                    }}
                  >
                    {format.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button className="btn-sm" onClick={onRecompare} title="使用当前文件重新比对">
            <IconRefresh size={14} />
            重新比对
          </button>

          <button
            onClick={onToggleConfig}
            title="比对配置"
            aria-expanded={configOpen}
            className={`btn-sm ${configOpen ? 'is-active' : ''}`}
          >
            <IconSliders size={14} />
            {configOpen ? '收起配置' : '比对配置'}
          </button>
        </div>
      </header>

      {configOpen && (
        <ConfigSection
          config={config}
          onChange={onConfigChange}
          appliedConfig={summary?.config}
          onApply={onApplyConfig}
          defaultOpen
        />
      )}

      <NoticeBar notices={result?.notices || summary?.notices || []} />

      <div className="result-body">
        <aside className={`diff-nav ${navCollapsed ? 'collapsed' : ''}`}>
          <DiffNav
            emptyText={
              (result?.notices || []).some(
                (notice) => notice.includes('未参与比对') || notice.includes('扫描'),
              )
                ? '未发现文本差异；部分页面未参与比对'
                : undefined
            }
            changes={filteredChanges}
            stats={stats}
            filter={filter}
            onFilterChange={onFilterChange}
            sections={sections}
            sheets={sheets}
            selectedChange={selectedChange}
            onSelect={onSelect}
          />
        </aside>

        <div className="diff-content">
          <button
            className="nav-toggle"
            onClick={() => setNavCollapsed((value) => !value)}
            title={navCollapsed ? '展开差异清单' : '收起差异清单'}
            aria-label={navCollapsed ? '展开差异清单' : '收起差异清单'}
          >
            {navCollapsed ? <IconChevronRight size={12} /> : <IconChevronLeft size={12} />}
          </button>

          {pdfMode ? (
            <PdfCompareView
              left={leftDocument}
              right={rightDocument}
              changes={changes}
              selectedChange={selectedChange}
              onSelect={onSelect}
              pageMapping={result?.page_mapping || []}
            />
          ) : (
            <>
              <Pane
                side="a"
                tone="#3b82f6"
                label="原始文件"
                name={summary?.file_a_name}
                preview={previews.a}
                status={previewStatus.a}
                changes={changes}
                selectedChange={selectedChange}
                onSelect={onSelect}
                bodyRef={paneARef}
                zoom={docZoom}
                onZoomChange={setDocZoom}
                onScroll={(event) => syncScroll(event.currentTarget, paneBRef.current)}
              />
              <Pane
                side="b"
                tone="#22c55e"
                label="修订文件"
                name={summary?.file_b_name}
                preview={previews.b}
                status={previewStatus.b}
                changes={changes}
                selectedChange={selectedChange}
                onSelect={onSelect}
                bodyRef={paneBRef}
                zoom={docZoom}
                onZoomChange={setDocZoom}
                onScroll={(event) => syncScroll(event.currentTarget, paneARef.current)}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Pane({
  side,
  tone,
  label,
  name,
  preview,
  status,
  changes,
  selectedChange,
  onSelect,
  bodyRef,
  zoom,
  onZoomChange,
  onScroll,
}) {
  const isSpreadsheet = Boolean(preview?.sheets?.length)
  const isDocument = Boolean(preview?.blocks?.length)
  const canShowLayout = ['doc', 'docx', 'wps'].includes(preview?.file_type)
  const [view, setView] = useState('render')
  const [renderFailed, setRenderFailed] = useState(false)
  const fallbackToText = useCallback(() => {
    setView('text')
    setRenderFailed(true)
  }, [])

  // A new file always starts in the layout view.
  useEffect(() => {
    setView('render')
    setRenderFailed(false)
  }, [preview?.file_id])

  return (
    <section className="diff-pane">
      <header className="pane-header">
        <span className="ph-icon" style={{ background: tone }}>
          {side.toUpperCase()}
        </span>
        <span className="ph-label">{label}</span>
        <span className="ph-name" title={name}>
          {name}
        </span>
        {isDocument && canShowLayout && !renderFailed && (
          <div className="pane-view-toggle" role="group" aria-label="文档展示方式">
            <button
              type="button"
              className={view === 'render' ? 'active' : ''}
              onClick={() => setView('render')}
              title="按原始排版展示"
            >
              原貌
            </button>
            <button
              type="button"
              className={view === 'text' ? 'active' : ''}
              onClick={() => setView('text')}
              title="按提取的文本展示（差异高亮更精确）"
            >
              文本
            </button>
          </div>
        )}
        {isDocument && canShowLayout && !renderFailed && view === 'render' && (
          <ZoomControl value={zoom} onChange={onZoomChange} />
        )}
      </header>
      <div className="pane-body" ref={bodyRef} onScroll={onScroll} tabIndex={0} role="region">
        {status === 'loading' ? (
          <Skeleton />
        ) : isDocument && canShowLayout && view === 'render' ? (
          <DocxPane
            fileId={preview.file_id}
            preview={preview}
            changes={changes}
            selectedChange={selectedChange}
            onSelect={onSelect}
            side={side}
            zoom={zoom}
            onFallback={fallbackToText}
          />
        ) : isDocument ? (
          <DocumentPane
            preview={preview}
            changes={changes}
            selectedChange={selectedChange}
            onSelect={onSelect}
            side={side}
          />
        ) : isSpreadsheet ? (
          <SpreadsheetPane
            preview={preview}
            changes={changes}
            selectedChange={selectedChange}
            onSelect={onSelect}
            side={side}
          />
        ) : (
          <div className="history-empty">
            {status === 'error' ? '预览加载失败' : '暂无可预览的内容'}
          </div>
        )}
      </div>
    </section>
  )
}

function ZoomControl({ value, onChange }) {
  const step = (delta) => {
    const next = Math.round((value + delta) * 10) / 10
    onChange(Math.min(2, Math.max(0.5, next)))
  }
  return (
    <div className="pane-zoom" role="group" aria-label="文档缩放">
      <button type="button" onClick={() => step(-0.1)} title="缩小" aria-label="缩小">
        －
      </button>
      <button
        type="button"
        className="pane-zoom-value"
        onClick={() => onChange(1)}
        title="复位到 100%"
      >
        {Math.round(value * 100)}%
      </button>
      <button type="button" onClick={() => step(0.1)} title="放大" aria-label="放大">
        ＋
      </button>
    </div>
  )
}

function Skeleton() {
  return (
    <div className="skeleton" aria-hidden="true">
      <div className="skeleton-line" style={{ width: '72%' }} />
      <div className="skeleton-line" style={{ width: '94%' }} />
      <div className="skeleton-line" style={{ width: '58%' }} />
      <div className="skeleton-line" style={{ width: '86%' }} />
      <div className="skeleton-line" style={{ width: '40%' }} />
    </div>
  )
}

function findSide(result, side, changeId) {
  const change = result?.changes?.find((item) => item.change_id === changeId)
  if (!change) return null
  return side === 'a' ? change.document_a : change.document_b
}

function scrollToNode(container, side) {
  if (!container || !side) return
  const location = side.location || {}
  const anchors = [location.block_id, location.extra?.table_id, side.node_id].filter(Boolean)
  for (const anchor of anchors) {
    const target =
      container.querySelector(`[data-node-id="${CSS.escape(anchor)}"]`) ||
      container.querySelector(`[data-table-id="${CSS.escape(anchor)}"]`)
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
  }
}
