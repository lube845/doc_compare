/**
 * Verifies that a difference inside a merged cell is highlighted on the
 * right cell.
 *
 * Two documents with the same merged-cell table are compared through the
 * running backend; the changed cell is then located in the rendered DOM with
 * the same helpers the result page uses.
 *
 *   DOCX_A=old.docx DOCX_B=new.docx node scripts/verify-table-merge.mjs
 */

import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
  pretendToBeVisual: true,
})

global.window = dom.window
global.document = dom.window.document
global.NodeFilter = dom.window.NodeFilter
global.DOMParser = dom.window.DOMParser
global.XMLSerializer = dom.window.XMLSerializer
global.HTMLElement = dom.window.HTMLElement
global.Node = dom.window.Node
global.Range = dom.window.Range

const { renderAsync } = await import('docx-preview')
const { anchorBlocks } = await import('../src/lib/docxAnchor.js')
const { buildTableGrid, originEntry, rowCells } = await import('../src/lib/tableGrid.js')

const BASE = process.env.BASE || 'http://127.0.0.1:8000'

async function upload(path, filename) {
  const { readFile } = await import('node:fs/promises')
  const form = new FormData()
  form.append('file', new Blob([await readFile(path)]), filename)
  const response = await fetch(`${BASE}/api/files/upload`, { method: 'POST', body: form })
  if (!response.ok) throw new Error(`upload failed: ${response.status} ${await response.text()}`)
  return (await response.json()).file_id
}

async function preview(fileId) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const response = await fetch(`${BASE}/api/files/${fileId}/preview`)
    if (response.ok) return response.json()
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error('preview never became ready')
}

async function compare(fileA, fileB) {
  const response = await fetch(`${BASE}/api/comparisons?wait=true`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_a_id: fileA, file_b_id: fileB }),
  })
  if (!response.ok) throw new Error(`comparison failed: ${response.status} ${await response.text()}`)
  const { comparison_id: comparisonId } = await response.json()
  const result = await (await fetch(`${BASE}/api/comparisons/${comparisonId}/result`)).json()
  return result
}

const pathA = process.env.DOCX_A
const pathB = process.env.DOCX_B
if (!pathA || !pathB) throw new Error('需要 DOCX_A 与 DOCX_B 环境变量')

const fileA = await upload(pathA, 'a.docx')
const fileB = await upload(pathB, 'b.docx')

const previewB = await preview(fileB)
const result = await compare(fileA, fileB)

const cellChanges = result.changes.filter((change) => change.category === 'table_cell')
console.log('table cell changes:', cellChanges.length)
for (const change of cellChanges) {
  console.log(
    '  ',
    change.primary_type,
    `row=${change.document_b?.location?.row_index}`,
    `col=${change.document_b?.location?.column_index}`,
    JSON.stringify(change.document_b?.text),
  )
}
if (cellChanges.length !== 1) {
  throw new Error(`expected exactly one table cell change, got ${cellChanges.length}`)
}

// -- render the changed document -------------------------------------------
const host = document.getElementById('host')
const renderResponse = await fetch(`${BASE}/api/files/${fileB}/render`)
if (!renderResponse.ok) throw new Error(`render failed: ${renderResponse.status}`)
await renderAsync(Buffer.from(await renderResponse.arrayBuffer()), host, null, {
  className: 'docx',
  inWrapper: false,
  breakPages: true,
  ignoreWidth: true,
  ignoreHeight: true,
  renderHeaders: true,
  renderFooters: true,
  useBase64URL: true,
  experimental: true,
})

anchorBlocks(host, previewB.blocks)
const table = host.querySelector('[data-table-id]')
if (!table) throw new Error('the table was not anchored')

const grid = buildTableGrid(table)
console.log(
  'grid rows:',
  grid.rows.map((row) => row.map((entry) => `${entry.column}:${entry.cell.textContent.trim()}`).join(' | ')).join('  //  '),
)

const change = cellChanges[0]
const location = change.document_b.location
const entry = originEntry(grid, location.row_index, location.column_index)
if (!entry) throw new Error('the changed cell could not be resolved')
const cell = entry.cell
console.log('resolved cell:', JSON.stringify(cell.textContent.trim()))
console.log('resolved cell is visible:', cell.style.display !== 'none')

if (!cell.textContent.includes(change.document_b.text.trim())) {
  throw new Error(
    `the highlight landed on "${cell.textContent.trim()}" instead of "${change.document_b.text.trim()}"`,
  )
}
if (cell.style.display === 'none') {
  throw new Error('the highlight landed on a hidden vertical-merge continuation')
}

// The rows of the table must not lose their cells: a merged cell is one cell.
const visibleCells = rowCells(grid, 0)
console.log('cells of row 0:', visibleCells.length)
if (visibleCells.length !== 2) {
  throw new Error(`expected 2 visible cells in row 0, got ${visibleCells.length}`)
}

console.log('OK')
