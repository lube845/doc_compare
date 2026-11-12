/**
 * Mapping between the rendered DOCX (docx-preview) and the parsed blocks
 * the backend diff engine works on.
 *
 * The parser emits one block per top-level paragraph (`b_n`) or table
 * (`t_n`); docx-preview renders the same document as DOM.  The rendered
 * nodes are matched back to the blocks in document order and the block ids
 * are written as `data-node-id` / `data-table-id`, which is the anchor
 * `ResultPage.scrollToNode` already looks for.
 *
 * The helpers work on plain DOM nodes so they can be exercised without a
 * React tree.
 */

import { buildSegments } from './diffText.js'

const MARK_CLASSES = ['mark-added', 'mark-deleted', 'mark-modified', 'mark-moved']

/**
 * Collects the rendered body paragraphs and tables in document order.
 *
 * Paragraphs inside table cells and header/footer content are skipped: the
 * parser emits one block per top-level paragraph/table, so those nodes have
 * no counterpart and would shift the mapping.
 */
export function collectCandidates(root) {
  const candidates = []
  const visit = (node) => {
    for (const child of Array.from(node.children)) {
      const tag = child.tagName
      if (tag === 'P' || tag === 'TABLE') {
        candidates.push(child)
        continue // do not descend into paragraphs or table cells
      }
      visit(child)
    }
  }
  root.querySelectorAll('article').forEach(visit)
  return candidates
}

export function normaliseText(value) {
  return (value || '').replace(/\s+/g, '')
}

/**
 * Writes the block ids onto the rendered nodes.
 *
 * The renderer keeps empty paragraphs while the parser drops them, so the
 * expected node may be a few positions ahead; a bounded look-ahead keeps the
 * matching linear without risking a match far down the document.
 */
export function anchorBlocks(root, blocks) {
  const candidates = collectCandidates(root)
  let cursor = 0

  for (const block of blocks) {
    if (block.type === 'table') {
      const index = nextMatching(candidates, cursor, (element) => element.tagName === 'TABLE')
      if (index === -1) continue
      candidates[index].dataset.tableId = block.table?.table_id || block.block_id
      cursor = index + 1
      continue
    }

    const wanted = normaliseText(block.text)
    let index = -1
    if (wanted) {
      index = nextMatching(
        candidates,
        cursor,
        (element) => element.tagName === 'P' && normaliseText(element.textContent) === wanted,
        80,
      )
    }
    if (index === -1 && block.images?.length) {
      // A paragraph that only carries a picture has no text in the rendered
      // DOM (and the preview uses a placeholder text), so match the next
      // paragraph that contains a picture instead.
      index = nextMatching(
        candidates,
        cursor,
        (element) => element.tagName === 'P' && element.querySelector('img'),
        80,
      )
    }
    if (index === -1) continue
    candidates[index].dataset.nodeId = block.block_id
    cursor = index + 1
  }

  return candidates
}

function nextMatching(candidates, from, predicate, limit = candidates.length) {
  const end = Math.min(candidates.length, from + limit)
  for (let index = from; index < end; index += 1) {
    if (predicate(candidates[index])) return index
  }
  return -1
}

export function markBlock(element, related) {
  const types = new Set(related.map((change) => change.primary_type))
  types.forEach((type) => element.classList.add(`mark-${type}`))
  element.classList.add('docx-anchor')
}

/** Marks the pictures of an anchored block/cell when their bytes changed. */
export function markImages(element, related) {
  const images = element.querySelectorAll('img')
  if (!images.length) return
  const imageChanges = related.filter(
    (change) => change.category === 'image' || change.category === 'table_image',
  )
  if (!imageChanges.length) return
  const types = new Set(imageChanges.map((change) => change.primary_type))
  images.forEach((image) => {
    image.classList.add('docx-image-mark')
    types.forEach((type) => image.classList.add(`mark-${type}`))
  })
}

/** Removes the marks of the previous run so the next one starts clean. */
export function clearHighlights(root) {
  root
    .querySelectorAll(
      `.${MARK_CLASSES.join(',.')}, .docx-anchor, .cell-mark, .docx-table-mark, .docx-image-mark, .selected`,
    )
    .forEach((element) => {
      element.classList.remove(
        ...MARK_CLASSES,
        'docx-anchor',
        'cell-mark',
        'docx-table-mark',
        'docx-image-mark',
        'selected',
      )
    })
  // Remove the spans added by the previous run and merge the text nodes back,
  // otherwise the next run would wrap inside the previous marks.
  root.querySelectorAll('mark.diff-mark').forEach((mark) => {
    mark.replaceWith(...Array.from(mark.childNodes))
  })
  root.querySelectorAll('[data-node-id], [data-table-id]').forEach((element) => element.normalize())
}

/**
 * Wraps the changed substrings inside ``element`` with `mark.diff-mark`.
 *
 * The needles come from the backend fragments; their positions are found with
 * the same sequential search the text preview uses, then a DOM Range maps the
 * character offsets back onto the existing text nodes, so inline formatting
 * (bold, links, ...) is preserved.
 */
export function highlightText(element, needles, kind) {
  if (!needles?.length) return

  const textNodes = []
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.parentElement?.closest('mark.diff-mark')
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  })
  while (walker.nextNode()) textNodes.push(walker.currentNode)

  const fullText = textNodes.map((node) => node.nodeValue).join('')
  if (!fullText) return

  const segments = buildSegments(fullText, needles, kind)
  if (!segments.some((segment) => segment.kind !== 'plain')) return

  // Character offset -> text node + local offset.
  const ends = []
  let total = 0
  textNodes.forEach((node) => {
    total += node.nodeValue.length
    ends.push(total)
  })

  const locate = (offset, isEnd) => {
    for (let index = 0; index < textNodes.length; index += 1) {
      const end = ends[index]
      const start = index === 0 ? 0 : ends[index - 1]
      if (offset < end || (isEnd && offset === end)) {
        return { node: textNodes[index], offset: offset - start }
      }
    }
    return null
  }

  const ranges = []
  let offset = 0
  segments.forEach((segment) => {
    if (segment.kind !== 'plain') {
      const start = locate(offset, false)
      const end = locate(offset + segment.text.length, true)
      if (start && end) {
        const range = document.createRange()
        range.setStart(start.node, start.offset)
        range.setEnd(end.node, end.offset)
        ranges.push(range)
      }
    }
    offset += segment.text.length
  })
  if (!ranges.length) return

  // Apply from the end of the paragraph towards the start so the offsets of
  // the remaining ranges stay valid while the DOM is split.
  ranges.reverse().forEach((range) => {
    const mark = document.createElement('mark')
    mark.className = `diff-mark diff-${kind}`
    try {
      range.surroundContents(mark)
    } catch {
      // The range crosses inline elements (e.g. a bold run); wrapping the
      // whole range keeps the existing nodes inside the mark.
      try {
        mark.appendChild(range.extractContents())
        range.insertNode(mark)
      } catch {
        /* skip this fragment rather than corrupt the rendered document */
      }
    }
  })
}
