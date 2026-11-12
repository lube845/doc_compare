import { ACCEPTED_EXTENSIONS, formatMismatchMessage, isSameFormat } from '../config'
import ConfigSection from './ConfigSection'
import UploadBox from './UploadBox'
import { IconPlay, IconSwap } from './Icons'

/**
 * Landing page: pick the two documents, optionally adjust the comparison
 * rules and start the run.
 */
export default function UploadPage({
  fileA,
  fileB,
  onUpload,
  onRemove,
  onSwap,
  config,
  onConfigChange,
  onCompare,
  onOpenSettings,
  busy,
  error,
}) {
  const ready = Boolean(fileA?.file_id && fileB?.file_id)
  // A scan that could not be read has no text to compare.
  const scanned = [fileA, fileB].filter(
    (file) => file?.parse_status === 'unsupported_ocr',
  )
  // Only files of the same format can be compared.
  const sameFormat = isSameFormat(fileA, fileB)
  const formatHint =
    ready && !sameFormat ? formatMismatchMessage(fileA, fileB) : ''

  return (
    <div className="page-body">
      <div className="upload-container">
        <header className="upload-header">
          <h1>多格式文档比对助手</h1>
          <p>支持 DOC、DOCX、WPS、ET、XLS、XLSX、PDF，同格式文件两两比对</p>
          <p>自动识别新增、删除、修改与段落 / 单元格移动</p>
        </header>

        <div className="upload-row">
          <UploadBox
            file={fileA}
            side="a"
            label="原始文件 A"
            onUpload={onUpload}
            onRemove={onRemove}
          />
          <button
            className="swap-btn"
            onClick={onSwap}
            title="交换文件"
            aria-label="交换文件 A 与文件 B"
            disabled={busy}
          >
            <IconSwap size={18} />
          </button>
          <UploadBox
            file={fileB}
            side="b"
            label="修订文件 B"
            onUpload={onUpload}
            onRemove={onRemove}
          />
        </div>

        {scanned.length > 0 && (
          <div className="scanned-alert" role="alert">
            <div>
              <strong>检测到扫描版 PDF</strong>
              <p>{scanned.map((file) => file.filename).join('、')}</p>
              <p className="hint">
                {scanned[0].message ||
                  '扫描件没有文本层，未配置 OCR 时无法比对其中的文字。'}
              </p>
            </div>
            {onOpenSettings && (
              <button className="btn-sm" onClick={onOpenSettings}>
                去配置 OCR
              </button>
            )}
          </div>
        )}

        <ConfigSection config={config} onChange={onConfigChange} />

        {formatHint && (
          <div className="error" role="alert">
            {formatHint}
          </div>
        )}

        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}

        <div className="upload-actions">
          <button
            className="primary"
            onClick={onCompare}
            disabled={!ready || !sameFormat || busy || scanned.length > 0}
          >
            <IconPlay size={16} />
            {busy ? '处理中…' : '开始比对'}
          </button>
        </div>

        <div className="format-tags">
          {ACCEPTED_EXTENSIONS.map((extension) => (
            <span className="format-tag" key={extension}>
              {extension.toUpperCase()}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
