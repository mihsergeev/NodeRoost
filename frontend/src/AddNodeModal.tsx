import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  getAgent,
  enrollNode,
  enrollStatus,
  OS_TABS,
  setNodeMeta,
  type EnrollResult,
  type NodeOs,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Props = {
  kind: 'server' | 'device'
  onClose: () => void
  onEnrolled: () => void
  onUnauthorized: () => void
}

export function AddNodeModal({ kind, onClose, onEnrolled, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)
  const [name, setName] = useState('')
  const [os, setOs] = useState<NodeOs>('linux')
  const [exitNode, setExitNode] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<EnrollResult | null>(null)
  const [connected, setConnected] = useState(false)
  // команда установки агента — только для серверов и только после подключения:
  // токен привязан к id ноды, а его до регистрации не существует
  const [agentCmd, setAgentCmd] = useState('')
  const [agentCopied, setAgentCopied] = useState(false)
  const [copied, setCopied] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // поллинг статуса подключения ноды
  useEffect(() => {
    if (!result || connected) return
    let alive = true
    const tick = async () => {
      try {
        const s = await enrollStatus(result.key_id, result.hostname)
        if (alive && s.connected) {
          setConnected(true)
          // Тип ноды закрепляем ДО обновления списка и ждём ответа: иначе список
          // успевает перечитаться раньше, чем мета запишется, и свежий сервер
          // уезжает в «Устройства» по авто-определению.
          if (s.node) {
            try {
              await setNodeMeta(s.node.id, { description: '', kind })
            } catch {
              /* тип проставится вручную — не мешаем показать «подключена» */
            }
            if (kind === 'server') {
              try {
                setAgentCmd((await getAgent(s.node.id)).setup_oneline)
              } catch {
                /* не смогли выдать токен — поставят из «Маршрутов» позже */
              }
            }
          }
          onEnrolled()
        }
      } catch {
        /* временная ошибка поллинга — игнорируем, попробуем снова */
      }
    }
    pollRef.current = setInterval(tick, 4000)
    tick()
    return () => {
      alive = false
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [result, connected, kind, onEnrolled])

  async function create() {
    setBusy(true)
    setError(null)
    try {
      setResult(await enrollNode(name, os, exitNode))
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
      /* clipboard недоступен — пользователь выделит вручную */
    }
  }

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>
            {result
              ? t('Скрипт подключения готов')
              : kind === 'server'
                ? t('Добавить сервер')
                : t('Добавить устройство')}
          </h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        {!result ? (
          <>
            <label className="field">
              <span>{t('Имя ноды')}</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={kind === 'server' ? 'web-1' : 'laptop-vasya'}
                autoFocus
              />
            </label>
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
            {kind === 'server' && os === 'linux' && (
              <label className="route-row exit-opt">
                <input
                  type="checkbox"
                  checked={exitNode}
                  onChange={(e) => setExitNode(e.target.checked)}
                />
                <span className="route-cidr">{t('Exit-нода')}</span>
                <span className="muted small">
                  {t('клиенты смогут выходить в интернет через неё; скрипт включит и закрепит ip_forward. Одобрить exit в «Маршрутах» после подключения.')}
                </span>
              </label>
            )}
            {error && <p className="form-error">{error}</p>}
            <div className="modal-actions">
              <button className="ghost" onClick={onClose}>
                {t('Отмена')}
              </button>
              <button onClick={create} disabled={busy || !name.trim()}>
                {busy ? t('Создание…') : t('Создать')}
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
            <p className="muted small">
              {t('Пока создан только одноразовый ключ. Нода появится в списке, когда подключится.')}
              {exitNode && ' ' + t('Затем одобри exit-маршрут в «Маршрутах».')}
            </p>
            {(os === 'linux' || os === 'macos') && (
              <p className="muted small">
                {t('Ключ не попадёт в историю шелла — скрипт первой строкой отключает её запись. Ваша история при этом сохраняется. В zsh (macOS) опции нет: там надёжнее сохранить скрипт в файл и запустить.')}
              </p>
            )}
            <pre className="enroll-script">{result.script}</pre>
            <div className="enroll-actions">
              <button onClick={copy}>
                {copied
                  ? t('Скопировано ✓')
                  : os === 'android'
                    ? t('Скопировать')
                    : t('Скопировать скрипт')}
              </button>
            </div>

            <div className={`enroll-status${connected ? ' enroll-status-ok' : ''}`}>
              <span className={`dot ${connected ? 'dot-ok' : 'dot-unknown'}`} />
              <span>
                {connected
                  ? t('Нода подключена ✓')
                  : t('Ждём подключения ноды — статус обновится сам…')}
              </span>
            </div>

            {connected && agentCmd && (
              <div className="exit-setup">
                <p className="muted small">
                  {t('Чтобы управлять маршрутами этого сервера из панели, поставьте на него агента. Выполните на СЕРВЕРЕ под root, один раз — иначе панель сможет только показывать маршруты, но не применять их.')}
                </p>
                <pre className="enroll-script cmd-oneline">{agentCmd}</pre>
                <div className="enroll-actions">
                  <button
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(agentCmd)
                        setAgentCopied(true)
                        setTimeout(() => setAgentCopied(false), 1500)
                      } catch {
                        /* clipboard недоступен — выделят вручную */
                      }
                    }}
                  >
                    {agentCopied ? t('Скопировано ✓') : t('Скопировать команду установки')}
                  </button>
                </div>
              </div>
            )}

            <div className="modal-actions">
              <button onClick={onClose}>
                {connected ? t('Готово') : t('Закрыть')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
