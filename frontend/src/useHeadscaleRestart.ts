import { useEffect } from 'react'
import { ApiError, getHsInfo, getToken } from './api'

const POLL_INTERVAL = 4000
const REQUEST_TIMEOUT = 10000

export function useHeadscaleRestart(pending: boolean, onApplied: () => void, onUnauthorized: () => void) {
  useEffect(() => {
    if (!pending) return
    let active = true
    let timer = window.setTimeout(check, POLL_INTERVAL)
    let request: AbortController | undefined
    let deadline: number | undefined

    async function check() {
      const controller = new AbortController()
      const token = getToken()
      request = controller
      deadline = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT)
      try {
        const info = await getHsInfo(controller.signal)
        if (!active) return
        if (info.restart_pending === false) {
          onApplied()
          return
        }
      } catch (err) {
        if (!active) return
        if (err instanceof ApiError && err.status === 401 && getToken() === token) {
          onUnauthorized()
          return
        }
        // Во время перезапуска GET тоже может быть временно недоступен.
      } finally {
        window.clearTimeout(deadline)
        request = undefined
      }
      if (active) timer = window.setTimeout(check, POLL_INTERVAL)
    }

    return () => {
      active = false
      window.clearTimeout(timer)
      window.clearTimeout(deadline)
      request?.abort()
    }
  }, [pending, onApplied, onUnauthorized])
}
