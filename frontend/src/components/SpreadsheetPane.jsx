import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import useVirtualRows from '../hooks/useVirtualRows'
import InlineDiff from './InlineDiff'

const ESTIMATED_ROW_HEIGHT = 30

/**
 * Renders a workbook as a grid and highlights the cells that changed
 * (PRD 10.3 - spreadsheet view). Rows are virtualised because the preview
 * endpoint returns every cell of every sheet (PRD 16.1 allows 10 000 rows).
 */
function SpreadsheetPane({ preview, changes, selectedChange, onSelect, side }) {
  const [activeSheet, setActiveSheet] = useState(preview.sheets[0]?.name)
  const [rowHeight, setRowHeight] = useState(ESTIMATED_ROW_HEIGHT)
  const [pendingRow, setPendingRow] = useState(null)
  const firstRowRef = useRef(null)
  const previewFileRef = useRef(preview.file_id)

  // A different workbook was loaded — start from its first sheet.
  if (previewFileRef.current !== preview.file_id) {
    previewFileRef.current = preview.file_id
    if (activeSheet !== preview.sheets[0]?.name) setActiveSheet(preview.sheets[0]?.name)
  }

  const sheet = preview.sheets.find((item) => item.name === activeSheet) || preview.sheets[0]
  const totalRows = sheet?.rows.length || 0

  const { containerRef, start, end } = useVirtualRows({
    total: totalRows,
    rowHeight,
    resetKey: sheet?.name,
  })

  // Changes that point at a specific cell, keyed by sheet/row/column.
  const cellChanges = useMemo(() => {
    const map = new Map()
    changes.forEach((change) => {
      const location = side === 'a' ? change.document_a?.location : change.document_b?.location
      const sheetName = location?.sheet_name
      const row = location?.row_index
      const column = location?.column_index
      if (!sheetName || row == null || column == null) return
      const key = `${sheetName}::${row}::${column}`
      const list = map.get(key)
      if (list) list.push(change)
      else map.set(key, [change])
    })
    return map
  }, [changes, side])

  // Row level changes (added / deleted rows) only carry a row index; cell
  // level changes also carry a column and are marked per cell instead.
  const rowChanges = useMemo(() => {
    const map = new Map()
    changes.forEach((change) => {
      const location = side === 'a' ? change.document_a?.location : change.document_b?.location
      if (location?.sheet_name !== sheet?.name) return
      const row = location?.row_index
      if (row == null || location?.column_index != null) return
      const list = map.get(row)
      if (list) list.push(change)
      else map.set(row, [change])
    })
    return map
  }, [changes, side, sheet])

  const sheetCounts = useMemo(() => {
    const counts = new Map()
    changes.forEach((change) => {
      const location = side === 'a' ? change.document_a?.location : change.document_b?.location
      const name = location?.sheet_name
      if (!name) return
      counts.set(name, (counts.get(name) || 0) + 1)
    })
    return counts
  }, [changes, side])

  const columnCount = useMemo(() => {
    let max = 0
    sheet?.rows.forEach((row) => {
      if (row.length > max) max = row.length
    })
    return max
  }, [sheet])

  // Follow the selected difference into the sheet that contains it.
  useEffect(() => {
    if (!selectedChange) return
    const change = changes.find((item) => item.change_id === selectedChange)
    const location = side === 'a' ? change?.document_a?.location : change?.document_b?.location
    const target = location?.sheet_name
    if (!target) return
    if (preview.sheets.some((item) => item.name === target)) setActiveSheet(target)
    if (location.row_index != null) setPendingRow(location.row_index)
  }, [selectedChange, changes, side, preview.sheets])

  // Scroll the target row into view. The row may not be rendered yet (it is
  // virtualised), so the position is computed instead of queried.
  useEffect(() => {
    if (pendingRow == null) return
    const container = containerRef.current
    if (!container) return
    // Measure the row that is actually on screen instead of trusting the
    // cached estimate, so the target lands on the right line.
    const rendered = container.querySelector('.sheet-grid tbody tr:not(.spacer)')
    const height = rendered?.getBoundingClientRect().height || rowHeight
    const top = (pendingRow - 1) * height
    const target = Math.max(0, top - container.clientHeight / 2 + height / 2)
    container.scrollTo({ top: target, behavior: 'smooth' })
    setPendingRow(null)
  }, [pendingRow, activeSheet, rowHeight, containerRef])

  // Keep the virtualisation maths in sync with the real row height.
  useLayoutEffect(() => {
    const row = firstRowRef.current
    if (!row) return
    const height = row.getBoundingClientRect().height
    if (height > 0 && Math.abs(height - rowHeight) > 0.5) setRowHeight(height)
  }, [activeSheet, totalRows, rowHeight])

  if (!sheet) return <div className="empty">暂无可预览的表格</div>

  const handleCellClick = (related) => {
    if (!related.length) return
    const current = related.find((change) => change.change_id === selectedChange)
    onSelect(current || related[0])
  }

  // The virtual window can lag one frame behind a sheet switch.
  const windowStart = Math.min(start, totalRows)
  const windowEnd = Math.min(end, totalRows)
  const visibleRows = sheet.rows.slice(windowStart, windowEnd)
  const columnCountTotal = columnCount + 1
  return (
    <div className="spreadsheet-pane">
      {preview.sheets.length > 1 && (
        <div className="sheet-tabs" role="tablist" aria-label="工作表">
          {preview.sheets.map((item) => {
            const count = sheetCounts.get(item.name) || 0
            return (
              <button
                key={item.name}
                role="tab"
                aria-selected={item.name === sheet.name}
                className={item.name === sheet.name ? 'active' : ''}
                onClick={() => {
                  setActiveSheet(item.name)
                  setPendingRow(null)
                  if (containerRef.current) containerRef.current.scrollTop = 0
                }}
              >
                {item.name}
                {item.hidden ? '（隐藏）' : ''}
                {count > 0 && <span className="tab-badge">{count}</span>}
              </button>
            )
          })}
        </div>
      )}

      <div
        className="grid-scroll"
        ref={containerRef}
        tabIndex={0}
        role="region"
        aria-label={`工作表 ${sheet.name}`}
      >
        <table className="sheet-grid">
          <thead>
            <tr>
              <th className="row-head corner" scope="col">
                {'#'}
              </th>
              {Array.from({ length: columnCount }, (_, index) => (
                <th className="col-head" key={index} scope="col">
                  {columnLetter(index)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {windowStart > 0 && (
              <tr className="spacer" style={{ height: windowStart * rowHeight }}>
                <td colSpan={columnCountTotal} />
              </tr>
            )}
            {visibleRows.map((row, offset) => {
              const rowIndex = windowStart + offset
              const rowNumber = rowIndex + 1
              const rowRelated = rowChanges.get(rowNumber) || []
              const selectedRow = rowRelated.some(
                (change) => change.change_id === selectedChange,
              )
              const rowClass = [
                rowRelated.length ? 'row-marked' : '',
                selectedRow ? 'row-selected' : '',
                rowRelated.length ? `row-${rowRelated[0].primary_type}` : '',
              ]
                .filter(Boolean)
                .join(' ')

              return (
                <tr key={rowIndex} className={rowClass} ref={rowIndex === windowStart ? firstRowRef : null}>
                  <th className="row-head" scope="row">
                    {rowNumber}
                  </th>
                  {row.map((value, columnIndex) => {
                    const cellRelated =
                      cellChanges.get(`${sheet.name}::${rowNumber}::${columnIndex}`) || []
                    const types = new Set(cellRelated.map((item) => item.primary_type))
                    const className = [
                      types.size ? `cell-mark mark-${[...types].join(' ')}` : '',
                      cellRelated.some((item) => item.change_id === selectedChange)
                        ? 'cell-selected'
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' ')
                    const cellId = sheet.cell_ids?.[rowIndex]?.[columnIndex]
                    const formula = sheet.formulas?.[rowIndex]?.[columnIndex]
                    const formulaRelated = cellRelated.filter(
                      (item) => item.category === 'formula',
                    )
                    const hasFormula = Boolean(formula)
                    const text = value ?? ''
                    let cellTitle = String(text)
                    if (formulaRelated.length) {
                      const a = formulaRelated[0].details?.formula_a || '（无）'
                      const b = formulaRelated[0].details?.formula_b || '（无）'
                      cellTitle = `公式不一致\nA：${a}\nB：${b}\n显示值：${text}`
                    } else if (hasFormula) {
                      cellTitle = `公式：${formula}\n显示值：${text}`
                    }
                    return (
                      <td
                        key={columnIndex}
                        className={[
                          className,
                          hasFormula ? 'cell-has-formula' : '',
                          formulaRelated.length ? 'cell-formula-changed' : '',
                        ]
                          .filter(Boolean)
                          .join(' ')}
                        data-node-id={cellId}
                        title={cellTitle}
                        onClick={() => handleCellClick(cellRelated)}
                      >
                        {cellRelated.length ? (
                          <InlineDiff text={String(text)} changes={cellRelated} side={side} />
                        ) : (
                          text
                        )}
                        {hasFormula && (
                          <span
                            className={
                              formulaRelated.length ? 'cell-fx cell-fx-changed' : 'cell-fx'
                            }
                            aria-hidden="true"
                          >
                            fx
                          </span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
            {windowEnd < totalRows && (
              <tr className="spacer" style={{ height: (totalRows - windowEnd) * rowHeight }}>
                <td colSpan={columnCountTotal} />
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalRows > 0 && (
        <div className="grid-footer">
          共 {totalRows} 行 · {columnCount} 列
          {rowChangeCount(rowChanges) > 0 && ` · ${rowChangeCount(rowChanges)} 处行级差异`}
        </div>
      )}
    </div>
  )
}

function rowChangeCount(rowChanges) {
  let total = 0
  rowChanges.forEach((list) => {
    total += list.length
  })
  return total
}

function columnLetter(index) {
  let value = index
  let letters = ''
  do {
    letters = String.fromCharCode(65 + (value % 26)) + letters
    value = Math.floor(value / 26) - 1
  } while (value >= 0)
  return letters
}

export default memo(SpreadsheetPane)
