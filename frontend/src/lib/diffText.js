/**
 * Turns the word-level diff fragments produced by the backend
 * (`details.fragments`, see `document_diff.diff_block_text`) into inline
 * highlight segments, so the panes can mark the exact characters that
 * changed instead of colouring whole blocks (PRD 10.3 "差异高亮").
 */

const ELLIPSIS = /…|\.\.\./

/**
 * Collects the substrings that differ on one side of a change.
 *
 * The backend emits one fragment per Myers hunk: `delete` hunks carry the
 * removed text in `a`, `insert` hunks carry the added text in `b`. The
 * merged `highlight` field is used as a fallback and may join several ranges
 * with an ellipsis.
 */
export function collectNeedles(changes, side) {
  const needles = []
  const add = (value) => {
    if (typeof value !== 'string') return
    // Keep the fragment verbatim (no trimming) so it still matches the source
    // text exactly; only whitespace-only fragments are dropped.
    if (!value.trim()) return
    needles.push(value)
  }

  const list = Array.isArray(changes) ? changes : changes ? [changes] : []
  list.forEach((change) => {
    const fragments = change?.details?.fragments
    if (Array.isArray(fragments)) {
      fragments.forEach((fragment) => {
        if (side === 'a' && fragment?.type !== 'insert') add(fragment?.a)
        if (side === 'b' && fragment?.type !== 'delete') add(fragment?.b)
      })
    }
    const sideData = side === 'a' ? change?.document_a : change?.document_b
    const highlight = sideData?.highlight
    if (typeof highlight === 'string') {
      highlight.split(ELLIPSIS).forEach((part) => add(part))
    }
  })

  return Array.from(new Set(needles))
}

/**
 * Splits `text` into plain and marked segments covering the changed
 * substrings.
 *
 * The backend only ships the *text* of each hunk, so the position has to be
 * recovered by searching. Fragments are matched in order and each one is
 * marked at most once, starting after the previous match: an unchanged
 * repetition of the same words further down the text is therefore not
 * highlighted. A fragment that cannot be found is skipped rather than
 * matched at a wrong position.
 */
export function buildSegments(text, needles, markedKind = 'del') {
  const source = typeof text === 'string' ? text : ''
  if (!source || !needles?.length) return [{ text: source, kind: 'plain' }]

  const ranges = []
  let cursor = 0
  needles.forEach((needle) => {
    if (!needle || needle.length > source.length) return
    const index = source.indexOf(needle, cursor)
    if (index === -1) return
    ranges.push([index, index + needle.length])
    cursor = index + needle.length
  })
  if (!ranges.length) return [{ text: source, kind: 'plain' }]

  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const merged = []
  ranges.forEach((range) => {
    const last = merged[merged.length - 1]
    if (last && range[0] <= last[1]) {
      last[1] = Math.max(last[1], range[1])
    } else {
      merged.push([range[0], range[1]])
    }
  })

  const segments = []
  let position = 0
  merged.forEach(([start, end]) => {
    if (start > position) segments.push({ text: source.slice(position, start), kind: 'plain' })
    segments.push({ text: source.slice(start, end), kind: markedKind })
    position = end
  })
  if (position < source.length) {
    segments.push({ text: source.slice(position), kind: 'plain' })
  }
  return segments
}
