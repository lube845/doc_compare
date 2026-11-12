import { useCallback, useEffect, useMemo, useState } from 'react'
import { getOcrSettings, testOcrSettings, updateOcrSettings } from '../api'
import { IconCheck, IconRefresh, IconSettings } from './Icons'

/** The channels, in the order the backend offers them. */
const FALLBACK_PROVIDERS = [
  { name: 'baidu', display_name: '百度智能云', description: '', fields: [] },
  { name: 'tencent', display_name: '腾讯云', description: '', fields: [] },
  { name: 'custom', display_name: '通用 HTTP 接口', description: '', fields: [] },
]

/** The values of one channel, with the defaults of its fields. */
function defaultsFor(provider) {
  const values = {}
  ;(provider?.fields || []).forEach((field) => {
    values[field.key] = field.default || ''
  })
  return values
}

/**
 * The settings of the deployment. Today this is the OCR channel: the user
 * picks a provider here and enters its credentials, so that switching
 * providers does not need a rebuild of the image.
 */
export default function SettingsPage() {
  const [settings, setSettings] = useState(null)
  const [providers, setProviders] = useState(FALLBACK_PROVIDERS)
  const [provider, setProvider] = useState('baidu')
  const [values, setValues] = useState({})
  const [secretsSet, setSecretsSet] = useState({})
  const [clearSecrets, setClearSecrets] = useState({})
  const [dpi, setDpi] = useState(200)
  const [maxPages, setMaxPages] = useState(100)
  const [source, setSource] = useState('environment')
  //: the configuration currently in the form passed the connection test
  const [tested, setTested] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getOcrSettings()
      const list = data.providers?.length ? data.providers : FALLBACK_PROVIDERS
      // An empty provider means "switched off" once the user saved it; before
      // that the interface opens on the default channel.
      const selected =
        data.provider || (data.source === 'environment' ? 'baidu' : '')
      const found = list.find((item) => item.name === selected)
      setProviders(list)
      setProvider(selected)
      setValues({ ...defaultsFor(found), ...(data.values || {}) })
      setSecretsSet(data.secret_keys_set || {})
      setClearSecrets({})
      setDpi(data.dpi ?? 200)
      setMaxPages(data.max_pages ?? 100)
      setSource(data.source || 'environment')
      // A saved configuration that was verified can be saved again untouched.
      setTested(Boolean(data.verified))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const current = useMemo(
    () => providers.find((item) => item.name === provider) || providers[0],
    [providers, provider],
  )

  const choose = useCallback(
    (name) => {
      const found = providers.find((item) => item.name === name)
      setProvider(name)
      setValues({ ...defaultsFor(found), ...(name === provider ? values : {}) })
      setSecretsSet(name === provider ? secretsSet : {})
      setClearSecrets({})
      setTested(false)
      setMessage(null)
      setError('')
    },
    [providers, provider, values, secretsSet],
  )

  const setValue = useCallback((key, value) => {
    setValues((previous) => ({ ...previous, [key]: value }))
    setTested(false)
    if (value) {
      setClearSecrets((previous) => ({ ...previous, [key]: false }))
    }
  }, [])

  const payload = useMemo(() => ({
    provider,
    values,
    clear_secrets: Object.entries(clearSecrets)
      .filter(([, value]) => value)
      .map(([key]) => key),
    dpi: Number(dpi),
    max_pages: Number(maxPages),
  }), [provider, values, clearSecrets, dpi, maxPages])

  const save = useCallback(async () => {
    setSaving(true)
    setMessage(null)
    setError('')
    try {
      const data = await updateOcrSettings(payload)
      // The secret fields are emptied after a save: an empty secret keeps
      // the stored one, and the interface never shows the value.
      const found = data.providers?.find((item) => item.name === data.provider)
      setValues({ ...defaultsFor(found), ...(data.values || {}) })
      setSecretsSet(data.secret_keys_set || {})
      setClearSecrets({})
      setSource(data.source)
      setTested(Boolean(data.verified))
      setMessage(data.enabled ? '已保存，扫描件将使用该渠道识别' : '已保存')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }, [payload])

  const test = useCallback(async () => {
    setTesting(true)
    setMessage(null)
    setError('')
    setTested(false)
    try {
      const result = await testOcrSettings(payload)
      if (result.ok) {
        setTested(true)
        setMessage(result.message)
      } else {
        setError(result.message)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setTesting(false)
    }
  }, [payload])

  const enabled = Boolean(
    provider && (current?.fields || []).every((field) => {
      if (!field.required) return true
      if (field.secret) return secretsSet[field.key] || values[field.key]
      return values[field.key]
    }),
  )

  const renderField = (field) => {
    const value = values[field.key] ?? ''
    const id = `ocr-${provider}-${field.key}`
    const control = () => {
      if (field.kind === 'select') {
        return (
          <select id={id} value={value} onChange={(event) => setValue(field.key, event.target.value)}>
            {field.options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        )
      }
      if (field.kind === 'textarea') {
        return (
          <textarea
            id={id}
            rows={field.key === 'box_map' || field.key === 'body' ? 3 : 2}
            placeholder={
              field.secret && secretsSet[field.key] && !value
                ? '已保存，留空表示不修改'
                : field.placeholder
            }
            value={value}
            onChange={(event) => setValue(field.key, event.target.value)}
          />
        )
      }
      return (
        <input
          id={id}
          type={field.secret ? 'password' : 'text'}
          autoComplete="off"
          placeholder={
            field.secret && secretsSet[field.key] && !value ? '已保存，留空表示不修改' : field.placeholder
          }
          value={value}
          onChange={(event) => setValue(field.key, event.target.value)}
        />
      )
    }

    return (
      <div className="settings-field" key={field.key}>
        <label htmlFor={id}>{field.label}</label>
        {control()}
        {field.secret && secretsSet[field.key] && (
          <label className="switch">
            <input
              type="checkbox"
              checked={Boolean(clearSecrets[field.key])}
              onChange={(event) => {
                setClearSecrets((previous) => ({ ...previous, [field.key]: event.target.checked }))
                setTested(false)
              }}
            />
            <span>清除已保存的 {field.label}</span>
          </label>
        )}
        {field.hint && <p className="hint">{field.hint}</p>}
      </div>
    )
  }

  return (
    <div className="page-body">
      <div className="settings-container">
        <header className="settings-header">
          <h1>
            <IconSettings size={20} />
            设置
          </h1>
          <p>配置扫描件识别等运行参数，保存后立即生效</p>
        </header>

        {loading ? (
          <div className="settings-section">
            <p className="hint">正在读取设置…</p>
          </div>
        ) : (
          <section className="settings-section">
            <h2>扫描件 OCR 识别</h2>
            <p className="hint">
              扫描件（没有文本层的 PDF）会被渲染成图片交给识别渠道，识别结果带坐标，
              因此扫描件也能参与比对，页面上也能高亮差异位置。
            </p>

            <label className="switch">
              <input
                type="checkbox"
                checked={Boolean(provider)}
                onChange={(event) => {
                  if (event.target.checked) {
                    choose(providers[0]?.name || 'baidu')
                  } else {
                    setProvider('')
                    setValues({})
                    setSecretsSet({})
                    setClearSecrets({})
                    setTested(true)
                    setMessage(null)
                    setError('')
                  }
                }}
              />
              <span>启用扫描件 OCR 识别</span>
            </label>

            {!provider && (
              <p className="hint">
                未启用：扫描版 PDF 不会调用任何识别服务，上传时会提示为扫描件。
              </p>
            )}

            {provider && (
            <div className="provider-list">
              {providers.map((item) => (
                <label
                  className={`provider-card ${provider === item.name ? 'is-active' : ''}`}
                  key={item.name}
                >
                  <input
                    type="radio"
                    name="ocr-provider"
                    checked={provider === item.name}
                    onChange={() => choose(item.name)}
                  />
                  <span>
                    <strong>{item.display_name}</strong>
                    <small>{item.description}</small>
                  </span>
                </label>
              ))}
            </div>
            )}

            {provider && current?.fields.map(renderField)}

            <div className="settings-row">
              <div className="settings-field">
                <label htmlFor="ocr-dpi">渲染分辨率（DPI）</label>
                <input
                  id="ocr-dpi"
                  type="number"
                  min="50"
                  max="600"
                  step="10"
                  value={dpi}
                  onChange={(event) => setDpi(event.target.value)}
                />
                <p className="hint">越高越准也越慢</p>
              </div>

              <div className="settings-field">
                <label htmlFor="ocr-max-pages">单文件最多识别页数</label>
                <input
                  id="ocr-max-pages"
                  type="number"
                  min="1"
                  max="5000"
                  step="10"
                  value={maxPages}
                  onChange={(event) => setMaxPages(event.target.value)}
                />
                <p className="hint">控制费用，超出的页不识别</p>
              </div>
            </div>

            {provider && (
              <p className="hint">修改配置后需要先「测试连接」，测试通过才能保存。</p>
            )}

            <div className="settings-actions">
              <button
                className="primary"
                onClick={save}
                disabled={saving || (Boolean(provider) && !tested)}
                title={tested ? '' : '请先测试连接，通过后才能保存'}
              >
                {saving ? '保存中…' : '保存设置'}
              </button>
              <button onClick={test} disabled={testing || !enabled || !provider}>
                <IconRefresh size={15} />
                {testing ? '测试中…' : '测试连接'}
              </button>
              <span className="settings-status">
                {enabled ? '已启用' : '未启用'} ·{' '}
                {source === 'database' ? '来自界面配置' : '来自部署配置'}
              </span>
            </div>

            {message && (
              <div className="settings-status ok" role="status">
                <IconCheck size={14} />
                {message}
              </div>
            )}
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
          </section>
        )}

        <section className="settings-section">
          <h2>关于识别服务</h2>
          <p className="hint">
            识别由所选渠道的 OCR 服务完成，需要先在对应控制台开通服务并创建密钥。
            识别失败不会中断比对：该页会标记为未参与比对，原因会显示在比对结果中。
            凭证只保存在服务端，界面上不会回显。
          </p>
        </section>
      </div>
    </div>
  )
}
