/**
 * Grid model of a rendered Word table.
 *
 * The backend compares tables on Word's **grid**: every row spans the same
 * number of grid columns, a horizontally merged cell covers several of them
 * and a vertically merged cell continues into the rows below.  The DOM only
 * has one `<td>` per `<w:tc>`, so "column 3 of row 2" in the backend is not
 * the third `<td>` of that row - the mapping has to be rebuilt from the
 * rendered table.
 *
 * `docx-preview` renders:
 * - `w:gridSpan` as `<td colspan="n">` (one cell, several grid columns);
 * - `w:vMerge` as a hidden `<td style="display:none">` in the continuation
 *   rows, while the cell that carries the text gets the `rowspan`;
 * - `w:gridBefore` / `w:gridAfter` as empty, borderless placeholder cells.
 */

/** A placeholder cell is empty and borderless - only at the row edges. */
function isGridPlaceholder(cell, index, count) {
  if (index !== 0 && index !== count - 1) return false
  if (cell.textContent.trim()) return false
  const border = cell.style?.border
  return border === 'none' || border === 'none none none none'
}

/**
 * Builds the grid of a rendered table: one entry per grid column and row.
 *
 * Each entry points at the `<td>` that covers that grid position.
 */
export function buildTableGrid(table) {
  const rows = []
  Array.from(table.rows).forEach((row) => {
    const cells = Array.from(row.cells)
    const entries = []
    let column = 0
    cells.forEach((cell, index) => {
      // The placeholder cells are not part of the grid the backend compares.
      if (isGridPlaceholder(cell, index, cells.length)) return
      const span = Math.max(1, cell.colSpan || 1)
      const hidden = cell.style?.display === 'none'
      for (let offset = 0; offset < span; offset += 1) {
        entries.push({ column: column + offset, cell, hidden })
      }
      column += span
    })
    rows.push(entries)
  })
  return { rows }
}

/** The entry of a grid position, or `null` when the row is unknown. */
export function gridEntry(grid, rowIndex, columnIndex) {
  const entries = grid.rows[rowIndex]
  if (!entries) return null
  return entries.find((entry) => entry.column === columnIndex) || null
}

/**
 * The entry that actually carries the text.
 *
 * A vertically merged cell continues into the rows below - the DOM keeps a
 * hidden `<td>` there, but the content lives in the first row of the merge,
 * which is also the row the backend reports the change on.
 */
export function originEntry(grid, rowIndex, columnIndex) {
  let row = rowIndex
  let entry = gridEntry(grid, row, columnIndex)
  while (entry?.hidden && row > 0) {
    row -= 1
    entry = gridEntry(grid, row, columnIndex)
  }
  return entry
}

/** The visible cells of a row (merged cells only once, continuations resolved). */
export function rowCells(grid, rowIndex) {
  const entries = grid.rows[rowIndex] || []
  const seen = new Set()
  const cells = []
  entries.forEach((entry) => {
    const target = entry.hidden ? originEntry(grid, rowIndex, entry.column) : entry
    const cell = target?.cell || entry.cell
    if (seen.has(cell)) return
    seen.add(cell)
    cells.push(cell)
  })
  return cells
}
