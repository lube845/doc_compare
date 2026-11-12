/**
 * Scroll alignment between the two result panes.
 *
 * The document panes render the original Word layout, so a page is a
 * `<section class="docx">`.  Copying `scrollTop` between the panes drifts
 * as soon as the two revisions have different page heights; aligning by page
 * index keeps both sides on the same page and preserves the position inside
 * it.  When a pane shows the text view (no pages), the panes fall back to
 * proportional scrolling.
 */

function pageGeometry(container) {
  const pages = container.querySelectorAll('section.docx')
  if (!pages.length) return null
  const base = container.getBoundingClientRect()
  return Array.from(pages).map((page) => {
    const rect = page.getBoundingClientRect()
    return {
      top: rect.top - base.top + container.scrollTop,
      height: rect.height,
    }
  })
}

function locatePage(container, pages) {
  const scrollTop = container.scrollTop
  let index = 0
  for (let i = 0; i < pages.length; i += 1) {
    if (pages[i].top <= scrollTop + 4) index = i
    else break
  }
  const page = pages[index]
  const ratio = page.height > 0 ? (scrollTop - page.top) / page.height : 0
  return { index, ratio: Math.min(1, Math.max(0, ratio)) }
}

/** Scroll `target` so it shows the same page (and position) as `source`. */
export function alignScroll(source, target) {
  const sourcePages = pageGeometry(source)
  const targetPages = pageGeometry(target)
  if (!sourcePages?.length || !targetPages?.length) {
    target.scrollTop = source.scrollTop
    return
  }
  const { index, ratio } = locatePage(source, sourcePages)
  const page = targetPages[Math.min(index, targetPages.length - 1)]
  target.scrollTop = Math.max(0, page.top + ratio * page.height)
}
