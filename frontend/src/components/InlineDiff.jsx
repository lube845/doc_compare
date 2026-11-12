import { useMemo } from 'react'
import { buildSegments, collectNeedles } from '../lib/diffText'

/**
 * Renders text with the changed characters marked inline. The needles come
 * from the backend diff fragments, so the highlight points at the exact
 * characters that differ (PRD 10.3).
 */
export default function InlineDiff({ text, side = 'a', change, changes, className = '' }) {
  const segments = useMemo(() => {
    const needles = collectNeedles(changes ?? change, side)
    return buildSegments(text, needles, side === 'a' ? 'del' : 'ins')
  }, [text, change, changes, side])

  if (!segments.length) return null

  return (
    <span className={className}>
      {segments.map((segment, index) =>
        segment.kind === 'plain' ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <mark key={index} className={`diff-mark diff-${segment.kind}`}>
            {segment.text}
          </mark>
        ),
      )}
    </span>
  )
}
