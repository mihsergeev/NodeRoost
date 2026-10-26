import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, loginOptions, setToken } from './api'
import { BrandLockup } from './Logo'
import { useI18n } from './i18n'

type Props = {
  onLogin: () => void
}

export function LoginPage({ onLogin }: Props) {
  const { t } = useI18n()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [needOtp, setNeedOtp] = useState(false)
  // «не спрашивать код на этом устройстве»: сколько дней помнить, решает
  // панель, и ноль означает, что галку показывать незачем
  const [remember, setRemember] = useState(false)
  const [trustDays, setTrustDays] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!needOtp) return
    loginOptions()
      .then((o) => setTrustDays(o.trust_days))
      .catch(() => setTrustDays(0))
  }, [needOtp])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { access_token } = await api<{ access_token: string }>(
        '/api/auth/login',
        {
          method: 'POST',
          body: JSON.stringify({
            username,
            password,
            otp: otp.trim() || null,
            remember,
          }),
        },
      )
      setToken(access_token)
      onLogin()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        if (err.message === '2fa_required') {
          setNeedOtp(true)
          setError(null)
        } else if (err.message === '2fa_invalid') {
          setNeedOtp(true)
          setError(t('Неверный код 2FA'))
        } else {
          setError(t('Неверный логин или пароль'))
        }
      } else {
        setError(err instanceof Error ? err.message : t('Не удалось войти'))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <BrandLockup />
        <h2>{t('Вход в панель')}</h2>
        <label>
          {t('Логин')}
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            disabled={needOtp}
            required
          />
        </label>
        <label>
          {t('Пароль')}
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={needOtp}
            required
          />
        </label>
        {needOtp && (
          <label>
            {t('Код из приложения (2FA)')}
            <input
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              autoComplete="one-time-code"
              inputMode="numeric"
              placeholder="123456"
              autoFocus
              required
            />
          </label>
        )}
        {needOtp && trustDays > 0 && (
          <label className="login-remember">
            <input
              type="checkbox"
              className="field-checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>
              {t('Не спрашивать код на этом устройстве {days} дней', {
                days: trustDays,
              })}
            </span>
          </label>
        )}
        {error && <p className="form-error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? t('Проверка…') : t('Войти')}
        </button>
      </form>
    </div>
  )
}
