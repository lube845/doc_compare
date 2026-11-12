import { IconArrowRight } from './Icons'

const STEPS = ['上传文件', '解析文档', '提取结构', '分析差异', '生成结果']

/**
 * Progress page shown while the comparison runs in the background. The step,
 * the percentage and the text are all derived from the real backend state,
 * so the page reflects what the server is actually doing.
 *
 * The task keeps running on the server when the user leaves this page - the
 * history view is where it can be watched later.
 */
export default function ComparePage({
  fileA,
  fileB,
  currentStep,
  progress,
  statusText,
  statusDetail,
  onCancel,
  error,
}) {
  return (
    <div className="page-body">
      <div className="compare-container">
        <div className="compare-files">
          <FileCard file={fileA} label="原始文件" tone="#3b82f6" />
          <span className="compare-arrow">
            <IconArrowRight size={20} />
          </span>
          <FileCard file={fileB} label="修订文件" tone="#22c55e" />
        </div>

        <div className="steps">
          {STEPS.map((label, index) => {
            const position = index + 1
            const state =
              position < currentStep ? 'done' : position === currentStep ? 'active' : ''
            return (
              <div className={`step ${state}`} key={label}>
                <div className="step-circle">{position}</div>
                <div className="step-label">{label}</div>
                {position < STEPS.length && (
                  <div className="step-line">
                    <div
                      className="step-line-fill"
                      style={{ width: position < currentStep ? '100%' : '0%' }}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${progress}%` }} />
        </div>

        <div className="compare-status">
          <div className="status-text">{statusText}</div>
          <div className="status-detail">{statusDetail}</div>
          {typeof progress === 'number' && progress > 0 && (
            <div className="status-percent">{Math.round(progress)}%</div>
          )}
        </div>

        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}

        <div className="compare-cancel">
          <button onClick={onCancel}>转到后台执行</button>
          <div className="compare-hint">
            比对在服务器上继续运行，可在「历史任务」中查看进度和结果
          </div>
        </div>
      </div>
    </div>
  )
}

function FileCard({ file, label, tone }) {
  const extension = (file?.filename || '').split('.').pop() || '?'
  const meta = [
    label,
    file?.size ? formatSize(file.size) : null,
    file?.page_count ? `${file.page_count} 页` : null,
    file?.sheet_count ? `${file.sheet_count} 个工作表` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="compare-file">
      <div className="cf-icon" style={{ background: tone }} aria-hidden="true">
        {extension.toUpperCase()}
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="cf-name" title={file?.filename}>
          {file?.filename || '未选择文件'}
        </div>
        <div className="cf-meta">{meta}</div>
      </div>
    </div>
  )
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(index === 0 ? 0 : 0)} ${units[index]}`
}
