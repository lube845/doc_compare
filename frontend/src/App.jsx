import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  EXPORT_FORMATS,
  createComparison,
  exportReport,
  getComparison,
  getFile,
  getPreview,
  getResult,
  listComparisons,
  updateConfig,
  uploadFile,
} from './api'
import { DEFAULT_CONFIG, DEFAULT_FILTER, formatMismatchMessage, isSameFormat } from './config'
import {
  isParseFinished,
  isTaskRunning,
  sleep,
  stepForTask,
  textForTask,
} from './lib/taskStatus'
import useDebouncedValue from './hooks/useDebouncedValue'
import useTheme from './hooks/useTheme'
import ComparePage from './components/ComparePage'
import HistoryPage from './components/HistoryPage'
import Navbar from './components/Navbar'
import ResultPage from './components/ResultPage'
import SettingsPage from './components/SettingsPage'
import UploadPage from './components/UploadPage'
import { IconClose } from './components/Icons'

/** Stable identity so the memos below are not invalidated on every render. */
const EMPTY_CHANGES = []

const STAGE_COPY = {
  1: { text: '正在准备文件…', detail: '文件已上传，等待解析' },
  2: { text: '正在解析文档…', detail: '提取文本与结构信息' },
  3: { text: '正在提取文档结构…', detail: '识别段落、表格与样式' },
  4: { text: '正在分析差异…', detail: '比对文本内容与结构变化' },
  5: { text: '正在生成结果…', detail: '构建差异视图' },
}

const STAGE_PROGRESS = { 1: 12, 2: 32, 3: 52, 4: 78, 5: 95 }

