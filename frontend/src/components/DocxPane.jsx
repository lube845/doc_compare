import { useEffect, useMemo, useRef, useState } from 'react'
import { getRenderBlob } from '../api'
import {
  anchorBlocks,
  clearHighlights,
  highlightText,
  markBlock,
  markImages,
} from '../lib/docxAnchor'
import { collectNeedles } from '../lib/diffText'
import { buildTableGrid, originEntry, rowCells } from '../lib/tableGrid'

const EMPTY = []

/**
 * The element a change points at.
 *
 * Paragraph changes point at the paragraph itself; a table cell change points
 * at the cell - merged cells included, where the DOM position differs from
 * the grid position the backend reports.
 */
function resolveChangeTarget(anchor, location) {
  if (anchor.tagName !== 'TABLE') return anchor
  if (location?.row_index == null) return anchor
  const grid = buildTableGrid(anchor)
  if (location.column_index == null) {
    return rowCells(grid, location.row_index)[0] || null
  }
  return originEntry(grid, location.row_index, location.column_index)?.cell || null
}

/**
 * Shows a Word document in its original layout (fonts, bold/italic, tables,
 * pictures, alignment) by rendering the DOCX container in the browser with
 * `docx-preview`.
 *
 * The backend diff engine works on parsed blocks (`b_n` / `t_n`).  The
 * rendered DOM is therefore matched back to the parsed blocks in document
 * order (see `lib/docxAnchor`) and the differences are painted on top of the
 * rendered document; the document itself is never modified.
 */
export default function DocxPane({
  fileId,
  preview,
  changes,
  selectedChange,
  onSelect,
  side,
  zoom = 1,
  onFallback,
}) {
  const hostRef = useRef(null)
  const [status, setStatus] = useState('loading')

  // Block id / table id -> the changes touching it on this side.
  const blockChanges = useMemo(() => {
    const map = new Map()
    const push = (key, change) => {
      if (!key) return
      const list = map.get(key)
      if (list) list.push(change)
      else map.set(key, [change])
    }
    changes.forEach((change) => {
      const location = side === 'a' ? change.document_a?.location : change.document_b?.location
      push(location?.block_id, change)
      const tableId = location?.extra?.table_id
      if (tableId && tableId !== location?.block_id) push(tableId, change)
    })
    return map
  }, [changes, side])

  // Load and render the document once per file.
  useEffect(() => {
    const host = hostRef.current
    if (!host || !fileId) return undefined
    let cancelled = false
    const controller = new AbortController()
    setStatus('loading')

    ;(async () => {
      // Loaded on demand: docx-preview is only needed on the result page.
      const { renderAsync } = await import('docx-preview')
      const blob = await getRenderBlob(fileId, controller.signal)
      if (cancelled) return
      await renderAsync(blob, host, null, {
        className: 'docx',
        inWrapper: false,
        breakPages: true,
        // The two result panes are narrower than a Word page; fitting the
        // width keeps the document readable without a horizontal scrollbar.
        ignoreWidth: true,
        ignoreHeight: true,
        renderHeaders: true,
        renderFooters: true,
        renderFootnotes: true,
        renderEndnotes: true,
        useBase64URL: true,
        experimental: true,
      })
      if (cancelled) return
      anchorBlocks(host, preview?.blocks || [])
      setStatus('ready')
    })().catch((error) => {
      if (cancelled || error?.name === 'AbortError') return
      console.warn('docx preview unavailable:', error)
      setStatus('failed')
      onFallback?.()
    })

    return () => {
      cancelled = true
      controller.abort()
      host.innerHTML = ''
    }
  }, [fileId, preview, onFallback])

  // Paint the differences.  This does not depend on the selected change so
  // selecting one of many differences does not re-wrap every text node.
  useEffect(() => {
    const host = hostRef.current
    if (!host || status !== 'ready') return
    clearHighlights(host)

    host.querySelectorAll('[data-node-id]').forEach((element) => {
      const related = blockChanges.get(element.dataset.nodeId) || EMPTY
      if (!related.length) return
      markBlock(element, related)
      markImages(element, related)
      highlightText(element, collectNeedles(related, side), side === 'a' ? 'del' : 'ins')
    })

    host.querySelectorAll('[data-table-id]').forEach((table) => {
      const related = blockChanges.get(table.dataset.tableId) || EMPTY
      if (!related.length) return
      // The backend addresses cells by grid position; the DOM only has one
      // <td> per cell, so the grid has to be rebuilt for merged tables.
      const grid = buildTableGrid(table)
      related.forEach((change) => {
        const location = side === 'a' ? change.document_a?.location : change.document_b?.location
        const type = change.primary_type
        if (location?.row_index == null) {
          table.classList.add('docx-table-mark', `mark-${type}`)
          return
        }
        const cells =
          location.column_index == null
            ? rowCells(grid, location.row_index)
            : [originEntry(grid, location.row_index, location.column_index)?.cell].filter(Boolean)
        if (!cells.length) return
        cells.forEach((cell) => {
          cell.classList.add('cell-mark', `mark-${type}`)
          markImages(cell, [change])
          highlightText(cell, collectNeedles([change], side), side === 'a' ? 'del' : 'ins')
        })
      })
    })
  }, [status, blockChanges, side])

  // Selected change: only the outline moves, the text marks stay untouched.
  useEffect(() => {
    const host = hostRef.current
    if (!host || status !== 'ready') return
    host.querySelectorAll('.selected').forEach((element) => element.classList.remove('selected'))
    if (!selectedChange) return

    const change = changes.find((item) => item.change_id === selectedChange)
    const location = side === 'a' ? change?.document_a?.location : change?.document_b?.location
    const key = location?.block_id || location?.extra?.table_id
    if (!key) return
    const anchor = host.querySelector(
      `[data-node-id="${CSS.escape(key)}"], [data-table-id="${CSS.escape(key)}"]`,
    )
    if (!anchor) return
    // A table cell change selects the cell itself, not the whole table.
    const target = resolveChangeTarget(anchor, location) || anchor
    target.classList.add('selected')
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [status, selectedChange, changes, side])

  const handleClick = (event) => {
    const anchor = event.target.closest?.('[data-node-id], [data-table-id]')
    if (!anchor) return
    const key = anchor.dataset.nodeId || anchor.dataset.tableId
    const related = blockChanges.get(key) || EMPTY
    if (!related.length) return
    const current = related.find((change) => change.change_id === selectedChange)
    onSelect?.(current || related[0])
  }

  return (
    <div className="docx-pane">
      {status === 'loading' && (
        <div className="docx-status" aria-live="polite">
          正在生成原貌预览…
        </div>
      )}
      {status === 'failed' && (
        <div className="docx-status">原貌预览不可用，正在切换为文本视图…</div>
      )}
      <div
        ref={hostRef}
        className="docx-host"
        style={{ zoom }}
        onClick={handleClick}
      />
    </div>
  )
}
