import { useMemo, useState } from 'react'
import { DEFAULT_CONFIG } from '../config'
import { IconChevronRight, IconRefresh } from './Icons'

const GROUPS = [
  {
    title: '全局规则',
    items: [
      ['ignore_whitespace', '忽略连续空格'],
      ['ignore_newlines', '忽略换行差异'],
      ['ignore_headers_footers', '忽略页眉页脚'],
      ['ignore_punctuation', '忽略标点差异'],
      ['ignore_case', '忽略大小写'],
      ['ignore_full_width', '忽略全角/半角差异'],
    ],
  },
  {
    title: '文档规则',
    items: [
      ['ignore_numbering', '忽略段落编号变化'],
      ['detect_moved_paragraphs', '识别段落移动'],
    ],
  },
  {
    title: '表格规则',
    items: [
      ['ignore_blank_cells', '忽略空白单元格'],
      ['ignore_formula', '忽略公式差异'],
      ['compare_formula', '比较公式'],
      ['compare_display_value', '比较显示值'],
      ['compare_cell_format', '比较单元格格式'],
      ['compare_row_order', '比较行列顺序'],
      ['ignore_blank_rows', '忽略空白行列'],
      ['compare_sheet_order', '比较工作表顺序'],
    ],
  },
  {
    title: 'PDF 规则',
    items: [
      ['pdf_stream_mode', 'PDF 按字符流比对（不受排版影响）'],
    ],
  },
]

/**
 * Collapsible "比对配置" section. It is used on the upload page (the config is
 * picked up when the comparison starts) and in the result header (where the
 * user can push the changed rules into the running comparison).
 */
export default function ConfigSection({
  config,
  onChange,
  appliedConfig,
  onApply,
  defaultOpen = false,
}) {
  const [open, setOpen] = useState(defaultOpen)

  const dirty = useMemo(() => {
    if (!appliedConfig) return false
    return Object.keys(DEFAULT_CONFIG).some((key) => {
      if (key === 'primary_key_columns') {
        return (
          (appliedConfig.primary_key_columns || []).join(',') !==
          (config.primary_key_columns || []).join(',')
        )
      }
      return Boolean(appliedConfig[key]) !== Boolean(config[key])
    })
  }, [config, appliedConfig])

  const toggle = (key) => onChange({ ...config, [key]: !config[key] })

  return (
    <section className="config-section">
      <button
        className={`config-header ${open ? 'open' : ''}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="config-body"
      >
        <IconChevronRight size={14} />
        <span>比对配置</span>
        <span className="config-hint">
          {dirty ? '配置已修改，尚未应用' : '默认配置已优化'}
        </span>
      </button>

      <div className={`config-body ${open ? 'open' : ''}`} id="config-body">
        {GROUPS.map((group) => (
          <fieldset className="config-group" key={group.title}>
            <legend>{group.title}</legend>
            {group.items.map(([key, label]) => (
              <label className="switch" key={key}>
                <input
                  type="checkbox"
                  checked={!!config[key]}
                  onChange={() => toggle(key)}
                />
                <span>{label}</span>
              </label>
            ))}
          </fieldset>
        ))}

        <div className="config-group">
          <h4>主键列</h4>
          <input
            className="key-input"
            placeholder="用逗号分隔，例如：编号,合同号"
            aria-label="主键列"
            value={(config.primary_key_columns || []).join(',')}
            onChange={(event) =>
              onChange({
                ...config,
                primary_key_columns: event.target.value
                  .split(/[,，]/)
                  .map((item) => item.trim())
                  .filter(Boolean),
              })
            }
          />
          <p className="hint">留空时系统自动识别唯一标识列</p>
        </div>

        <div className="config-actions">
          <button
            onClick={() => onChange({ ...DEFAULT_CONFIG })}
            disabled={!dirty && !onApply}
          >
            <IconRefresh size={15} />
            恢复默认
          </button>
          {onApply && (
            <button className="primary" onClick={() => onApply(config)} disabled={!dirty}>
              应用配置并重新比对
            </button>
          )}
        </div>
      </div>
    </section>
  )
}
