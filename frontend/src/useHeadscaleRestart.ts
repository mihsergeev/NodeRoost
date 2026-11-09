import { useEffect } from 'react'
import { ApiError, getHsInfo } from './api'

export function useHeadscaleRestart(pending: boolean, onApplied: () => void, onUnauthorized: () => void) {
  useEffect(() => {
    if (!pending) return
    let active = true
    let timer = window.setTimeout(check, 4000)

    async function check() {
      try {
        const info = await getHsInfo()
        if (!active) return
        if (info.restart_pending === false) {
          onApplied()
          return
        }
      } catch (err) {
        if (!active) return
        if (err instanceof ApiError && err.status === 401) {
          onUnauthorized()
          return
        }
        // Во время перезапуска GET тоже может быть временно недоступен.
      }
      if (active) timer = window.setTimeout(check, 4000)
    }

    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [pending, onApplied, onUnauthorized])
}