export default function App() {
  const [page, setPage] = useState('upload')
  const [fileA, setFileA] = useState(null)
  const [fileB, setFileB] = useState(null)
  const [config, setConfig] = useState(DEFAULT_CONFIG)
  const [configOpen, setConfigOpen] = useState(false)
  const [previews, setPreviews] = useState({ a: null, b: null })
  const [previewStatus, setPreviewStatus] = useState({ a: 'idle', b: 'idle' })
  const [result, setResult] = useState(null)
  const [summary, setSummary] = useState(null)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState('')
  const [selectedChange, setSelectedChange] = useState(null)
  const [filter, setFilter] = useState(DEFAULT_FILTER)
  const [historyItems, setHistoryItems] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const [stage, setStage] = useState({ step: 1, progress: STAGE_PROGRESS[1] })

  const uploadSeq = useRef({ a: 0, b: 0 })
  const pollTimer = useRef(null)
  /** Generation counter: bumping it invalidates pending poll callbacks. */
  const followSeq = useRef(0)
  const mounted = useRef(true)

  const { theme, toggleTheme } = useTheme()

  /** Two PDFs are shown in the PDF viewer, which does not need a text preview. */
  const isPdfPair = Boolean(fileA?.file_type === 'pdf' && fileB?.file_type === 'pdf')

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  // -- previews (cancelled on change so a slow response cannot win a race) ---
  useEffect(() => {
    const fileId = fileA?.file_id
    // The preview reads the parse result: asking for it while the file is
    // still being parsed only produces a 409.
    if (!fileId || isPdfPair || !isParseFinished(fileA)) {
      setPreviews((prev) => ({ ...prev, a: null }))
      setPreviewStatus((prev) => ({ ...prev, a: fileId ? 'loading' : 'idle' }))
      return undefined
    }
    const controller = new AbortController()
    let disposed = false
    setPreviewStatus((prev) => ({ ...prev, a: 'loading' }))
    getPreview(fileId, controller.signal)
      .then((data) => {
        if (disposed) return
        setPreviews((prev) => ({ ...prev, a: data }))
        setPreviewStatus((prev) => ({ ...prev, a: 'ready' }))
      })
      .catch((err) => {
        if (disposed || err?.name === 'AbortError') return
        setPreviews((prev) => ({ ...prev, a: null }))
        setPreviewStatus((prev) => ({ ...prev, a: 'error' }))
      })
    return () => {
      disposed = true
      controller.abort()
    }
  }, [fileA, isPdfPair])

  useEffect(() => {
    const fileId = fileB?.file_id
    if (!fileId || isPdfPair || !isParseFinished(fileB)) {
      setPreviews((prev) => ({ ...prev, b: null }))
      setPreviewStatus((prev) => ({ ...prev, b: fileId ? 'loading' : 'idle' }))
      return undefined
    }
    const controller = new AbortController()
    let disposed = false
    setPreviewStatus((prev) => ({ ...prev, b: 'loading' }))
    getPreview(fileId, controller.signal)
      .then((data) => {
        if (disposed) return
        setPreviews((prev) => ({ ...prev, b: data }))
        setPreviewStatus((prev) => ({ ...prev, b: 'ready' }))
      })
      .catch((err) => {
        if (disposed || err?.name === 'AbortError') return
        setPreviews((prev) => ({ ...prev, b: null }))
        setPreviewStatus((prev) => ({ ...prev, b: 'error' }))
      })
    return () => {
      disposed = true
      controller.abort()
    }
  }, [fileB, isPdfPair])

  // -- uploads -------------------------------------------------------------
  /**
   * The file is parsed in the background, so the state of the file is
   * followed until the parse is done.
   */
  const trackParse = useCallback(async (uploaded, side, ticket) => {
    const fileId = uploaded.file_id
    for (;;) {
      await sleep(600)
      if (!mounted.current || uploadSeq.current[side] !== ticket) return
      let info
      try {
        info = await getFile(fileId)
      } catch {
        return
      }
      if (!mounted.current || uploadSeq.current[side] !== ticket) return
      if (side === 'a') {
        setFileA((prev) => (prev?.file_id === fileId ? info : prev))
      } else {
        setFileB((prev) => (prev?.file_id === fileId ? info : prev))
      }
      if (isParseFinished(info)) return
    }
  }, [])

  const handleUpload = useCallback(
    async (file, side) => {
      setStatus('uploading')
      setError('')
      // A slow upload must not overwrite a newer one for the same slot.
      const ticket = ++uploadSeq.current[side]
      try {
        const uploaded = await uploadFile(file)
        if (uploadSeq.current[side] !== ticket) return uploaded
        if (side === 'a') setFileA(uploaded)
        else setFileB(uploaded)
        setStatus('idle')
        void trackParse(uploaded, side, ticket)
        return uploaded
      } catch (err) {
        if (uploadSeq.current[side] !== ticket) return null
        setError(err.message)
        setStatus('idle')
        return null
      }
    },
    [trackParse],
  )

  const handleRemove = useCallback((side) => {
    uploadSeq.current[side] += 1
    if (side === 'a') setFileA(null)
    else setFileB(null)
    setError('')
  }, [])

  const handleSwap = useCallback(() => {
    setFileA((prev) => fileB)
    setFileB((prev) => fileA)
  }, [fileA, fileB])

  const refreshHistory = useCallback(async (options = {}) => {
    if (!options.silent) setHistoryLoading(true)
    setHistoryError('')
    try {
      const list = await listComparisons({ limit: 200 })
      if (mounted.current) setHistoryItems(list)
    } catch (err) {
      if (!options.silent) setHistoryError(err.message)
    } finally {
      if (!options.silent) setHistoryLoading(false)
    }
  }, [])

  // -- comparison ----------------------------------------------------------
  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current)
      pollTimer.current = null
    }
  }, [])

  /** Stop following a task; pending poll callbacks become no-ops. */
  const stopFollowing = useCallback(() => {
    followSeq.current += 1
    stopPolling()
  }, [stopPolling])

  /**
   * Follow a comparison task until it finishes.
   *
   * The task itself runs on the server, so this only polls its state and
   * shows the real progress. Starting another follow (or leaving the page)
   * invalidates the pending callbacks through ``followSeq``.
   */
  const followComparison = useCallback(
    (comparisonId, options = {}) => {
      const generation = ++followSeq.current
      const alive = () => mounted.current && followSeq.current === generation
      let failures = 0

      const tick = async () => {
        if (!alive()) return
        try {
          const detail = await getComparison(comparisonId)
          if (!alive()) return
          failures = 0
          setSummary(detail)

          if (detail.status === 'failed') {
            // Stay on the progress page: it shows the reason, and the task
            // stays visible in the history.
            setError(detail.message || '比对失败，请稍后重试')
            setStatus('failed')
            setPage('compare')
            refreshHistory({ silent: true })
            return
          }

          if (detail.status === 'completed' || detail.status === 'partial_completed') {
            const [full, infoA, infoB] = await Promise.all([
              getResult(comparisonId),
              getFile(detail.file_a_id).catch(() => null),
              getFile(detail.file_b_id).catch(() => null),
            ])
            if (!alive()) return
            if (infoA) setFileA(infoA)
            if (infoB) setFileB(infoB)
            setResult(full)
            // The reference prototype opens on the first difference.
            setSelectedChange(full.changes?.[0]?.change_id ?? null)
            setStatus('done')
            setStage({ step: 5, progress: 100, detail: null })
            setPage('result')
            if (options.onDone) options.onDone()
            return
          }

          const files = await Promise.all([
            getFile(detail.file_a_id).catch(() => null),
            getFile(detail.file_b_id).catch(() => null),
          ])
          if (!alive()) return

          const step = stepForTask(detail, files[0], files[1])
          const percent =
            typeof detail.progress === 'number' && detail.progress > 0
              ? detail.progress
              : (STAGE_PROGRESS[step] ?? STAGE_PROGRESS[1])
          setStage({ step, progress: percent, detail: textForTask(detail) })
          pollTimer.current = setTimeout(tick, 800)
        } catch (err) {
          if (!alive()) return
          // A transient failure must not abort a task running on the server.
          failures += 1
          if (failures <= 5) {
            pollTimer.current = setTimeout(tick, 1500)
            return
          }
          setError(err.message)
          setStatus('idle')
          setPage('upload')
        }
      }

      pollTimer.current = setTimeout(tick, 300)
      return generation
    },
    [stopPolling, refreshHistory],
  )

  const runComparison = useCallback(
    async (options = {}) => {
      if (!fileA?.file_id || !fileB?.file_id) {
        setError('请先上传文件 A 和文件 B')
        return
      }
      // Only same-format pairs can be compared; the server refuses the rest.
      if (!isSameFormat(fileA, fileB)) {
        setError(formatMismatchMessage(fileA, fileB))
        return
      }
      stopFollowing()
      const generation = followSeq.current
      setStatus('comparing')
      setError('')
      setSelectedChange(null)
      setFilter(DEFAULT_FILTER)
      setConfigOpen(false)
      setStage({ step: 1, progress: STAGE_PROGRESS[1], detail: null })
      setPage('compare')

      try {
        const created = await createComparison(fileA.file_id, fileB.file_id, config, false)
        // The user may have left the page while the task was being created.
        if (!mounted.current || followSeq.current !== generation) return
        setSummary(created)
        followComparison(created.comparison_id, options)
      } catch (err) {
        setError(err.message)
        setStatus('idle')
        setPage('upload')
      }
    },
    [fileA, fileB, config, stopFollowing, followComparison],
  )

  /**
   * The comparison runs on the server: leaving the page only stops watching
   * it. The task stays in the history and can be opened again later.
   */
  const cancelComparison = useCallback(() => {
    stopFollowing()
    setStatus('idle')
    setPage('upload')
  }, [stopFollowing])

  const applyConfig = useCallback(
    async (next) => {
      setConfig(next)
      if (!result) return
      setStatus('comparing')
      setError('')
      try {
        const updated = await updateConfig(result.comparison_id, next)
        const full = await getResult(result.comparison_id)
        setSummary((prev) => ({ ...(prev || {}), ...updated }))
        setResult(full)
        setStatus('done')
      } catch (err) {
        setError(err.message)
        setStatus('idle')
      }
    },
    [result],
  )

  // -- history -------------------------------------------------------------
  const openHistory = useCallback(() => {
    // Leaving the progress page ends the live view; the task itself keeps
    // running on the server and is shown in the list below.
    stopFollowing()
    setPage('history')
    refreshHistory()
  }, [refreshHistory, stopFollowing])

  // The history view refreshes itself while a task is still running.
  useEffect(() => {
    if (page !== 'history') return undefined
    if (!historyItems.some((item) => isTaskRunning(item.status))) return undefined
    const timer = setTimeout(() => refreshHistory({ silent: true }), 2000)
    return () => clearTimeout(timer)
  }, [page, historyItems, refreshHistory])

  /** Start a clean comparison: no stale files, results or selection. */
  const startNewComparison = useCallback(() => {
    stopFollowing()
    // Invalidate any upload still in flight for either slot.
    uploadSeq.current.a += 1
    uploadSeq.current.b += 1
    setFileA(null)
    setFileB(null)
    setPreviews({ a: null, b: null })
    setPreviewStatus({ a: 'idle', b: 'idle' })
    setResult(null)
    setSummary(null)
    setSelectedChange(null)
    setFilter(DEFAULT_FILTER)
    setError('')
    setStatus('idle')
    setPage('upload')
  }, [stopFollowing, stopPolling])

  const navigate = useCallback(
    (next) => {
      if (next === 'history') openHistory()
      else if (next === 'upload') startNewComparison()
      else setPage(next)
    },
    [openHistory, startNewComparison],
  )

  const loadHistoryComparison = useCallback(async (item) => {
    setStatus('loading')
    setError('')
    try {
      const [detail, full, fileAInfo, fileBInfo] = await Promise.all([
        getComparison(item.comparison_id),
        getResult(item.comparison_id),
        getFile(item.file_a_id),
        getFile(item.file_b_id),
      ])
      setFileA(fileAInfo)
      setFileB(fileBInfo)
      setSummary(detail)
      setResult(full)
      setConfig({ ...DEFAULT_CONFIG, ...(detail.config || {}) })
      setSelectedChange(full.changes?.[0]?.change_id ?? null)
      setFilter(DEFAULT_FILTER)
      setStatus('done')
      setPage('result')
    } catch (err) {
      setError(err.message)
      setStatus('idle')
    }
  }, [])

  /** Open a history entry: finished results are shown, running tasks followed. */
  const openHistoryItem = useCallback(
    (item) => {
      if (isTaskRunning(item.status)) {
        stopFollowing()
        setStatus('comparing')
        setError('')
        setSelectedChange(null)
        setFilter(DEFAULT_FILTER)
        setStage({ step: 1, progress: STAGE_PROGRESS[1], detail: null })
        // The real file records are loaded when the task finishes.
        setFileA({ file_id: item.file_a_id, filename: item.file_a_name })
        setFileB({ file_id: item.file_b_id, filename: item.file_b_name })
        setSummary(item)
        setPage('compare')
        followComparison(item.comparison_id)
        return
      }
      loadHistoryComparison(item)
    },
    [followComparison, loadHistoryComparison, stopFollowing],
  )

  // -- export --------------------------------------------------------------
  const handleExport = useCallback(
    async (format) => {
      if (!result || exporting) return
      setExporting(format)
      try {
        const text = await exportReport(result.comparison_id, format)
        const meta = EXPORT_FORMATS.find((item) => item.value === format) || EXPORT_FORMATS[0]
        const blob = new Blob([text], { type: meta.mime })
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `diff-report-${result.comparison_id}.${meta.extension}`
        document.body.appendChild(link)
        link.click()
        link.remove()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      } catch (err) {
        setError(err.message)
      } finally {
        setExporting('')
      }
    },
    [result, exporting],
  )

  // -- filtering -----------------------------------------------------------
  const changes = result?.changes ?? EMPTY_CHANGES
  const debouncedKeyword = useDebouncedValue(filter.keyword, 160)

  const filteredChanges = useMemo(() => {
    const needle = debouncedKeyword.trim().toLowerCase()
    return changes.filter((change) => {
      if (filter.type !== 'all' && change.primary_type !== filter.type) return false
      if (filter.sheet) {
        const sheetA = change.document_a?.location?.sheet_name
        const sheetB = change.document_b?.location?.sheet_name
        if (sheetA !== filter.sheet && sheetB !== filter.sheet) return false
      }
      if (filter.section) {
        const sectionA = change.document_a?.location?.section
        const sectionB = change.document_b?.location?.section
        if (sectionA !== filter.section && sectionB !== filter.section) return false
      }
      if (needle) {
        const haystack = [
          change.change_id,
          change.summary,
          change.category,
          change.document_a?.text,
          change.document_b?.text,
          change.document_a?.location?.section,
          change.document_b?.location?.section,
          change.document_a?.location?.sheet_name,
          change.document_b?.location?.sheet_name,
          change.document_a?.location?.coordinate,
          change.document_b?.location?.coordinate,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        if (!haystack.includes(needle)) return false
      }
      return true
    })
    // `filter` is destructured so a keystroke does not invalidate the memo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changes, filter.type, filter.sheet, filter.section, debouncedKeyword])

  const sections = useMemo(() => {
    const values = new Set()
    changes.forEach((change) => {
      const section = change.document_a?.location?.section || change.document_b?.location?.section
      if (section) values.add(section)
    })
    return Array.from(values)
  }, [changes])

  const sheets = useMemo(() => {
    const values = new Set()
    changes.forEach((change) => {
      const sheet = change.document_a?.location?.sheet_name || change.document_b?.location?.sheet_name
      if (sheet) values.add(sheet)
    })
    return Array.from(values)
  }, [changes])

  const selectChange = useCallback((change) => {
    if (!change) return
    setSelectedChange(change.change_id)
  }, [])

  const busy = status === 'comparing' || status === 'loading'

  return (
    <div className="app">
      <Navbar
        theme={theme}
        onToggleTheme={toggleTheme}
        onNavigate={navigate}
        onHelp={() => setHelpOpen(true)}
      />

      {page === 'upload' && (
        <UploadPage
          fileA={fileA}
          fileB={fileB}
          onUpload={handleUpload}
          onRemove={handleRemove}
          onSwap={handleSwap}
          config={config}
          onConfigChange={setConfig}
          onCompare={() => runComparison()}
          onOpenSettings={() => navigate('settings')}
          busy={busy || status === 'uploading'}
          error={error}
        />
      )}

      {page === 'compare' && (
        <ComparePage
          fileA={fileA}
          fileB={fileB}
          currentStep={stage.step}
          progress={stage.progress}
          statusText={STAGE_COPY[stage.step]?.text}
          statusDetail={stage.detail || STAGE_COPY[stage.step]?.detail}
          onCancel={cancelComparison}
          error={error}
        />
      )}

      {page === 'result' && result && (
        <ResultPage
          summary={summary}
          result={result}
          changes={changes}
          filteredChanges={filteredChanges}
          filter={filter}
          onFilterChange={setFilter}
          sections={sections}
          sheets={sheets}
          selectedChange={selectedChange}
          onSelect={selectChange}
          previews={previews}
          previewStatus={previewStatus}
          pdfMode={isPdfPair}
          config={config}
          onConfigChange={setConfig}
          onApplyConfig={applyConfig}
          configOpen={configOpen}
          onToggleConfig={() => setConfigOpen((value) => !value)}
          onBack={() => setPage('upload')}
          onRecompare={() => runComparison()}
          onExport={handleExport}
          exporting={exporting}
        />
      )}

      {page === 'history' && (
        <HistoryPage
          items={historyItems}
          loading={historyLoading}
          error={historyError}
          activeId={result?.comparison_id}
          onSelect={openHistoryItem}
          onRefresh={() => refreshHistory()}
        />
      )}

      {page === 'settings' && <SettingsPage />}

      {helpOpen && <HelpDialog onClose={() => setHelpOpen(false)} />}
    </div>
  )
}

function HelpDialog({ onClose }) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <>
      <div className="history-backdrop" onClick={onClose} />
      <div
        className="help-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="使用帮助"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="help-header">
          <h3>使用帮助</h3>
          <button className="icon-button" onClick={onClose} aria-label="关闭帮助">
            <IconClose size={16} />
          </button>
        </header>
        <div className="help-body">
          <ol>
            <li>上传两份同格式的文档：DOC、DOCX、WPS、ET、XLS、XLSX、PDF。</li>
            <li>仅支持同格式比对，两个文件的格式必须一致。</li>
            <li>按需展开「比对配置」，调整忽略空格、页眉页脚、段落移动等规则。</li>
            <li>点击「开始比对」，等待解析与差异分析完成。</li>
            <li>在结果页左侧清单中筛选、搜索差异，点击即可定位到两侧原文。</li>
          </ol>
          <p className="hint">
            快捷键：<kbd>↑</kbd> <kbd>↓</kbd> 切换差异 · <kbd>/</kbd> 搜索 · <kbd>Esc</kbd> 关闭面板
          </p>
        </div>
      </div>
    </>
  )
}
