import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  enrollStatus,
  OS_TABS,
  reconnectNode,
  setNodeMeta,
  setNodeTags,
  type EnrollResult,
  type Node,
  type NodeOs,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Props = {
  node: Node
  onClose: () => void
  onDone: () => void
  onUnauthorized: () => void
}

export function ReconnectModal({ node, onClose, onDone, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)
  const [os, setOs] = useState<NodeOs>('linux')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<EnrollResult | null>(null)
  const [newIp, setNewIp] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!result || newIp) return
    let alive = true
    const tick = async () => {
      try {
        const s = await enrollStatus(result.key_id, result.hostname)
        if (alive && s.connected && s.node) {
          // переносим классификацию со старой ноды на новую (у неё новый id)
          const nid = s.node.id
          try {
            await setNodeMeta(nid, {
              description: node.description,
              kind: node.kind,
              admin: node.admin,
              group: node.group,
              subgroup: node.subgroup,
            })
            if (node.forced_tags.length) await setNodeTags(nid, node.forced_tags)
          } catch {
            /* восстановление меты best-effort */
          }
          setNewIp(s.node.ip_addresses.find((ip) => !ip.includes(':')) ?? '—')
          onDone()
        }
      } catch {
        /* временная ошибка — повторим */
      }
    }
    pollRef.current = setInterval(tick, 4000)
    tick()
    return () => {
      alive = false
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [result, newIp, onDone])

  async function start() {
    if (
      !window.confirm(
        t(
          'Переподключить «{name}»? Её текущая запись будет удалена, и после запуска скрипта на ноде она подключится заново с новым IP.',
          { name: node.name },
        ),
      )
    )
      return
    setBusy(true)
    setError(null)
    try {
      setResult(await reconnectNode(node.id, os))
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized()
        return
      }
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    if (!result) return
    try {
      await navigator.clipboard.writeText(result.script)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard недоступен */
    }
  }

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Переподключить «{name}»', { name: node.name })}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        {!result ? (
          <>
            <p className="muted small">
              {t('Нода будет удалена из headscale и получит новый IP из текущего диапазона при повторном подключении. Имя и владелец сохранятся.')}
            </p>
            <div className="field">
              <span>{t('Операционная система')}</span>
              <div className="os-tabs">
                {OS_TABS.map(({ os: o, label }) => (
                  <button
                    key={o}
                    className={`os-tab${os === o ? ' os-tab-active' : ''}`}
                    onClick={() => setOs(o)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {error && <p className="form-error">{error}</p>}
            <div className="modal-actions">
              <button className="ghost" onClick={onClose}>
                {t('Отмена')}
              </button>
              <button onClick={start} disabled={busy}>
                {busy ? t('Готовлю…') : t('Переподключить')}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="muted small">
              {os === 'windows'
                ? t('Выполните скрипт на ноде в PowerShell от администратора. Ключ одноразовый.')
                : os === 'macos'
                  ? t('Выполните скрипт на Mac в Терминале (нужен Homebrew). Ключ одноразовый.')
                  : os === 'android'
                    ? t('На Android скрипта нет — выполните шаги в приложении Tailscale. Ключ одноразовый.')
                    : t('Выполните скрипт на ноде под root. Ключ одноразовый.')}
            </p>
            <pre className="enroll-script">{result.script}</pre>
            <div className="enroll-actions">
              <button onClick={copy}>
                {copied ? t('Скопировано ✓') : t('Скопировать скрипт')}
              </button>
            </div>
            <div className={`enroll-status${newIp ? ' enroll-status-ok' : ''}`}>
              <span className={`dot ${newIp ? 'dot-ok' : 'dot-unknown'}`} />
              <span>
                {newIp
                  ? t('Переподключена ✓ — новый IP: {ip}', { ip: newIp })
                  : t('Ждём переподключения ноды — статус обновится сам…')}
              </span>
            </div>
            <div className="modal-actions">
              <button onClick={onClose}>{newIp ? t('Готово') : t('Закрыть')}</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
