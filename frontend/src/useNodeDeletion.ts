import { useRef, useState } from 'react'
import { ApiError, deleteNode, type Node } from './api'
import { useI18n } from './i18n'

export type NodeDeletionTarget = Pick<Node, 'id' | 'name' | 'kind' | 'created_at'>
type PendingDeletion = NodeDeletionTarget & { busy?: boolean; error?: string }
const STORAGE_KEY = 'noderoost_pending_deletions'

export function matchesDeletionTarget(target: NodeDeletionTarget, current: Node) {
  return target.created_at ? target.created_at === current.created_at : target.name === current.name
}

function readPending(): PendingDeletion[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    if (!Array.isArray(saved)) return []
    return saved.filter((n): n is NodeDeletionTarget =>
      n !== null && typeof n === 'object' && typeof n.id === 'string' && /^\d+$/.test(n.id) &&
      typeof n.name === 'string' && (n.kind === 'server' || n.kind === 'device') &&
      (typeof n.created_at === 'string' || n.created_at === null),
    ).map(({ id, name, kind, created_at }) => ({ id, name, kind, created_at }))
  } catch {
    return []
  }
}

export function useNodeDeletion(onUnauthorized: () => void) {
  const { t } = useI18n()
  const [pending, setPending] = useState(readPending)
  const latest = useRef(pending)

  function update(id: string, value?: PendingDeletion) {
    latest.current = latest.current.filter((n) => n.id !== id)
    if (value) latest.current.push(value)
    setPending(latest.current)
    try {
      const saved = readPending().filter((n) => n.id !== id)
      if (value) {
        const { name, kind, created_at } = value
        saved.push({ id, name, kind, created_at })
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved))
    } catch {
      // Повтор остаётся доступен до закрытия вкладки.
    }
  }

  async function remove(target: NodeDeletionTarget) {
    const { id, name, kind, created_at } = target
    if (latest.current.some((n) => n.id === id && n.busy)) return false
    const entry = { id, name, kind, created_at }
    update(id, { ...entry, busy: true })
    try {
      await deleteNode(id)
      update(id)
      return true
    } catch (err) {
      update(id, { ...entry, error: err instanceof Error ? err.message : t('Ошибка') })
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      return false
    }
  }

  return { pending, remove }
}
