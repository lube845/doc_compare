/**
 * Verifies that a replaced picture is reported and highlighted in the
 * original-layout view.
 *
 *   DOCX_A=old.docx DOCX_B=new.docx node scripts/verify-image.mjs
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
global.FileReader = dom.window.FileReader

const { renderAsync } = await import('docx-preview')
const { anchorBlocks, markBlock, markImages } = await import('../src/lib/docxAnchor.js')
const { buildTableGrid, originEntry } = await import('../src/lib/tableGrid.js')

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
  return (await fetch(`${BASE}/api/comparisons/${comparisonId}/result`)).json()
}

const pathA = process.env.DOCX_A
const pathB = process.env.DOCX_B
if (!pathA || !pathB) throw new Error('需要 DOCX_A 与 DOCX_B 环境变量')

const fileA = await upload(pathA, 'a.docx')
const fileB = await upload(pathB, 'b.docx')

const previewB = await preview(fileB)
const images = previewB.blocks.flatMap((block) => [
  ...(block.images || []),
  ...((block.table?.layout || []).flat().flatMap((cell) => cell.images || [])),
])
console.log('preview pictures:', images.length, images)
if (!images.length) throw new Error('图片没有出现在预览数据里')

const result = await compare(fileA, fileB)
console.log('files:', fileA, fileB)
console.log('all changes:', result.changes.map((change) => `${change.category}/${change.primary_type}`))
const imageChanges = result.changes.filter(
  (change) => change.category === 'image' || change.category === 'table_image',
)
console.log('image changes:', imageChanges.length)
for (const change of imageChanges) {
  console.log('  ', change.primary_type, change.summary)
}
if (imageChanges.length !== 1) {
  throw new Error(`expected exactly one image change, got ${imageChanges.length}`)
}
if (!result.notices.some((notice) => notice.includes('图片'))) {
  throw new Error('结果里缺少图片比对的说明')
}
console.log('notice:', result.notices.find((notice) => notice.includes('图片')))

// -- render the changed document and mark it ------------------------------
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
const change = imageChanges[0]
const location = change.document_b.location
const key = location.block_id || location.extra?.table_id
let anchor = host.querySelector(
  `[data-node-id="${key}"], [data-table-id="${key}"]`,
)
if (!anchor) throw new Error(`block ${key} was not anchored`)
let target = anchor
if (anchor.tagName === 'TABLE' && location.row_index != null) {
  const grid = buildTableGrid(anchor)
  const entry = originEntry(grid, location.row_index, location.column_index)
  if (!entry) throw new Error('the changed cell could not be resolved')
  target = entry.cell
}
console.log('anchored block:', key, 'pictures inside:', target.querySelectorAll('img').length)
if (target.querySelectorAll('img').length !== 1) {
  throw new Error('the picture is not inside the anchored block/cell')
}

markBlock(anchor, [change])
markImages(target, [change])
const image = target.querySelector('img')
console.log('picture classes:', image.className)
if (!image.classList.contains('docx-image-mark')) throw new Error('picture was not marked')
if (!image.classList.contains(`mark-${change.primary_type}`)) {
  throw new Error(`picture is not marked as ${change.primary_type}`)
}
// The browser fills `src` from the embedded image; jsdom has no working
// FileReader for Node Blobs, so only the element and its marks are checked.

console.log('OK')
