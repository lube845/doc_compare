import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const PAGE_GAP = 14
const WARNING_HEIGHT = 32
const MIN_ZOOM = 0.25
const MAX_ZOOM = 4

const TYPE_LABELS = {
  added: '新增',
  deleted: '删除',
  modified: '修改',
  moved: '移动',
}

/** The colour a difference has on one side of the comparison (pdf展示 2.4). */
function toneOf(change, side) {
  const type = change.primary_type
  if (side === 'a') {
    if (type === 'added') return null
    if (type === 'deleted' || type === 'modified') return 'del'
    if (type === 'moved') return 'move'
    return null
  }
  if (type === 'deleted') return null
  if (type === 'added' || type === 'modified') return 'ins'
  if (type === 'moved') return 'move'
  return null
}

function sideDataOf(change, side) {
  return side === 'a' ? change.document_a : change.document_b
}

/**
 * Load a PDF (and the size of every page) from its original file.
 * PDF.js renders the original page; the comparison never re-lays-out text
 * (pdf展示 2.3 / 3.3).
 */
function usePdfDocument(url) {
  const [state, setState] = useState({ status: 'idle', pdf: null, pages: [], error: '' })

  useEffect(() => {
    if (!url) {
      setState({ status: 'idle', pdf: null, pages: [], error: '' })
      return undefined
    }
    let cancelled = false
    let document = null
    setState({ status: 'loading', pdf: null, pages: [], error: '' })

    const task = pdfjsLib.getDocument({
      url,
      cMapUrl: '/pdfjs/cmaps/',
      cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/',
    })

    task.promise
      .then(async (loaded) => {
        if (cancelled) {
          loaded.destroy()
          return
        }
        const pages = []
        for (let number = 1; number <= loaded.numPages; number += 1) {
          const page = await loaded.getPage(number)
          const viewport = page.getViewport({ scale: 1 })
          pages.push({ page, width: viewport.width, height: viewport.height })
          if (cancelled) return
        }
        if (cancelled) return
        document = loaded
        setState({ status: 'ready', pdf: loaded, pages, error: '' })
      })
      .catch((error) => {
        if (cancelled) return
        setState({
          status: 'error',
          pdf: null,
          pages: [],
          error: error?.message || 'PDF 加载失败',
        })
      })

    return () => {
      cancelled = true
      // The loading task owns the worker and the document: destroying it
      // releases both (PDF.js 6 has no destroy() on the document itself).
      task.destroy?.()
      document = null
    }
  }, [url])

  return state
}

/** The rectangles of a change on one page, in viewport coordinates. */
function rectsOnPage(change, side, pageNumber, pageView, viewport) {
  const data = sideDataOf(change, side)
  const location = data?.location || {}
  const extra = location.extra || {}
  const rects = Array.isArray(extra.rects) ? extra.rects : []
  const rectPages = Array.isArray(extra.rect_pages) ? extra.rect_pages : []
  const fallback = location.page

  const result = []
  rects.forEach((rect, index) => {
    const rectPage = rectPages[index] ?? fallback
    if (rectPage !== pageNumber) return
    if (!Array.isArray(rect) || rect.length < 4) return
    // The backend coordinate system: unrotated page, origin at the top left,
    // y growing downwards (PyMuPDF).  PDF.js works in the PDF user space:
    // origin at the bottom left of the page box, y growing upwards.  The
    // viewport then applies the page rotation and the zoom.
    const [x0, y0, x1, y1] = rect
    const userRect = [
      x0,
      pageView[3] - y1,
      x1,
      pageView[3] - y0,
    ]
    // PDF.js 6 no longer ships convertToViewportRectangle: the two corners
    // of the rectangle are converted one by one.
    const cornerA = viewport.convertToViewportPoint(userRect[0], userRect[1])
    const cornerB = viewport.convertToViewportPoint(userRect[2], userRect[3])
    const left = Math.min(cornerA[0], cornerB[0])
    const top = Math.min(cornerA[1], cornerB[1])
    result.push({
      change,
      tone: toneOf(change, side),
      left,
      top,
      width: Math.abs(cornerB[0] - cornerA[0]),
      height: Math.abs(cornerB[1] - cornerA[1]),
    })
  })
  return result
}

