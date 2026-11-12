import { memo, useMemo } from 'react'
import InlineDiff from './InlineDiff'

const EMPTY = []

/**
 * Renders the parsed document blocks and highlights the differences that the
 * diff engine reported (PRD 10.3 / 10.5).
 */
function DocumentPane({ preview, changes, selectedChange, onSelect, side }) {
  // Map block id -> the changes touching it on this side. Table cell
  // differences are anchored on the table id (`t{n}`) rather than the block
  // id (`b{n}`), so both ids are indexed.
  const blockChanges = useMemo(() => {
    const map = new Map()
    const push = (key, change) => {
      if (!key) return
      const list = map.get(key)
      if (list) list.push(change)
      else map.set(key, [change])
    }
    changes.forEach((change) => {
      const location = side === 'a' ? change.document_a?.location : change.document_b?.location
      push(location?.block_id, change)
      // The table id is often identical to the block id — index it only once.
      const tableId = location?.extra?.table_id
      if (tableId !== location?.block_id) push(tableId, change)
    })
    return map
  }, [changes, side])

  const handleClick = (related) => {
    if (!related.length) return
    // Keep the current selection when a block carries several differences.
    const current = related.find((change) => change.change_id === selectedChange)
    onSelect(current || related[0])
  }

  return (
    <div className="document-pane">
      {preview.blocks.map((block) => {
        const related =
          blockChanges.get(block.block_id) || blockChanges.get(block.table?.table_id) || EMPTY
        const types = new Set(related.map((change) => change.primary_type))
        const className = [
          'doc-block',
          `type-${block.type}`,
          types.size ? `mark-${[...types].join(' ')}` : '',
          related.some((change) => change.change_id === selectedChange) ? 'selected' : '',
        ]
          .filter(Boolean)
          .join(' ')

        return (
          <div
            key={block.block_id}
            className={className}
            data-node-id={block.block_id}
            data-table-id={block.table?.table_id}
            onClick={() => handleClick(related)}
            role={related.length ? 'button' : undefined}
            tabIndex={related.length ? -1 : undefined}
            aria-label={
              related.length ? `包含 ${related.length} 处差异的段落，点击查看详情` : undefined
            }
          >
            {block.page ? <span className="page-tag">P{block.page}</span> : null}
            {block.type === 'heading' ? (
              <h3 style={{ marginLeft: `${((block.level || 1) - 1) * 14}px` }}>
                <InlineDiff text={block.text} changes={related} side={side} />
              </h3>
            ) : null}
            {block.type === 'table' && block.table ? (
              <table className="doc-table">
                <tbody>
                  {renderTableRows(block.table)}
                </tbody>
              </table>
            ) : null}
            {block.type !== 'table' && block.type !== 'heading' ? (
              <p>
                <InlineDiff text={block.text} changes={related} side={side} />
                {block.images?.length ? <ImageChips images={block.images} /> : null}
              </p>
            ) : null}
            {block.type === 'heading' && block.images?.length ? (
              <ImageChips images={block.images} />
            ) : null}
            {related.length > 0 && (
              <div className="block-marks">
                {related.map((change) => (
                  <span key={change.change_id} className={`mark mark-${change.primary_type}`}>
                    {typeLabel(change.primary_type)}
                  </span>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Renders the rows of a table.
 *
 * `layout` describes what the reader sees: one entry per cell with the
 * columns and rows it covers, so a merged cell is shown once instead of
 * being repeated for every grid column it occupies.  Formats without a
 * layout (native DOC reader, PDF tables) fall back to the plain grid rows.
 */
function renderTableRows(table) {
  const layout = table.layout
  if (!layout?.length) {
    return table.rows.map((row, rowIndex) => (
      <tr key={rowIndex}>
        {row.map((cell, cellIndex) => (
          <td key={cellIndex}>{cell}</td>
        ))}
      </tr>
    ))
  }

  return layout.map((row, rowIndex) => (
    <tr key={rowIndex}>
      {row
        .filter((cell) => !cell.continuation)
        .map((cell, cellIndex) => (
          <td
            key={cellIndex}
            colSpan={cell.colspan > 1 ? cell.colspan : undefined}
            rowSpan={cell.rowspan > 1 ? cell.rowspan : undefined}
          >
            {cell.text}
            {cell.images?.length ? <ImageChips images={cell.images} /> : null}
          </td>
        ))}
    </tr>
  ))
}

/**
 * Marker for pictures in the text view.
 *
 * The text view does not carry the picture bytes (the original-layout view
 * renders them); the chip makes the picture visible in the comparison and
 * states how it takes part: identical bytes are compared, the content of the
 * picture (text/shapes) is not recognised.
 */
function ImageChips({ images }) {
  return (
    <span
      className="image-chips"
      title="图片按文件哈希参与比对；不识别图片内的文字或图形"
    >
      {images.map((image, index) => (
        <span key={index} className="image-chip">
          图片 {formatSize(image.size)}
        </span>
      ))}
    </span>
  )
}

function formatSize(size) {
  if (!size) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function typeLabel(type) {
  if (type === 'added') return '新增'
  if (type === 'deleted') return '删除'
  if (type === 'moved') return '移动'
  if (type === 'unchanged') return '未变更'
  return '修改'
}

export default memo(DocumentPane)
