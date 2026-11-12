import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * Fixed-height row virtualisation for the spreadsheet grid.
 *
 * The preview endpoint returns every cell of every sheet, so a 10 000-row
 * workbook would otherwise mount hundreds of thousands of DOM nodes
 * (PRD 16.1). Only the rows inside the viewport (plus a small overscan) are
 * rendered; the rest is represented by two spacer rows.
 *
 * `resetKey` identifies the rendered content (the worksheet name): whenever it
 * changes the scroll offset and the window are reset, because the previous
 * offset may point past the end of the new, shorter sheet.
 */
export default function useVirtualRows({ total, rowHeight, overscan = 12, resetKey }) {
  const containerRef = useRef(null)
  const [range, setRange] = useState({ start: 0, end: Math.min(total, 40) })
  const resetKeyRef = useRef(resetKey)

  // Adjust the state while rendering (React re-renders before committing) so
  // the grid never shows a stale window for a single frame.
  if (resetKeyRef.current !== resetKey) {
    resetKeyRef.current = resetKey
    if (range.start !== 0) setRange({ start: 0, end: Math.min(total, 40) })
  }

  useLayoutEffect(() => {
    if (containerRef.current) containerRef.current.scrollTop = 0
  }, [resetKey])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined

    let frame = 0
    const update = () => {
      frame = 0
      const scrollTop = container.scrollTop
      const height = container.clientHeight || 480
      const rawStart = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan)
      // The scroll offset can still belong to a previous, longer sheet.
      const start = Math.min(rawStart, total)
      const end = Math.max(
        start,
        Math.min(total, Math.ceil((scrollTop + height) / rowHeight) + overscan),
      )
      setRange((prev) =>
        prev.start === start && prev.end === end ? prev : { start, end },
      )
    }
    const onScroll = () => {
      if (frame) return
      frame = requestAnimationFrame(update)
    }

    update()
    container.addEventListener('scroll', onScroll, { passive: true })
    const observer = new ResizeObserver(update)
    observer.observe(container)

    return () => {
      container.removeEventListener('scroll', onScroll)
      observer.disconnect()
      if (frame) cancelAnimationFrame(frame)
    }
  }, [total, rowHeight, overscan])

  return { containerRef, start: range.start, end: range.end }
}
