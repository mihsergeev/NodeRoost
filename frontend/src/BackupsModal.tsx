import { useEffect, useEffectEvent, useRef, useState } from 'react'
import {
  ApiError,
  downloadBackup,
  getBackupConfig,
  getToken,
  listBackups,
  putBackupConfig,
  runBackup,
  type BackupConfig,
  type BackupFile,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

type Props = {
  onClose: () => void
  onUnauthorized: () => void
}

export function BackupsModal({ onClose, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)
  const [files, setFiles] = useState<BackupFile[] | 'error' | null>(null)
  const [cfg, setCfg] = useState<BackupConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const listSequence = useRef(0)

  function handle(err: unknown) {
    if (err instanceof ApiError && err.status === 401) onUnauthorized()
    else setError(err instanceof Error ? err.message : t('Ошибка'))
  }

  async function load() {
    const sequence = ++listSequence.current
    try {
      const next = await listBackups()
      if (sequence === listSequence.current) setFiles(next)
    } catch (err) {
      if (sequence !== listSequence.current) return
      handle(err)
      setFiles('error')
    }
  }

  const loadInitialFiles = useEffectEvent(load)
  const handleLoadError = useEffectEvent(handle)
  useEffect(() => {
    let active = true
    void loadInitialFiles()
    getBackupConfig()
      .then((next) => { if (active) setCfg(next) })
      .catch((err) => { if (active) handleLoadError(err) })
    return () => {
      active = false
      ++listSequence.current
    }
  }, [])

  async function create() {
    const token = getToken()
    setBusy(true)
    setError(null)
    setMsg(null)
    try {
      const r = await runBackup()
      if (getToken() !== token) return
      if (r.problems.length)
        setError(t('Бэкап создан, но self-тест: {p}', { p: r.problems.join('; ') }))
      else setMsg(t('Бэкап создан ({size}).', { size: fmtSize(r.size) }))
      await load()
    } catch (err) {
      if (getToken() === token) handle(err)
    } finally {
      setBusy(false)
    }
  }

  async function saveCfg() {
    if (!cfg) return
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      setCfg(await putBackupConfig(cfg))
      setMsg(t('Сохранено.'))
    } catch (err) {
      handle(err)
    } finally {
      setSaving(false)
    }
  }

  async function dl(name: string) {
    try {
      await downloadBackup(name)
    } catch (err) {
      handle(err)
    }
  }

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Бэкапы')}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>
        <p className="muted small">
          {t(
            'В бэкап входит снимок состояния headscale (база + config + ключи: ноды, пользователи, ключи, ACL) и настройки панели. Метрики/история — нет.',
          )}
        </p>
        <p className="muted small">
          {t(
            'Храните архив как приватный ключ: внутри секрет второго фактора, хеш пароля администратора, токены агентов и ключи control-сервера. Кто получил файл — получил и сеть.',
          )}
        </p>

        <div className="modal-actions" style={{ justifyContent: 'flex-start' }}>
          <button onClick={create} disabled={busy || saving}>
            {busy ? t('Создание…') : t('Создать бэкап сейчас')}
          </button>
        </div>

        {cfg && (
          <div className="settings-group">
            <h4>{t('Автобэкап')}</h4>
            <div className="apikey-create">
              <label className="field-inline">
                <span>{t('Каждые')}</span>
                <select
                  className="select"
                  disabled={busy || saving}
                  value={cfg.interval_hours}
                  onChange={(e) =>
                    setCfg({ ...cfg, interval_hours: Number(e.target.value) })
                  }
                >
                  <option value={0}>{t('выключено')}</option>
                  <option value={6}>6 {t('ч')}</option>
                  <option value={12}>12 {t('ч')}</option>
                  <option value={24}>24 {t('ч')}</option>
                  <option value={72}>72 {t('ч')}</option>
                </select>
              </label>
              <label className="field-inline">
                <span>{t('Хранить копий')}</span>
                <input
                  type="number"
                  disabled={busy || saving}
                  min={1}
                  max={365}
                  style={{ width: '5rem' }}
                  value={cfg.keep}
                  onChange={(e) =>
                    setCfg({ ...cfg, keep: Number(e.target.value) || 1 })
                  }
                />
              </label>
              <button className="ghost" onClick={saveCfg} disabled={busy || saving}>
                {saving ? t('Сохранение…') : t('Сохранить')}
              </button>
            </div>
          </div>
        )}

        {error && <p className="form-error">{error}</p>}
        {msg && <p className="form-ok">{msg}</p>}

        <div className="settings-group">
          <h4>{t('Копии на сервере')}</h4>
          {files === null ? (
            <p className="muted">{t('загрузка…')}</p>
          ) : files === 'error' ? (
            <button className="ghost" onClick={() => { setError(null); setFiles(null); void load() }}>
              {t('Обновить')}
            </button>
          ) : files.length === 0 ? (
            <p className="muted small">{t('Копий пока нет.')}</p>
          ) : (
            <div className="table-scroll">
              <table className="keys-table">
                <thead>
                  <tr>
                    <th>{t('Файл')}</th>
                    <th>{t('Размер')}</th>
                    <th>{t('Дата')}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {files.map((f) => (
                    <tr key={f.filename}>
                      <td className="mono">{f.filename}</td>
                      <td className="muted">{fmtSize(f.size)}</td>
                      <td className="muted">
                        {new Date(f.created).toLocaleString()}
                      </td>
                      <td>
                        <button className="ghost" onClick={() => dl(f.filename)}>
                          {t('Скачать')}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <p className="muted small settings-note">
          {t(
            'Восстановление headscale — вручную: распакуйте архив в data/headscale и перезапустите стек (docker compose up -d).',
          )}
        </p>
      </div>
    </div>
  )
}