function PdfPageView({
  meta,
  scale,
  number,
  changes,
  side,
  selectedChange,
  onSelect,
}) {
  // A page is rendered while it is near the viewport and released when it
  // leaves it: a canvas keeps its pixels in memory (about 3 MB for an A4
  // page), and a long document would otherwise use hundreds of megabytes
  // after a scroll through it.
  const [near, setNear] = useState(false)
  const hostRef = useRef(null)
  const canvasRef = useRef(null)

  const viewport = useMemo(
    () => meta.page.getViewport({ scale }),
    [meta.page, scale],
  )

  useEffect(() => {
    const node = hostRef.current
    if (!node) return undefined
    if (typeof IntersectionObserver === 'undefined') {
      setNear(true)
      return undefined
    }
    const observer = new IntersectionObserver(
      (entries) => {
        setNear(entries.some((entry) => entry.isIntersecting))
      },
      { rootMargin: '600px 0px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined

    if (!near) {
      // Release the pixels of the page; it is rendered again when the
      // reader scrolls back to it.
      canvas.width = 0
      canvas.height = 0
      return undefined
    }

    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.floor(viewport.width * ratio)
    canvas.height = Math.floor(viewport.height * ratio)
    canvas.style.width = `${viewport.width}px`
    canvas.style.height = `${viewport.height}px`

    let task = null
    try {
      task = meta.page.render({
        canvasContext: canvas.getContext('2d'),
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      })
    } catch {
      return undefined
    }
    task.promise.catch(() => {
      /* a cancelled render is not an error */
    })
    return () => {
      task?.cancel()
    }
  }, [near, meta.page, viewport])

  const highlights = useMemo(() => {
    const result = []
    changes.forEach((change) => {
      rectsOnPage(change, side, number, meta.page.view, viewport).forEach((item) => {
        if (item.tone) result.push(item)
      })
    })
    return result
  }, [changes, side, number, meta.page, viewport])

  return (
    <div
      className="pdf-page"
      ref={hostRef}
      style={{ width: `${viewport.width}px`, height: `${viewport.height}px` }}
    >
      {!near ? <div className="pdf-page-placeholder" aria-hidden="true" /> : null}
      <canvas ref={canvasRef} className="pdf-canvas" />
      <div className="pdf-overlay" aria-label={`第 ${number} 页差异高亮`}>
        {highlights.map((item, index) => (
          <button
            key={`${item.change.change_id}-${index}`}
            type="button"
            className={`pdf-highlight pdf-highlight-${item.tone} ${
              item.change.change_id === selectedChange ? 'selected' : ''
            }`}
            style={{
              left: `${item.left}px`,
              top: `${item.top}px`,
              width: `${Math.max(2, item.width)}px`,
              height: `${Math.max(2, item.height)}px`,
            }}
            title={`${TYPE_LABELS[item.change.primary_type] || '差异'}：${
              item.change.summary || ''
            }`}
            onClick={(event) => {
              event.stopPropagation()
              onSelect?.(item.change)
            }}
          />
        ))}
      </div>
    </div>
  )
}

function PdfPane({
  side,
  document,
  changes,
  selectedChange,
  onSelect,
  zoom,
  fit,
  onZoom,
  onFit,
  currentPage,
  onPageChange,
  scrollRef,
  onScrollInfo,
}) {
  const state = usePdfDocument(document?.raw_url)
  const containerRef = useRef(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [pageDraft, setPageDraft] = useState(1)

  useEffect(() => {
    setPageDraft(currentPage)
  }, [currentPage])

  useEffect(() => {
    const node = containerRef.current
    if (!node || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(() => {
      setSize({ width: node.clientWidth, height: node.clientHeight })
    })
    observer.observe(node)
    setSize({ width: node.clientWidth, height: node.clientHeight })
    return () => observer.disconnect()
  }, [])

  // The scale is a fit of page 1 to the pane, multiplied by the zoom.
  const scale = useMemo(() => {
    const first = state.pages[0]
    if (!first || !size.width || !size.height) return 0
    const padding = 32
    const byWidth = (size.width - padding) / first.width
    const byHeight = (size.height - padding) / first.height
    const fitScale = fit === 'page' ? Math.min(byWidth, byHeight) : byWidth
    return Math.max(0.05, fitScale * zoom)
  }, [state.pages, size.width, size.height, fit, zoom])

  const noTextPages = document?.no_text_pages || []

  const tops = useMemo(() => {
    const result = []
    let y = 0
    state.pages.forEach((meta, index) => {
      // The warning banner above a page without a text layer takes room.
      if (noTextPages.includes(index + 1)) y += WARNING_HEIGHT
      result.push(y)
      y += meta.height * scale + PAGE_GAP
    })
    return result
  }, [state.pages, scale, noTextPages])

  const heights = useMemo(
    () => state.pages.map((meta) => meta.height * scale),
    [state.pages, scale],
  )

  const reportPage = useCallback(
    (scrollTop) => {
      if (!state.pages.length || !heights.length) return null
      const middle = scrollTop + (containerRef.current?.clientHeight || 0) / 3
      let index = 0
      for (let cursor = 0; cursor < tops.length; cursor += 1) {
        if (middle >= tops[cursor]) index = cursor
        if (middle <= tops[cursor] + heights[cursor]) {
          index = cursor
          break
        }
      }
      const ratio = heights[index]
        ? Math.max(0, Math.min(1, (scrollTop - tops[index]) / heights[index]))
        : 0
      onPageChange(index + 1)
      return { page: index + 1, ratio, index }
    },
    [state.pages, tops, heights, onPageChange],
  )

  const handleScroll = useCallback(
    (event) => {
      const info = reportPage(event.currentTarget.scrollTop)
      onScrollInfo?.(info)
    },
    [reportPage, onScrollInfo],
  )

  const scrollToPage = useCallback(
    (pageNumber, options = {}) => {
      const node = containerRef.current
      if (!node || !tops.length) return
      const index = Math.max(0, Math.min(tops.length - 1, pageNumber - 1))
      const ratio = options.ratio ?? 0
      const yPoints = options.yPoints ?? 0
      const y = tops[index] + ratio * heights[index] + Math.max(0, yPoints) * scale
      node.scrollTop = Math.max(0, y - node.clientHeight / 4)
    },
    [tops, heights, scale],
  )

  useEffect(() => {
    if (scrollRef) {
      scrollRef.current = {
        scrollToPage,
        container: containerRef.current,
        scale,
        pageCount: state.pages.length,
      }
    }
    return () => {
      if (scrollRef) scrollRef.current = null
    }
  }, [scrollRef, scrollToPage, scale, state.pages.length])

  return (
    <section className={`pdf-pane pdf-pane-${side}`}>
      <header className="pdf-pane-header">
        <span className={`ph-icon ${side === 'a' ? 'tone-a' : 'tone-b'}`}>
          {side.toUpperCase()}
        </span>
        <span className="ph-name" title={document?.file_name}>
          {document?.file_name}
        </span>
        <span className="pdf-page-indicator">
          {state.pages.length ? (
            <input
              className="pdf-page-input"
              type="number"
              min={1}
              max={state.pages.length}
              value={pageDraft}
              aria-label="跳转页码"
              onChange={(event) => setPageDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter') return
                const value = Number(event.currentTarget.value)
                if (Number.isFinite(value) && value >= 1) {
                  scrollToPage(Math.min(state.pages.length, Math.round(value)), {})
                }
              }}
            />
          ) : (
            0
          )}{' '}
          / {document?.page_count ?? state.pages.length} 页
        </span>
        <div className="pdf-pane-zoom">
          <button
            type="button"
            className="pdf-zoom-btn"
            title="缩小"
            onClick={() => onZoom(-1)}
          >
            −
          </button>
          <span>{scale ? Math.round(scale * 100) : 0}%</span>
          <button
            type="button"
            className="pdf-zoom-btn"
            title="放大"
            onClick={() => onZoom(1)}
          >
            +
          </button>
          <button
            type="button"
            className="pdf-zoom-btn pdf-zoom-fit"
            title="适合宽度"
            onClick={() => onFit('width')}
          >
            宽
          </button>
          <button
            type="button"
            className="pdf-zoom-btn pdf-zoom-fit"
            title="适合页面"
            onClick={() => onFit('page')}
          >
            页
          </button>
        </div>
      </header>

      {state.status === 'error' ? (
        <div className="pdf-pane-message">PDF 加载失败：{state.error}</div>
      ) : state.status === 'loading' ? (
        <div className="pdf-pane-message">正在加载原始 PDF…</div>
      ) : (
        <div
          className="pdf-scroll"
          ref={containerRef}
          onScroll={handleScroll}
          tabIndex={0}
          role="region"
          aria-label={`${side === 'a' ? '原始' : '修订'} PDF 页面`}
        >
          <div className="pdf-pages">
            {state.pages.map((meta, index) => (
              <div className="pdf-page-slot" key={index}>
                {noTextPages.includes(index + 1) ? (
                  <div className="pdf-page-warning">
                    本页没有可提取的文本，未参与文本比对
                  </div>
                ) : null}
                {scale > 0 ? (
                  <PdfPageView
                    meta={meta}
                    scale={scale}
                    number={index + 1}
                    changes={changes}
                    side={side}
                    selectedChange={selectedChange}
                    onSelect={onSelect}
                  />
                ) : null}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function pageLookup(pairs) {
  const leftToRight = new Map()
  const rightToLeft = new Map()
  pairs.forEach((pair) => {
    leftToRight.set(pair.left_page, pair.right_page)
    rightToLeft.set(pair.right_page, pair.left_page)
  })
  return { leftToRight, rightToLeft }
}

/** The page of the other document that goes with a page (page mapping). */
function counterpart(map, page, maxPage) {
  if (!map.size) return null
  if (map.has(page)) return map.get(page)
  let best = null
  for (const [left, right] of map.entries()) {
    if (left <= page && (!best || left > best.left)) best = { left, right }
  }
  if (!best) return null
  return Math.min(maxPage || best.right, best.right + (page - best.left))
}

/**
 * The PDF comparison view: two original PDF readers with an overlay of the
 * differences, a synchronised scroll and the navigation of the result page
 * (pdf展示 2.3 - 2.6).
 */
function PdfCompareView({
  left,
  right,
  changes,
  selectedChange,
  onSelect,
  pageMapping,
}) {
  const [viewMode, setViewMode] = useState('split')
  const [syncScroll, setSyncScroll] = useState(true)
  const [syncZoom, setSyncZoom] = useState(true)
  const [fit, setFit] = useState('width')
  const [zoomA, setZoomA] = useState(1)
  const [zoomB, setZoomB] = useState(1)
  const [pageA, setPageA] = useState(1)
  const [pageB, setPageB] = useState(1)

  const paneARef = useRef(null)
  const paneBRef = useRef(null)
  const syncing = useRef(null)

  const maps = useMemo(() => pageLookup(pageMapping || []), [pageMapping])

  // A big file is not a problem for the comparison (it runs on the server),
  // but the reader has to fetch it page by page.
  const bigFile = useMemo(() => {
    const size = Math.max(left?.size || 0, right?.size || 0)
    return size > 30 * 1024 * 1024 ? Math.round(size / 1048576) : 0
  }, [left?.size, right?.size])

  const zoomOf = (side) => (side === 'a' ? zoomA : zoomB)
  const setZoomOf = (side, value) => {
    const clamped = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value))
    if (side === 'a') {
      setZoomA(clamped)
      if (syncZoom) setZoomB(clamped)
    } else {
      setZoomB(clamped)
      if (syncZoom) setZoomA(clamped)
    }
  }

  const handleZoom = (side, direction) => {
    const value = zoomOf(side) * (direction > 0 ? 1.15 : 1 / 1.15)
    setZoomOf(side, value)
  }

  const handleFit = (_side, mode) => {
    // A fit is a property of the viewport, so it applies to both panes.
    setFit(mode)
    setZoomA(1)
    setZoomB(1)
  }

  const scrollOther = useCallback(
    (source, target, info) => {
      if (!syncScroll || !info || !target.current || syncing.current) return
      const map = source === 'a' ? maps.leftToRight : maps.rightToLeft
      const otherPage = counterpart(
        map,
        info.page,
        target.current.pageCount,
      )
      if (!otherPage) return
      syncing.current = target
      target.current.scrollToPage(otherPage, { ratio: info.ratio })
      setTimeout(() => {
        syncing.current = null
      }, 80)
    },
    [syncScroll, maps],
  )

  const onScrollA = useCallback(
    (info) => scrollOther('a', paneBRef, info),
    [scrollOther],
  )
  const onScrollB = useCallback(
    (info) => scrollOther('b', paneARef, info),
    [scrollOther],
  )

  // Jump to the selected difference on both sides.
  useEffect(() => {
    if (!selectedChange) return
    const change = changes.find((item) => item.change_id === selectedChange)
    if (!change) return
    const jump = (ref, side) => {
      const data = side === 'a' ? change.document_a : change.document_b
      if (!data || !ref.current) return
      const extra = data.location?.extra || {}
      const page = (extra.rect_pages || [])[0] || data.location?.page
      if (!page) return
      const rect = (extra.rects || [])[0]
      ref.current.scrollToPage(page, { yPoints: rect ? rect[1] : 0 })
    }
    jump(paneARef, 'a')
    jump(paneBRef, 'b')
  }, [selectedChange, changes])

  const showA = viewMode !== 'right'
  const showB = viewMode !== 'left'

  return (
    <div className="pdf-compare">
      <div className="pdf-toolbar">
        <div className="pdf-tool-group" role="group" aria-label="显示方式">
          <button
            type="button"
            className={`pdf-tool-btn ${viewMode === 'split' ? 'active' : ''}`}
            onClick={() => setViewMode('split')}
          >
            并排
          </button>
          <button
            type="button"
            className={`pdf-tool-btn ${viewMode === 'left' ? 'active' : ''}`}
            onClick={() => setViewMode('left')}
          >
            仅原文
          </button>
          <button
            type="button"
            className={`pdf-tool-btn ${viewMode === 'right' ? 'active' : ''}`}
            onClick={() => setViewMode('right')}
          >
            仅修订
          </button>
        </div>

        <div className="pdf-tool-group" role="group" aria-label="同步">
          <button
            type="button"
            className={`pdf-tool-btn ${syncScroll ? 'active' : ''}`}
            aria-pressed={syncScroll}
            onClick={() => setSyncScroll((value) => !value)}
          >
            同步滚动{syncScroll ? '开' : '关'}
          </button>
          <button
            type="button"
            className={`pdf-tool-btn ${syncZoom ? 'active' : ''}`}
            aria-pressed={syncZoom}
            onClick={() => setSyncZoom((value) => !value)}
          >
            同步缩放{syncZoom ? '开' : '关'}
          </button>
        </div>

        <div className="pdf-tool-group" role="group" aria-label="缩放到两侧">
          <button
            type="button"
            className="pdf-tool-btn"
            onClick={() => {
              setFit('width')
              setZoomA(1)
              setZoomB(1)
            }}
          >
            适合宽度
          </button>
          <button
            type="button"
            className="pdf-tool-btn"
            onClick={() => {
              setFit('page')
              setZoomA(1)
              setZoomB(1)
            }}
          >
            适合页面
          </button>
        </div>

        <div className="pdf-tool-hint">
          {bigFile ? (
            <span className="pdf-tool-warning">
              文件较大（{bigFile} MB），页面按需渲染，仅加载可视部分
            </span>
          ) : null}
          高亮：<span className="pdf-legend pdf-legend-del">删除/修改</span>
          <span className="pdf-legend pdf-legend-ins">新增/修改</span>
        </div>
      </div>

      <div className={`pdf-panes mode-${viewMode}`}>
        {showA ? (
          <PdfPane
            side="a"
            document={left}
            changes={changes}
            selectedChange={selectedChange}
            onSelect={onSelect}
            zoom={zoomA}
            fit={fit}
            onZoom={(direction) => handleZoom('a', direction)}
            onFit={(mode) => handleFit('a', mode)}
            currentPage={pageA}
            onPageChange={setPageA}
            scrollRef={paneARef}
            onScrollInfo={onScrollA}
          />
        ) : null}
        {showB ? (
          <PdfPane
            side="b"
            document={right}
            changes={changes}
            selectedChange={selectedChange}
            onSelect={onSelect}
            zoom={zoomB}
            fit={fit}
            onZoom={(direction) => handleZoom('b', direction)}
            onFit={(mode) => handleFit('b', mode)}
            currentPage={pageB}
            onPageChange={setPageB}
            scrollRef={paneBRef}
            onScrollInfo={onScrollB}
          />
        ) : null}
      </div>
    </div>
  )
}

export default memo(PdfCompareView)
