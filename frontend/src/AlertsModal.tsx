import { useEffect, useEffectEvent, useState } from 'react'
import { ApiError, getAlerts, getToken, putAlerts, testAlerts, type AlertConfig } from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Props = {
  onClose: () => void
  onUnauthorized: () => void
}

export function AlertsModal({ onClose, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)
  const [token, setToken] = useState('')
  const [chat, setChat] = useState('')
  const [apiUrl, setApiUrl] = useState('')
  const [webhook, setWebhook] = useState('')
  const [loadState, setLoadState] = useState<'loading' | 'loaded' | 'failed'>('loading')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function applyConfig(c: AlertConfig) {
    setToken(c.telegram_token)
    setChat(c.telegram_chat)
    setApiUrl(c.telegram_api)
    setWebhook(c.webhook)
  }

  function handleError(err: unknown) {
    if (err instanceof ApiError && err.status === 401) onUnauthorized()
    else setError(err instanceof Error ? err.message : t('Ошибка'))
  }

  const handleLoadError = useEffectEvent(handleError)
  useEffect(() => {
    const controller = new AbortController()
    getAlerts(controller.signal)
      .then((c) => {
        if (controller.signal.aborted) return
        applyConfig(c)
        setLoadState('loaded')
      })
      .catch((err) => {
        if (controller.signal.aborted) return
        setLoadState('failed')
        handleLoadError(err)
      })
    return () => controller.abort()
  }, [])

  async function save() {
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      applyConfig(await putAlerts({ telegram_token: token, telegram_chat: chat, telegram_api: apiUrl, webhook }))
      setMsg(t('Сохранено.'))
    } catch (err) {
      handleError(err)
    } finally {
      setSaving(false)
    }
  }

  async function runTest() {
    const auth = getToken()
    setTesting(true)
    setError(null)
    setMsg(null)
    try {
      // сначала сохраняем — тест идёт по текущим полям
      const config = await putAlerts({ telegram_token: token, telegram_chat: chat, telegram_api: apiUrl, webhook })
      if (getToken() !== auth) return
      applyConfig(config)
      const r = await testAlerts()
      if (r.sent) setMsg(t('Тестовый алерт отправлен — проверьте канал.'))
      else setError(t('Не отправлено: {err}', { err: r.errors.join('; ') }))
    } catch (err) {
      handleError(err)
    } finally {
      setTesting(false)
    }
  }

  const canTest = Boolean((token && chat) || webhook)
  const busy = saving || testing

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Алерты')}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>
        <p className="muted small">
          {t(
            'Панель следит за нодами и шлёт уведомление, когда нода уходит в офлайн или возвращается, а также когда ключ ноды скоро истекает.',
          )}
        </p>

        {loadState === 'loading' ? (
          <p className="muted">{t('загрузка…')}</p>
        ) : loadState === 'failed' ? (
          <p className="form-error" role="alert">{error}</p>
        ) : (
          <>
            <div className="settings-group">
              <h4>Telegram</h4>
              <p className="muted small">
                {t(
                  'Создайте бота через @BotFather, вставьте его токен и chat_id (свой ID узнаете у @userinfobot).',
                )}
              </p>
              <label className="field">
                <span>{t('Токен бота')}</span>
                <input disabled={busy} value={token} onChange={(e) => setToken(e.target.value)} placeholder="123456:ABC-DEF…" />
                {/* Панель отдаёт сохранённый токен только хвостом: целиком его
                    не покажет никто, включая эту форму. Оставите как есть -
                    сохранится прежний; впишете новый - заменится; очистите -
                    канал выключится. */}
                <span className="muted small">
                  {t('Сохранённый токен показывается хвостом. Оставьте как есть - останется прежний, очистите - канал выключится.')}
                </span>
              </label>
              <label className="field">
                <span>Chat ID</span>
                <input disabled={busy} value={chat} onChange={(e) => setChat(e.target.value)} placeholder="123456789" />
              </label>
              <label className="field">
                <span>{t('Адрес Bot API')}</span>
                <input disabled={busy} value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} placeholder="https://api.telegram.org" />
                <span className="muted small">
                  {t(
                    'Пусто = api.telegram.org. Укажите зеркало/прокси, если Telegram заблокирован в регионе (напр. https://api-tg.example.com).',
                  )}
                </span>
              </label>
            </div>

            <div className="settings-group">
              <h4>{t('Вебхук')}</h4>
              <p className="muted small">
                {t('POST с JSON {"text": "…"} на URL (Slack, Mattermost, свой сервис).')}
              </p>
              <label className="field">
                <span>URL</span>
                <input disabled={busy} value={webhook} onChange={(e) => setWebhook(e.target.value)} placeholder="https://…" />
                <span className="muted small">
                  {t('Сохранённый адрес показывается хвостом. Оставьте как есть - останется прежний, очистите - вебхук выключится.')}
                </span>
              </label>
            </div>

            {error && <p className="form-error">{error}</p>}
            {msg && <p className="form-ok">{msg}</p>}

            <div className="modal-actions">
              <button className="ghost" onClick={runTest} disabled={busy || !canTest}>
                {testing ? t('Отправка…') : t('Проверить')}
              </button>
              <button onClick={save} disabled={busy}>
                {saving ? t('Сохранение…') : t('Сохранить')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
