import { useLayoutEffect, useRef } from 'react'
import { getToken, resolveHost } from './api'

export function useHostResolution(onClose: () => void) {
  const request = useRef<AbortController | null>(null)

  useLayoutEffect(() => () => request.current?.abort(), [])

  function close() {
    request.current?.abort()
    onClose()
  }

  async function resolve(host: string) {
    if (request.current) return undefined
    const controller = new AbortController()
    const token = getToken()
    const canceled = () => controller.signal.aborted || getToken() !== token
    request.current = controller
    try {
      const result = await resolveHost(host, controller.signal)
      return canceled() ? undefined : result
    } catch (err) {
      if (canceled()) return undefined
      throw err
    } finally {
      request.current = null
    }
  }

  return { close, resolve }
}
