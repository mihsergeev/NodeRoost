import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  addDirection,
  delDirection,
  getAgent,
  getDirections,
  listNodes,
  refreshDirections,
  type Direction,
  type Node,
} from './api'
import { useI18n } from './i18n'
import { portLabel } from './aclui'

type Props = { onUnauthorized: () => void }

// Сайт — это почти всегда 80 И 443 сразу: по 80 идёт редирект на https, по 443
// сам сайт. Раздельные пункты заставляли либо заводить два направления, либо
// открывать всё. SSH тут не предлагаем: направление — про «ходить на сайт или
// сервис», а не про административный доступ. Нестандартный порт — вручную.
const PORT_PRESETS = ['*', '80,443', '443']

export function RoutingPage({ onUnauthorized }: Props) {
  const { t } = useI18n()
  const [dirs, setDirs] = useState<Direction[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [srcKind, setSrcKind] = useState<'node' | 'devices' | 'servers'>('node')
  const [src, setSrc] = useState<string[]>([])
  const [dst, setDst] = useState('')
  const [via, setVia] = useState('')
  const [ports, setPorts] = useState('*')
  const [customPort, setCustomPort] = useState('')
  const [filter, setFilter] = useState('')
  const [srcSearch, setSrcSearch] = useState('')
  // свёрнутые группы направлений: при нескольких правилах на одно устройство
  // имя повторялось в каждой строке и таблица росла без пользы
  const [folded, setFolded] = useState<Set<string>>(new Set())
  const toggleFold = (k: string) =>
    setFolded((prev) => {
      const next = new Set(prev)
      next.has(k) ? next.delete(k) : next.add(k)
      return next
    })
  // команда установки агента для конкретной ноды — показываем по клику на бейдж
  const [fix, setFix] = useState<{ node: string; cmd: string } | null>(null)
  const [copied, setCopied] = useState(false)

  const handle = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setError(err instanceof Error ? err.message : t('Ошибка'))
    },
    [onUnauthorized, t],
  )

  const load = useCallback(async () => {
    try {
      const [d, n] = await Promise.all([getDirections(), listNodes()])
      setDirs(d.directions)
      setNodes(n)
    } catch (err) {
      handle(err)
    } finally {
      setLoading(false)
    }
  }, [handle])

  useEffect(() => {
    load()
    // маршрут становится активным только когда агент его применит (до минуты) —
    // подтягиваем сами, чтобы не заставлять жать F5
    const id = setInterval(load, 20000)
    return () => clearInterval(id)
  }, [load])

  const nodeName = (id: string) => nodes.find((n) => n.id === id)?.name || id
  const portText = (p: string) =>
    p === '*'
      ? t('любой порт')
      : p === '80,443'
        ? t('сайт (80 и 443)')
        : p === '443'
          ? t('только HTTPS (443)')
          : portLabel(p, t)
  // через устройство ходить нельзя — это был бы чужой трафик через личную
  // машину пользователя; бэкенд это тоже проверяет, здесь просто не показываем
  // ноду-выход из источников убирает и бэкенд, но в списке её лучше сразу гасить
  const exits = nodes.filter((n) => n.kind === 'server')
  const srcLabel = (d: Direction) =>
    d.src_kind === 'devices'
      ? t('все устройства')
      : d.src_kind === 'servers'
        ? t('все серверы')
        : d.src.length === 1
          ? nodeName(d.src[0])
          : t('{n} нод', { n: d.src.length })
  // ноду-выход в источники не предлагаем: бэкенд её оттуда всё равно выкинет
  const pickable = (() => {
    const q = srcSearch.trim().toLowerCase()
    const list = nodes.filter((n) => n.id !== via)
    return q ? list.filter((n) => n.name.toLowerCase().includes(q)) : list
  })()
  const shown = filter.trim()
    ? dirs.filter((d) =>
        `${srcLabel(d)} ${d.dst} ${nodeName(d.via)}`
          .toLowerCase()
          .includes(filter.trim().toLowerCase()),
      )
    : dirs
  const grouped = (() => {
    const map = new Map<string, Direction[]>()
    for (const d of shown) {
      const k = srcLabel(d)
      const list = map.get(k)
      list ? list.push(d) : map.set(k, [d])
    }
    return [...map.entries()]
  })()

  // готовность к отправке + живая итоговая строка «кто → куда через кого»
  const hasWho = srcKind !== 'node' || src.length > 0
  const canBuild = hasWho && !!dst.trim() && !!via
  const whoSummary =
    srcKind === 'devices'
      ? t('все устройства')
      : srcKind === 'servers'
        ? t('все серверы')
        : src.length <= 3
          ? src.map(nodeName).join(', ')
          : t('{n} нод', { n: src.length })
  const dstSummary = dst.trim()
  const portSummary = portText(ports === 'custom' ? customPort.trim() || '…' : ports)
  const viaSummary = via ? nodeName(via) : ''

  async function add() {
    if ((srcKind === 'node' && !src.length) || !dst.trim() || !via) return
    setBusy(true)
    setError(null)
    try {
      await addDirection(srcKind, src, dst.trim(), via, ports === 'custom' ? customPort.trim() : ports)
      setDst('')
      await load()
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    setBusy(true)
    setError(null)
    try {
      await delDirection(id)
      await load()
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  // Панель видит, чего не хватает, — значит она же должна показывать, чем это
  // лечится. Токен агента выдаётся тем же эндпоинтом, что и в модалке маршрутов.
  async function showFix(viaId: string) {
    setError(null)
    try {
      const a = await getAgent(viaId)
      setFix({ node: nodeName(viaId), cmd: a.setup_oneline })
    } catch (err) {
      handle(err)
    }
  }

  async function copyFix() {
    if (!fix) return
    try {
      await navigator.clipboard.writeText(fix.cmd)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard недоступен — выделят вручную */
    }
  }

  async function recheck() {
    setBusy(true)
    setError(null)
    try {
      setDirs((await refreshDirections()).directions)
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="clients-head">
        <h3>{t('Маршрутизация')}</h3>
        <button className="ghost small" onClick={recheck} disabled={busy || !dirs.length}>
          {t('Проверить адреса')}
        </button>
      </div>

      <p className="muted small">
        {t('Заставляет выбранное устройство ходить на конкретный адрес через конкретную ноду. Остальной трафик устройства идёт напрямую — это не exit-нода, через которую уходит всё.')}
      </p>

      <div className="dir-builder">
        {/* 1 · Кто ходит — подлежащее идёт первым, а не под кнопкой */}
        <div className="dir-step dir-who">
          <div className="dir-step-head">
            <span className="dir-step-label">{t('1 · Кто ходит')}</span>
            <button
              className="ghost small"
              onClick={() => {
                setSrcKind('node')
                setSrc(src.length ? [] : pickable.map((n) => n.id))
              }}
            >
              {src.length ? t('снять все') : t('отметить все')}
            </button>
          </div>
          <input
            className="search-box"
            value={srcSearch}
            onChange={(e) => setSrcSearch(e.target.value)}
            placeholder={t('поиск ноды…')}
          />
          <div className="pick-list dir-pick-list">
            {pickable.length === 0 ? (
              <p className="muted small">{t('Под фильтр ничего не подошло.')}</p>
            ) : (
              pickable.map((n) => (
                <label key={n.id} className="pick-row">
                  <input
                    type="checkbox"
                    checked={srcKind === 'node' && src.includes(n.id)}
                    onChange={(e) => {
                      setSrcKind('node')
                      setSrc((prev) =>
                        e.target.checked ? [...prev, n.id] : prev.filter((x) => x !== n.id),
                      )
                    }}
                  />
                  <span className={`ent-dot ent-${n.kind === 'server' ? 'node' : 'any'}`} />
                  <span className="pick-label">{n.name}</span>
                  <span className="muted small">
                    {n.kind === 'server' ? t('сервер') : t('устройство')}
                  </span>
                </label>
              ))
            )}
            {/* группы — такими же строками: тот же выбор «кто», только разрядом */}
            <label className="pick-row">
              <input
                type="checkbox"
                checked={srcKind === 'devices'}
                onChange={(e) => {
                  setSrcKind(e.target.checked ? 'devices' : 'node')
                  setSrc([])
                }}
              />
              <span className="ent-dot ent-any" />
              <span className="pick-label">{t('Все устройства')}</span>
            </label>
            <label className="pick-row">
              <input
                type="checkbox"
                checked={srcKind === 'servers'}
                onChange={(e) => {
                  setSrcKind(e.target.checked ? 'servers' : 'node')
                  setSrc([])
                }}
              />
              <span className="ent-dot ent-any" />
              <span className="pick-label">{t('Все серверы')}</span>
            </label>
          </div>
        </div>

        {/* 2 · Куда · 3 · Через · порт · Направить */}
        <div className="dir-step dir-what">
          <span className="dir-step-label">{t('2 · Куда ходят')}</span>
          <div className="dir-mode-body">
            <input
              value={dst}
              onChange={(e) => setDst(e.target.value)}
              placeholder={t('домен, IP или подсеть')}
              onKeyDown={(e) => e.key === 'Enter' && add()}
            />
            <div className="dir-port-row">
              <span className="muted small">{t('порт')}</span>
              <select value={ports} onChange={(e) => setPorts(e.target.value)}>
                {PORT_PRESETS.map((p) => (
                  <option key={p} value={p}>
                    {portText(p)}
                  </option>
                ))}
                <option value="custom">{t('свой порт…')}</option>
              </select>
              {ports === 'custom' && (
                <input
                  className="dir-port"
                  value={customPort}
                  onChange={(e) => setCustomPort(e.target.value)}
                  placeholder="5432"
                  onKeyDown={(e) => e.key === 'Enter' && add()}
                />
              )}
            </div>
            <span className="muted small">
              {t('«Любой порт» — все порты; протокол правило не ограничивает, пройдут и TCP, и UDP.')}
            </span>
            <span className="muted small">
              {t('Весь трафик сервера через другой узел — это не направление, а exit-нода: пометьте узел «Шлюзом выхода», разрешите его источнику, затем на источнике выполните tailscale set --exit-node. Так трафик не течёт на другие ноды. Управляйте сервером в это время по его тайнет-адресу (100.x).')}
            </span>
          </div>

          <span className="dir-step-label">{t('3 · Через какую ноду')}</span>
          <select className="dir-via" value={via} onChange={(e) => setVia(e.target.value)}>
            <option value="">{t('выберите сервер…')}</option>
            {exits.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>

          <p className="dir-summary">
            {!canBuild
              ? t('Отметьте кто, куда и через какую ноду — здесь появится итог')
              : `${whoSummary} → ${dstSummary}${portSummary ? ' : ' + portSummary : ''} ${t('через')} ${viaSummary}`}
          </p>
          <button
            className="dir-go"
            onClick={add}
            disabled={busy || !canBuild || (ports === 'custom' && !customPort.trim())}
          >
            {busy ? t('…') : t('Направить')}
          </button>
        </div>
      </div>

      {error && <p className="form-error">{error}</p>}

      {dirs.length > 8 && (
        <input
          className="dir-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t('фильтр по имени или адресу')}
        />
      )}

      {loading ? (
        <p className="muted small">{t('загрузка…')}</p>
      ) : dirs.length === 0 ? (
        <p className="muted small">{t('Направлений пока нет.')}</p>
      ) : shown.length === 0 ? (
        <p className="muted small">{t('Под фильтр ничего не подошло.')}</p>
      ) : (
        <div className="dir-groups">
          {grouped.map(([who, rows]) => {
            const open = !folded.has(who)
            return (
              <div key={who} className="dir-group">
                <button className="node-group-head" onClick={() => toggleFold(who)}>
                  <span className="node-group-caret">{open ? '▾' : '▸'}</span>
                  <span className="node-group-name">{who}</span>
                  <span className="muted small">{rows.length}</span>
                </button>
                {open && (
                  <table className="keys-table dir-table">
                    <thead>
                      <tr>
                        <th>{t('Куда')}</th>
                        <th>{t('Через')}</th>
                        <th>{t('Порт')}</th>
                        <th>{t('Статус')}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((d) => (
                        <tr key={d.id}>
                          <td>
                            {d.full ? (
                              <span className="tag-chip tag-warn" title={t('Полный туннель через subnet-маршруты убран как небезопасный — удалите это направление. Весь трафик через узел делается exit-нодой (см. подсказку выше).')}>
                                {t('весь трафик (устарело)')}
                              </span>
                            ) : (
                              <>
                                <span className="mono">{d.dst}</span>
                                {d.ips.length > 0 && d.ips[0] !== d.dst && (
                                  <span className="muted small dir-ips"> → {d.ips.join(', ')}</span>
                                )}
                              </>
                            )}
                          </td>
                          <td>{nodeName(d.via)}</td>
                          <td>{portText(d.ports)}</td>
                          <td>
                            {d.error ? (
                              <span className="tag-chip tag-warn" title={d.error}>
                                {t('адрес не проверить')}
                              </span>
                            ) : !d.via_agent ? (
                              <button
                                className="tag-chip tag-warn tag-action"
                                onClick={() => showFix(d.via)}
                                title={t('Показать команду установки агента')}
                              >
                                {t('нет агента')}
                              </button>
                            ) : d.active ? (
                              <span className="tag-chip">{t('работает')}</span>
                            ) : (
                              <span className="muted small">{t('применяется…')}</span>
                            )}
                          </td>
                          <td className="row-actions">
                            <button
                              className="ghost small"
                              disabled={busy}
                              onClick={() => remove(d.id)}
                            >
                              {t('Убрать')}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )
          })}
        </div>
      )}

      {fix && (
        <div className="exit-setup">
          <p className="muted small">
            {t('На ноде «{name}» нет агента — без него она не применит маршрут. Выполните на НЕЙ САМОЙ под root, один раз:', { name: fix.node })}
          </p>
          <pre className="enroll-script cmd-oneline">{fix.cmd}</pre>
          <div className="enroll-actions">
            <button onClick={copyFix}>
              {copied ? t('Скопировано ✓') : t('Скопировать команду установки')}
            </button>
            <button className="ghost" onClick={() => setFix(null)}>
              {t('Закрыть')}
            </button>
          </div>
          <p className="muted small">{t('Статус обновится сам, как только агент отзовётся.')}</p>
        </div>
      )}

      <p className="muted small">
        {t('Адрес домена панель перепроверяет сама и обновляет маршрут, если сайт переехал.')}
      </p>
      <p className="muted small">
        {t('Важно: на устройстве-источнике должно быть включено принятие маршрутов, иначе направление молча не сработает (панель покажет «работает» — это про сторону ноды-выхода). Новые ноды NodeRoost включают его сами; уже подключённой достаточно один раз выполнить:')}
      </p>
      <pre className="enroll-script cmd-oneline">sudo tailscale set --accept-routes</pre>
    </section>
  )
}
