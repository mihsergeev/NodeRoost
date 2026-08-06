import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  getAgent,
  resolveHost,
  setAgent,
  setNodeRoutes,
  type AgentCfg,
  type Node,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Props = {
  node: Node
  onClose: () => void
  onSaved: () => void
  onUnauthorized: () => void
}

const EXIT = ['0.0.0.0/0', '::/0']

export function RoutesModal({ node, onClose, onSaved, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)

  const [agent, setAgentCfg] = useState<AgentCfg | null>(null)
  const [dest, setDest] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState('')

  // маршруты, которые нода анонсирует САМА (не через агента) — их по-прежнему
  // одобряем галками, чтобы ручной сценарий не сломался
  const advertised = node.available_routes.filter((r) => !EXIT.includes(r))
  const [checked, setChecked] = useState<Set<string>>(
    () => new Set(node.approved_routes.filter((r) => advertised.includes(r))),
  )

  // Пока агент не отозвался, опрашиваем чаще: карточка должна переключиться
  // сама, как только он установлен.
  const load = useCallback(() => {
    getAgent(node.id)
      .then(setAgentCfg)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) onUnauthorized()
      })
  }, [node.id, onUnauthorized])

  useEffect(() => {
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [load])

  function handle(err: unknown) {
    if (err instanceof ApiError && err.status === 401) onUnauthorized()
    else setError(err instanceof Error ? err.message : t('Ошибка'))
  }

  async function saveAgent(routes: string[], exitNode: boolean) {
    setBusy(true)
    setError(null)
    try {
      setAgentCfg(await setAgent(node.id, routes, exitNode))
      onSaved()
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  async function addRoute() {
    const raw = dest.trim()
    if (!raw || !agent) return
    setBusy(true)
    setError(null)
    try {
      let cidr = raw
      if (!/^[\d.]+(\/\d+)?$/.test(raw)) {
        // ввели домен — резолвим в IPv4: маршруты работают по адресам, не по именам
        const r = await resolveHost(raw)
        if (!r.ips.length) throw new Error(r.note || t('не удалось определить адрес'))
        cidr = r.ips[0]
      }
      if (!cidr.includes('/')) cidr += '/32'
      if (agent.routes.includes(cidr)) {
        setDest('')
        return
      }
      await saveAgent([...agent.routes, cidr], agent.exit_node)
      setDest('')
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  async function copy(text: string, tag: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(tag)
      setTimeout(() => setCopied(''), 1500)
    } catch {
      /* clipboard недоступен — выделят вручную */
    }
  }

  async function saveApprovals() {
    setBusy(true)
    setError(null)
    try {
      const approved = [...checked]
      if (node.is_exit_node) approved.push(...EXIT.filter((e) => node.available_routes.includes(e)))
      await setNodeRoutes(node.id, approved)
      onSaved()
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  // маршруты, анонсированные вручную (не заказанные через панель)
  const manual = advertised.filter((r) => !agent?.routes.includes(r))

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Маршруты · {name}', { name: node.name })}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        {!agent ? (
          <p className="muted small">{t('загрузка…')}</p>
        ) : !agent.installed ? (
          <div className="exit-setup">
            <p className="muted small">
              {t('Чтобы управлять маршрутами прямо отсюда, поставьте на ноду агента — он раз в минуту забирает настройки из панели и применяет их сам. Выполните на ноде под root один раз:')}
            </p>
            <pre className="enroll-script cmd-oneline">{agent.setup_oneline}</pre>
            <div className="enroll-actions">
              <button onClick={() => copy(agent.setup_oneline, 'setup')}>
                {copied === 'setup' ? t('Скопировано ✓') : t('Скопировать команду установки')}
              </button>
            </div>
            <p className="muted small">
              {t('Статус обновится сам, как только агент отзовётся.')}
            </p>
          </div>
        ) : (
          <>
            <div className="agent-state agent-state-ok">
              <span className="dot dot-ok" />
              <span>{t('Агент установлен — настройки применяются автоматически')}</span>
            </div>

            {/* Агент от прошлого релиза молча не умеет нового: выглядит это как
                «включил в панели — ничего не произошло». Свежие обновляются сами,
                но самому этому умению тоже надо один раз доехать до ноды. */}
            {!agent.script_current && (
              <div className="exit-setup">
                <p className="muted small">
                  {t('На ноде агент от прошлого релиза — новых возможностей панели он не понимает. Свежий обновляется сам; этому нужно помочь один раз, выполнив на ноде под root:')}
                </p>
                <pre className="enroll-script cmd-oneline">{agent.setup_oneline}</pre>
                <div className="enroll-actions">
                  <button onClick={() => copy(agent.setup_oneline, 'setup')}>
                    {copied === 'setup' ? t('Скопировано ✓') : t('Скопировать команду установки')}
                  </button>
                </div>
              </div>
            )}

            <label className="route-row">
              <input
                type="checkbox"
                checked={agent.exit_node}
                disabled={busy}
                onChange={(e) => saveAgent(agent.routes, e.target.checked)}
              />
              <span className="route-cidr">{t('Exit-node')}</span>
              <span className="muted small">
                {t('ВСЁ или НИЧЕГО: клиент, выбравший эту ноду, гонит через неё весь свой трафик. «Только определённые сайты через VPN» так не делается — для этого маршрут ниже.')}
              </span>
            </label>

            <p className="muted small route-section">
              {t('Через эту ноду ходить только на эти адреса (остальной трафик клиента — напрямую):')}
            </p>
            {agent.routes.length === 0 ? (
              <p className="muted small">{t('маршрутов нет')}</p>
            ) : (
              <div className="ent-chips">
                {agent.routes.map((r) => (
                  <span key={r} className="ent-chip mono">
                    {r}
                    {node.subnet_routes.includes(r) && <span className="tag-chip">{t('активен')}</span>}
                    <button
                      className="chip-x"
                      disabled={busy}
                      aria-label={t('Убрать')}
                      onClick={() => saveAgent(agent.routes.filter((x) => x !== r), agent.exit_node)}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="route-input-row">
              <input
                value={dest}
                onChange={(e) => setDest(e.target.value)}
                placeholder="203.0.113.7, 10.0.0.0/24, myip.ru"
                onKeyDown={(e) => e.key === 'Enter' && addRoute()}
              />
              <button className="ghost small" onClick={addRoute} disabled={busy || !dest.trim()}>
                {busy ? t('…') : t('+ Маршрут')}
              </button>
            </div>
            <p className="muted small">
              {t('Нода применит изменения в течение минуты, одобрение произойдёт само.')}
            </p>

            {agent.remove_oneline && (
              <details className="cmd-raw">
                <summary className="muted small">{t('Снять агента с ноды')}</summary>
                <pre className="enroll-script cmd-oneline">{agent.remove_oneline}</pre>
                <div className="enroll-actions">
                  <button className="ghost small" onClick={() => copy(agent.remove_oneline, 'rm')}>
                    {copied === 'rm' ? t('Скопировано ✓') : t('Скопировать команду')}
                  </button>
                </div>
              </details>
            )}
          </>
        )}

        {manual.length > 0 && (
          <>
            <p className="muted small route-section">
              {t('Нода анонсирует сама (не из панели) — одобрить:')}
            </p>
            {manual.map((r) => (
              <label key={r} className="route-row">
                <input
                  type="checkbox"
                  checked={checked.has(r)}
                  onChange={() =>
                    setChecked((prev) => {
                      const next = new Set(prev)
                      next.has(r) ? next.delete(r) : next.add(r)
                      return next
                    })
                  }
                />
                <span className="route-cidr mono">{r}</span>
                {node.subnet_routes.includes(r) && <span className="tag-chip">{t('активен')}</span>}
              </label>
            ))}
            <div className="modal-actions">
              <button onClick={saveApprovals} disabled={busy}>
                {busy ? t('Сохранение…') : t('Сохранить')}
              </button>
            </div>
          </>
        )}

        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>
      </div>
    </div>
  )
}
