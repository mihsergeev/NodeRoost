import { useEffect, useState } from 'react'
import {
  ApiError,
  downloadBackup,
  getBackupConfig,
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
  const [files, setFiles] = useState<BackupFile[] | null>(null)
  const [cfg, setCfg] = useState<BackupConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  function handle(err: unknown) {
    if (err instanceof ApiError && err.status === 401) onUnauthorized()
    else setError(err instanceof Error ? err.message : t('Ошибка'))
  }

  async function load() {
    try {
      setFiles(await listBackups())
    } catch (err) {
      handle(err)
      setFiles([])
    }
  }

  useEffect(() => {
    load()
    getBackupConfig().then(setCfg).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function create() {
    setBusy(true)
    setError(null)
    setMsg(null)
    try {
      const r = await runBackup()
      if (r.problems.length)
        setError(t('Бэкап создан, но self-тест: {p}', { p: r.problems.join('; ') }))
      else setMsg(t('Бэкап создан ({size}).', { size: fmtSize(r.size) }))
      load()
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  async function saveCfg() {
    if (!cfg) return
    setError(null)
    setMsg(null)
    try {
      setCfg(await putBackupConfig(cfg))
      setMsg(t('Сохранено.'))
    } catch (err) {
      handle(err)
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
          <button onClick={create} disabled={busy}>
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
                  min={1}
                  max={365}
                  style={{ width: '5rem' }}
                  value={cfg.keep}
                  onChange={(e) =>
                    setCfg({ ...cfg, keep: Number(e.target.value) || 1 })
                  }
                />
              </label>
              <button className="ghost" onClick={saveCfg}>
                {t('Сохранить')}
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
