import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import {
  disable2FA,
  enable2FA,
  forgetAllDevices,
  forgetDevice,
  get2FA,
  getTrustedDevices,
  setTrustDays,
  setup2FA,
  type TrustedDevices,
  type TwoFASetup,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Props = {
  onClose: () => void
  onUnauthorized: () => void
}

export function TwoFAModal({ onClose, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [setup, setSetup] = useState<TwoFASetup | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [otp, setOtp] = useState('')
  // Включение и выключение второго фактора подтверждается паролем: токена мало.
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Запомненные устройства: вводить код по десять раз в день на своей машине -
  // верный способ выключить 2FA совсем, поэтому срок настраивается здесь же.
  const [trusted, setTrusted] = useState<TrustedDevices | null>(null)
  const [daysInput, setDaysInput] = useState('')
  const [devBusy, setDevBusy] = useState(false)

  useEffect(() => {
    get2FA()
      .then((s) => setEnabled(s.enabled))
      .catch(() => onUnauthorized())
  }, [onUnauthorized])

  useEffect(() => {
    if (!enabled) return
    getTrustedDevices()
      .then((d) => {
        setTrusted(d)
        setDaysInput(String(d.trust_days))
      })
      .catch(() => setTrusted(null))
  }, [enabled])

  async function applyDays() {
    const days = Number(daysInput)
    if (!Number.isInteger(days) || days < 0 || days > 365) return
    setDevBusy(true)
    try {
      const d = await setTrustDays(days)
      setTrusted(d)
      setDaysInput(String(d.trust_days))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setDevBusy(false)
    }
  }

  async function dropDevice(id: number) {
    setDevBusy(true)
    try {
      await forgetDevice(id)
      setTrusted(await getTrustedDevices())
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setDevBusy(false)
    }
  }

  async function dropAll() {
    if (!window.confirm(t('Код 2FA будут спрашивать на всех устройствах, включая это. Продолжить?'))) {
      return
    }
    setDevBusy(true)
    try {
      await forgetAllDevices()
      setTrusted(await getTrustedDevices())
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setDevBusy(false)
    }
  }

  useEffect(() => {
    if (!setup) {
      setQr(null)
      return
    }
    let alive = true
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 240 })
      .then((url) => alive && setQr(url))
      .catch(() => alive && setQr(null))
    return () => {
      alive = false
    }
  }, [setup])

  async function startSetup() {
    setBusy(true)
    setError(null)
    try {
      setSetup(await setup2FA(password))
      setOtp('')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setBusy(false)
    }
  }

  async function confirmEnable() {
    setBusy(true)
    setError(null)
    try {
      const s = await enable2FA(otp.trim(), password)
      setEnabled(s.enabled)
      setSetup(null)
      setOtp('')
      setPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Неверный код'))
    } finally {
      setBusy(false)
    }
  }

  async function turnOff() {
    setBusy(true)
    setError(null)
    try {
      const s = await disable2FA(otp.trim(), password)
      setEnabled(s.enabled)
      setOtp('')
      setPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Неверный код'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Двухфакторная аутентификация')}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        {enabled === null ? (
          <p className="muted">{t('загрузка…')}</p>
        ) : enabled ? (
          <>
            <p className="form-ok">
              {t('2FA включена. Вход требует код из приложения.')}
            </p>
            <div className="trusted-box">
              <div className="clients-head">
                <h4>{t('Запомненные устройства')}</h4>
                {trusted && trusted.devices.length > 0 && (
                  <button className="ghost small" onClick={dropAll} disabled={devBusy}>
                    {t('Забыть все')}
                  </button>
                )}
              </div>
              <p className="muted small">
                {t('На запомненном устройстве код не спрашивают, пароль - всегда. С чужой машины вход по-прежнему требует код.')}
              </p>
              <label className="field">
                <span>{t('Помнить устройство, дней (0 - спрашивать всегда)')}</span>
                <div className="trusted-days">
                  <input
                    type="number"
                    min={0}
                    max={365}
                    value={daysInput}
                    onChange={(e) => setDaysInput(e.target.value)}
                  />
                  <button
                    onClick={applyDays}
                    disabled={devBusy || daysInput === String(trusted?.trust_days ?? '')}
                  >
                    {t('Сохранить')}
                  </button>
                </div>
              </label>
              {trusted && trusted.devices.length > 0 ? (
                <ul className="trusted-list">
                  {trusted.devices.map((d) => (
                    <li key={d.id}>
                      <div>
                        <span className="trusted-label">{d.label}</span>
                        {d.id === trusted.current_id && (
                          <>
                            {' '}
                            <span className="tag">{t('это устройство')}</span>
                          </>
                        )}
                        <span className="muted small">
                          {' '}
                          {d.ip} - {t('до {date}', {
                            date: new Date(d.expires_at).toLocaleDateString(),
                          })}
                        </span>
                      </div>
                      <button
                        className="ghost small"
                        onClick={() => dropDevice(d.id)}
                        disabled={devBusy}
                      >
                        {t('Забыть')}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">{t('Пока ни одного - отметьте галку при следующем входе.')}</p>
              )}
            </div>

            <p className="muted small">
              {t('Чтобы отключить, введите текущий пароль и код из приложения-аутентификатора.')}
            </p>
            <label className="field">
              <span>{t('Текущий пароль')}</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <label className="field">
              <span>{t('Код из приложения (2FA)')}</span>
              <input
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                inputMode="numeric"
                placeholder="123456"
              />
            </label>
            {error && <p className="form-error">{error}</p>}
            <div className="modal-actions">
              <button
                className="danger-solid"
                onClick={turnOff}
                disabled={busy || !otp.trim() || !password}
              >
                {busy ? '…' : t('Отключить 2FA')}
              </button>
            </div>
          </>
        ) : setup ? (
          <>
            <p className="muted small">
              {t(
                'Отсканируйте QR в приложении-аутентификаторе (Google Authenticator, Aegis, 1Password) или введите ключ вручную, затем подтвердите кодом.',
              )}
            </p>
            <div className="qr-wrap">
              {qr ? (
                <img src={qr} alt="2FA QR" width={200} height={200} />
              ) : (
                <span className="muted small">{t('генерация QR…')}</span>
              )}
            </div>
            <p className="muted small">
              {t('Ключ:')} <span className="mono">{setup.secret}</span>
            </p>
            <label className="field">
              <span>{t('Код из приложения (2FA)')}</span>
              <input
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                inputMode="numeric"
                placeholder="123456"
                autoFocus
              />
            </label>
            {error && <p className="form-error">{error}</p>}
            <div className="modal-actions">
              <button className="ghost" onClick={() => setSetup(null)}>
                {t('Отмена')}
              </button>
              <button onClick={confirmEnable} disabled={busy || !otp.trim()}>
                {busy ? '…' : t('Включить')}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="muted small">
              {t(
                'Добавьте второй фактор к входу в панель — одноразовый код из приложения-аутентификатора (TOTP).',
              )}
            </p>
            <label className="field">
              <span>{t('Текущий пароль')}</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            {error && <p className="form-error">{error}</p>}
            <div className="modal-actions">
              <button onClick={startSetup} disabled={busy || !password}>
                {busy ? '…' : t('Включить 2FA')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
