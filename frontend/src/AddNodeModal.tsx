import { useEffect, useEffectEvent, useState } from 'react'
import {
  ApiError,
  getAgent,
  getToken,
  enrollNode,
  enrollStatus,
  OS_TABS,
  setNodeMeta,
  type EnrollResult,
  type NodeOs,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'
import { NODE_NAME_RE, suggestNodeName } from './nodename'

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
  // Имя ноды становится её именем в сети, поэтому латиница, цифры и дефис.
  // Человек об этом не знает и пишет «corp o8 терминал»; вместо отказа показываем
  // годный вариант и даём подставить его одной кнопкой.
  const suggestion = suggestNodeName(name)
  const nameOk = name.trim() === '' || NODE_NAME_RE.test(name.trim().toLowerCase())
  const [os, setOs] = useState<NodeOs>('linux')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<EnrollResult | null>(null)
  const [connected, setConnected] = useState(false)
  // имя старой записи, если машина уже была в сети
  const [reused, setReused] = useState<string | null>(null)
  // команда установки агента — только для серверов и только после подключения:
  // токен привязан к id ноды, а его до регистрации не существует
  const [agentCmd, setAgentCmd] = useState('')
  const [agentCopied, setAgentCopied] = useState(false)
  const [copied, setCopied] = useState(false)
  const [cmdCopied, setCmdCopied] = useState(false)
  const enrolled = useEffectEvent(onEnrolled)
  const unauthorized = useEffectEvent(onUnauthorized)
  // поллинг статуса подключения ноды
  useEffect(() => {
    if (!result) return
    let alive = true
    let request: AbortController | undefined
    const token = getToken()
    const active = () => alive && getToken() === token
    const tick = async () => {
      if (!active() || request) return
      const controller = new AbortController()
      request = controller
      const deadline = setTimeout(() => controller.abort(), 10000)
      try {
        const s = await enrollStatus(result.key_id, result.hostname, controller.signal)
        if (!active() || !s.connected) return
        clearInterval(poll)
        setError(null)
        if (s.node) {
          // Записываем только тип и ждём ответа до обновления списка. При
          // повторном подключении описание, группа и подгруппа сохраняются.
          try {
            await setNodeMeta(s.node.id, { kind })
          } catch (err) {
            if (err instanceof ApiError && err.status === 401) throw err
            if (active() && err instanceof Error) setError(err.message)
          }
        }
        if (!active()) return
        setConnected(true)
        setReused(s.reused_from || null)
        enrolled()
        if (kind === 'server' && s.node) {
          try {
            const agent = await getAgent(s.node.id)
            if (active()) setAgentCmd(agent.setup_oneline)
          } catch (err) {
            if (err instanceof ApiError && err.status === 401) throw err
            /* токен можно получить позже из карточки ноды */
          }
        }
      } catch (err) {
        if (!active() || !(err instanceof ApiError)) return
        if (err.status === 401) unauthorized()
        else if (err.status === 409) setError(err.message)
      } finally {
        clearTimeout(deadline)
        request = undefined
      }
    }
    const poll = setInterval(tick, 4000)
    tick()
    return () => {
      alive = false
      request?.abort()
      clearInterval(poll)
    }
  }, [result, kind])

  async function create() {
    setBusy(true)
    setError(null)
    try {
      setResult(await enrollNode(name, os))
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

  async function copyCmd() {
    if (!result?.join_cmd) return
    try {
      await navigator.clipboard.writeText(result.join_cmd)
      setCmdCopied(true)
      setTimeout(() => setCmdCopied(false), 1500)
    } catch {
      /* clipboard недоступен — выделят вручную */
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
            {!nameOk && (
              <p className="muted small">
                {t('Это имя машины в сети: латинские буквы, цифры и дефис, без пробелов и точек. Человеческое название впишите потом в описание ноды.')}
                {suggestion && (
                  <>
                    {' '}
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => setName(suggestion)}
                    >
                      {t('Назвать «{name}»', { name: suggestion })}
                    </button>
                  </>
                )}
              </p>
            )}
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
              <button onClick={create} disabled={busy || !name.trim()}>
                {busy ? t('Создание…') : t('Создать')}
              </button>
            </div>
          </>
        ) : (
          <>
            {error && <p className="form-error" role="alert">{error}</p>}
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
            </p>
            {os === 'windows' && (
              <p className="muted small">
                {t('PowerShell должен быть запущен ОТ АДМИНИСТРАТОРА: команда ставит клиент Tailscale на машину. Если установка не пройдёт, скрипт назовёт код ошибки и путь к подробному логу.')}
              </p>
            )}
            {(os === 'linux' || os === 'macos') && !result.join_cmd && (
              <p className="muted small">
                {t('Ключ не попадёт в историю шелла — скрипт первой строкой отключает её запись. Ваша история при этом сохраняется. В zsh (macOS) опции нет: там надёжнее сохранить скрипт в файл и запустить.')}
              </p>
            )}
            {result.join_cmd ? (
              <>
                {/* Одна команда вместо простыни: вставленный целиком скрипт
                    консоль выполняет построчно, и падение в середине не
                    останавливает остальное — человек получает каскад вторичных
                    ошибок вместо причины. Команда исполняется как одно целое,
                    а ключ не оседает ни в истории, ни в буфере обмена. */}
                <pre className="enroll-script cmd-oneline">{result.join_cmd}</pre>
                <div className="enroll-actions">
                  <button onClick={copyCmd}>
                    {cmdCopied ? t('Скопировано ✓') : t('Скопировать команду')}
                  </button>
                </div>
                <details className="enroll-full">
                  <summary className="muted small">
                    {t('Показать сам скрипт (если хотите сначала прочитать)')}
                  </summary>
                  <pre className="enroll-script">{result.script}</pre>
                  <div className="enroll-actions">
                    <button className="ghost small" onClick={copy}>
                      {copied ? t('Скопировано ✓') : t('Скопировать скрипт')}
                    </button>
                  </div>
                </details>
              </>
            ) : (
              <>
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
              </>
            )}

            <div className={`enroll-status${connected ? ' enroll-status-ok' : ''}`}>
              <span className={`dot ${connected ? 'dot-ok' : 'dot-unknown'}`} />
              <span>
                {connected
                  ? t('Нода подключена ✓')
                  : t('Ждём подключения ноды — статус обновится сам…')}
              </span>
            </div>

            {connected && reused && (
              <p className="muted small">
                {t('Эта машина уже была в сети под именем')} «{reused}» —
                {' '}{t('её запись переиспользована и переименована. Прежние настройки доступа и маршруты сохранены.')}
              </p>
            )}

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
