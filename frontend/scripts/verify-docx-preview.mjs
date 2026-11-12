/**
 * Verifies the original-layout preview against real docx-preview output.
 *
 * The document is rendered in jsdom, the parsed blocks are fetched from the
 * running backend, and the anchoring / highlighting helpers are applied to
 * the rendered DOM.
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
const { anchorBlocks, highlightText, clearHighlights, collectCandidates } = await import(
  '../src/lib/docxAnchor.js'
)

const BASE = 'http://127.0.0.1:8000'

// ---------------------------------------------------------------- upload
// The sample document is passed in as DOCX_PATH or DOCX_BASE64; it is uploaded
// to the running backend so the real preview blocks are used.
const docxBase64 = process.env.DOCX_BASE64
const docxPath = process.env.DOCX_PATH
if (!docxBase64 && !docxPath) {
  throw new Error('需要 DOCX_PATH 或 DOCX_BASE64 环境变量')
}
const buffer = docxPath
  ? await (await import('node:fs/promises')).readFile(docxPath)
  : Buffer.from(docxBase64, 'base64')

const form = new FormData()
form.append('file', new Blob([buffer]), 'check.docx')
const uploadResponse = await fetch(`${BASE}/api/files/upload`, { method: 'POST', body: form })
if (!uploadResponse.ok) throw new Error(`upload failed: ${uploadResponse.status} ${await uploadResponse.text()}`)
const { file_id: fileId } = await uploadResponse.json()

let preview = null
for (let attempt = 0; attempt < 60; attempt += 1) {
  const response = await fetch(`${BASE}/api/files/${fileId}/preview`)
  if (response.ok) {
    preview = await response.json()
    break
  }
  await new Promise((resolve) => setTimeout(resolve, 1000))
}
if (!preview) throw new Error('preview never became ready')
console.log('blocks:', preview.blocks.map((b) => `${b.block_id}:${b.type}`).join(' '))

// ---------------------------------------------------------------- render
const host = document.getElementById('host')
const renderResponse = await fetch(`${BASE}/api/files/${fileId}/render`)
if (!renderResponse.ok) throw new Error(`render failed: ${renderResponse.status}`)
const renderBytes = Buffer.from(await renderResponse.arrayBuffer())

await renderAsync(renderBytes, host, null, {
  className: 'docx',
  inWrapper: false,
  breakPages: true,
  ignoreWidth: true,
  ignoreHeight: true,
  renderHeaders: true,
  renderFooters: true,
  renderFootnotes: true,
  renderEndnotes: true,
  useBase64URL: true,
  experimental: true,
})

console.log('sections:', host.querySelectorAll('section').length)
console.log('articles:', host.querySelectorAll('article').length)
console.log('paragraphs:', host.querySelectorAll('p').length)
console.log('tables:', host.querySelectorAll('table').length)
console.log('candidates:', collectCandidates(host).length)

// ---------------------------------------------------------------- anchor
anchorBlocks(host, preview.blocks)
const anchored = Array.from(host.querySelectorAll('[data-node-id], [data-table-id]'))
console.log('anchored:', anchored.map((el) => el.dataset.nodeId || el.dataset.tableId).join(' '))
if (anchored.length !== preview.blocks.length) {
  throw new Error(`only ${anchored.length}/${preview.blocks.length} blocks anchored`)
}

// ---------------------------------------------------------------- highlight
const target = host.querySelector('[data-node-id="b_1"]')
if (!target) throw new Error('b_1 not anchored')
highlightText(target, ['100万元'], 'del')
const marks = host.querySelectorAll('mark.diff-mark')
console.log('marks:', Array.from(marks).map((m) => m.textContent).join(' | '))
if (!marks.length) throw new Error('no inline mark produced')
if (marks[0].textContent !== '100万元') throw new Error(`unexpected mark text: ${marks[0].textContent}`)

// a needle that is not part of the paragraph must not create a mark
clearHighlights(host)
highlightText(target, ['不存在的文本'], 'del')
if (host.querySelectorAll('mark.diff-mark').length) {
  throw new Error('unknown needle produced a mark')
}

// ---------------------------------------------------------------- scroll sync
// The two panes are aligned by page; jsdom has no layout, so the geometry is
// stubbed here.
const { alignScroll } = await import('../src/lib/panes.js')

function fakePane(pages, scrollTop) {
  // The rectangles are viewport relative: scrolling moves them up.
  const stubs = pages.map(([top, height]) => ({
    getBoundingClientRect: () => ({ top: top - scrollTop, height, bottom: top - scrollTop + height }),
  }))
  return {
    scrollTop,
    querySelectorAll: () => stubs,
    getBoundingClientRect: () => ({ top: 0, height: 0 }),
  }
}

const sourcePane = fakePane([[0, 500], [500, 500], [1000, 500]], 700)
const targetPane = fakePane([[0, 400], [400, 400], [800, 400], [1200, 400]], 0)
alignScroll(sourcePane, targetPane)
// source is on page 1 at 40 % -> target page 1 at 40 % of 400 px
if (targetPane.scrollTop !== 400 + 0.4 * 400) {
  throw new Error(`unexpected target scrollTop: ${targetPane.scrollTop}`)
}

// A pane without pages (text view) falls back to proportional scrolling.
const plainSource = { scrollTop: 250, querySelectorAll: () => [], getBoundingClientRect: () => ({ top: 0 }) }
const plainTarget = { scrollTop: 0, querySelectorAll: () => [], getBoundingClientRect: () => ({ top: 0 }) }
alignScroll(plainSource, plainTarget)
if (plainTarget.scrollTop !== 250) {
  throw new Error(`unexpected fallback scrollTop: ${plainTarget.scrollTop}`)
}

console.log('scroll sync: OK')
console.log('OK')