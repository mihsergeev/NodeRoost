import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, getPolicyRules, getToken, putPolicyRules, type AclRule, type AclRules } from './api'
import { useI18n } from './i18n'

function validSnapshot(snapshot: AclRules | null) {
  return Array.isArray(snapshot?.rules) && typeof snapshot.revision === 'string' && !!snapshot.revision
}

export function useAclRules(onUnauthorized: () => void) {
  const { t } = useI18n()
  const [snapshot, setSnapshot] = useState<AclRules | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestInFlight = useRef(false)

  const read = useCallback(async (previousError?: string) => {
    const token = getToken()
    try {
      const current = await getPolicyRules()
      if (!validSnapshot(current)) {
        throw new Error(t('Не удалось загрузить правила доступа'))
      }
      setSnapshot(current)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && getToken() !== token) return
      setSnapshot(null)
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else {
        const loadError = err instanceof Error ? err.message : t('Не удалось загрузить правила доступа')
        setError(previousError ? previousError + ' ' + loadError : loadError)
      }
    }
  }, [onUnauthorized, t])

  const reload = useCallback(async () => {
    if (requestInFlight.current) return
    requestInFlight.current = true
    setLoading(true)
    setError(null)
    try {
      await read()
    } finally {
      requestInFlight.current = false
      setLoading(false)
    }
  }, [read])

  useEffect(() => {
    void reload()
  }, [reload])

  async function save(next: AclRule[]) {
    if (!snapshot || requestInFlight.current) return
    const token = getToken()
    requestInFlight.current = true
    setSaving(true)
    setError(null)
    try {
      const updated = await putPolicyRules(next, snapshot.revision)
      if (validSnapshot(updated)) setSnapshot(updated)
      else {
        setSnapshot(null)
        setError(t('Не удалось загрузить правила доступа'))
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        if (getToken() === token) onUnauthorized()
      } else if (err instanceof ApiError && err.status === 409) {
        const conflict = t('Правила доступа изменились. Проверьте актуальные правила и повторите правку.')
        setSnapshot(null)
        setError(conflict)
        await read(conflict)
      } else setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      requestInFlight.current = false
      setSaving(false)
    }
  }

  return { rules: snapshot?.rules ?? null, loading, saving, error, reload, save }
}
