import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  getOutdatedAgents,
  deleteNode,
  expireNode,
  listNodes,
  setNodeMeta,
  updateOutdatedAgents,
  type Node,
} from './api'
import { AddNodeModal } from './AddNodeModal'
import { Menu } from './Menu'
import { NodeDetail } from './NodeDetail'
import { NodeEditModal } from './NodeEditModal'
import { RoutesModal } from './RoutesModal'
import { CountryFlag } from './CountryFlag'
import { OsIcon } from './OsIcon'
import { useI18n } from './i18n'

const EXIT_CIDRS = ['0.0.0.0/0', '::/0']

type Filter = 'all' | 'online' | 'offline'
type Tr = (s: string, p?: Record<string, string | number>) => string

function fmtAgo(iso: string, t: Tr): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return t('только что')
  if (s < 3600) return t('{n} мин назад', { n: Math.floor(s / 60) })
  if (s < 86400) return t('{n} ч назад', { n: Math.floor(s / 3600) })
  return t('{n} дн назад', { n: Math.floor(s / 86400) })
}

export function NodesPage({
  kind,
  onUnauthorized,
  openNodeId,
  onOpened,
}: {
  kind: 'server' | 'device'
  onUnauthorized: () => void
  // id ноды из ссылки в алерте — открыть её карточку сразу при загрузке
  openNodeId?: string | null
  onOpened?: () => void
}) {
  const { t } = useI18n()
  const [nodes, setNodes] = useState<Node[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [hiddenOpen, setHiddenOpen] = useState(() => {
    try {
      return localStorage.getItem(`noderoost_layout_${kind}_hidden_open`) === '1'
    } catch {
      return false
    }
  })
  const toggleHidden = () =>
    setHiddenOpen((v) => {
      try {
        localStorage.setItem(`noderoost_layout_${kind}_hidden_open`, v ? '0' : '1')
      } catch {
        /* см. выше: без хранилища раскладка просто не запомнится */
      }
      return !v
    })
  // Сколько нод сидит на агенте прошлого релиза. Пока этого не было видно,
  // обновление делалось по одной карточке и до конца не доходило.
  const [outdated, setOutdated] = useState(0)
  const [upgBusy, setUpgBusy] = useState(false)
  const [editNode, setEditNode] = useState<Node | null>(null)
  const [routesNode, setRoutesNode] = useState<Node | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [detailNodeId, setDetailNodeId] = useState<string | null>(openNodeId ?? null)
  const [mobileDetailOpen, setMobileDetailOpen] = useState(!!openNodeId)
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 800px)').matches)
  const requestedOpen = useRef(openNodeId ?? null)
  const [linkedNodeId, setLinkedNodeId] = useState(openNodeId ?? null)
  const rowButtons = useRef(new Map<string, HTMLButtonElement>())
  const detailPanel = useRef<HTMLDivElement>(null)
  const returnFocus = useRef(false)
  const loadSequence = useRef(0)

  useEffect(() => {
    const media = window.matchMedia('(max-width: 800px)')
    const changed = () => setNarrow(media.matches)
    media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [])
  // перетаскивание ноды между группами (как в соседней панели): ref переживает
  // ре-рендеры, state нужен только для подсветки
  const dragRef = useRef<string | null>(null)
  // ноды, по которым прямо сейчас летит запрос скрытия: пункт меню не
  // блокируется, и второй клик слал ещё один конкурирующий запрос
  const hideBusy = useRef<Set<string>>(new Set())
  const [dragId, setDragId] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState<string | null>(null)
  // Раскладка списка переживает переходы по меню. Страница пересоздаётся при
  // каждом клике по навигации (даже по текущему разделу), и свёрнутые группы с
  // открытым разделом «Скрытые» сбрасывались - приходилось складывать их заново
  // по десять раз за сеанс. Это личное удобство одного браузера, поэтому
  // localStorage, а не настройка на сервере.
  const layoutKey = `noderoost_layout_${kind}`
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(`${layoutKey}_collapsed`)
      return new Set(raw ? (JSON.parse(raw) as string[]) : [])
    } catch {
      return new Set()
    }
  })

  const toggleCollapse = (k: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(k) ? next.delete(k) : next.add(k)
      try {
        localStorage.setItem(`${layoutKey}_collapsed`, JSON.stringify([...next]))
      } catch {
        /* приватный режим или запрет на хранилище - раскладка просто не запомнится */
      }
      return next
    })

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current
    try {
      const next = await listNodes()
      if (sequence !== loadSequence.current) return
      setNodes(next)
      setError(null)
    } catch (err) {
      if (sequence !== loadSequence.current) return
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized()
        return
      }
      setError(err instanceof Error ? err.message : t('Не удалось загрузить ноды'))
      setNodes((prev) => prev ?? [])
    }
  }, [onUnauthorized, t])

  const loadOutdated = useCallback(async () => {
    try {
      const o = await getOutdatedAgents()
      setOutdated(o.signed ? o.count : 0)
    } catch {
      setOutdated(0) // не смогли узнать - просто не показываем кнопку
    }
  }, [])

  async function upgradeAll() {
    if (
      !window.confirm(
        t('Ноды с агентом прошлого релиза обновятся в течение минуты. Всего нод: {count}. Продолжить?', {
          count: outdated,
        }),
      )
    ) {
      return
    }
    setUpgBusy(true)
    try {
      await updateOutdatedAgents()
      await loadOutdated()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized()
        return
      }
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setUpgBusy(false)
    }
  }

  useEffect(() => {
    loadOutdated()
  }, [loadOutdated, detailNodeId])

  useEffect(() => {
    load()
    // Обновляемся сами, как и остальные страницы панели (здоровье - 30 с,
    // направления - 20 с, маршруты - 15 с). Без таймера на открытой вкладке час
    // висело «оффлайн 2 мин назад», а нода, упавшая после загрузки страницы, не
    // подсвечивалась вовсе - то есть ровно то, ради чего подсветку и делали.
    const id = window.setInterval(load, 30000)
    return () => {
      ++loadSequence.current
      window.clearInterval(id)
    }
  }, [load])

  // ноды только этого типа (сервер/устройство)
  const all = useMemo(() => (nodes ?? []).filter((n) => n.kind === kind), [nodes, kind])
  // Скрытые живут отдельным разделом внизу: в счётчиках их нет, иначе «1 оффлайн»
  // горит из-за машины, которую сами же и отложили. Но и совсем прятать нельзя -
  // забытая нода хуже шумной, поэтому раздел всегда на виду, со своим счётчиком.
  const scoped = useMemo(() => all.filter((n) => !n.hidden), [all])
  const hiddenList = useMemo(() => all.filter((n) => n.hidden), [all])

  const counts = useMemo(() => {
    return {
      total: scoped.length,
      online: scoped.filter((n) => n.online).length,
      offline: scoped.filter((n) => !n.online).length,
    }
  }, [scoped])

  // Одна воронка для обоих списков. Раньше поиск и плитки применялись только к
  // основному: вводишь имя, которого ни у кого нет, сверху «Ничего не найдено»,
  // а снизу раздел «Скрытые» как ни в чём не бывало со всеми нодами.
  const applyFilters = useCallback(
    (list: Node[]) => {
      let out = list
      if (filter === 'online') out = out.filter((n) => n.online)
      if (filter === 'offline') out = out.filter((n) => !n.online)
      const q = search.trim().toLowerCase()
      if (q) {
        out = out.filter(
          (n) =>
            n.name.toLowerCase().includes(q) ||
            n.hostname.toLowerCase().includes(q) ||
            n.ip_addresses.some((ip) => ip.toLowerCase().includes(q)) ||
            n.tags.some((tag) => tag.toLowerCase().includes(q)) ||
            (n.group ?? '').toLowerCase().includes(q) ||
            (n.subgroup ?? '').toLowerCase().includes(q),
        )
      }
      return out
    },
    [filter, search],
  )

  const visible = useMemo(() => applyFilters(scoped), [scoped, applyFilters])
  const hiddenVisible = useMemo(() => applyFilters(hiddenList), [hiddenList, applyFilters])

  // Раскладка списка: админские ноды закреплены сверху отдельным блоком, затем
  // группы (организация) → подгруппы (проект). Без группы — в конце, чтобы
  // неразобранное не мешалось между осмысленными разделами.
  const sections = useMemo(() => {
    const admins = visible.filter((n) => n.admin)
    const rest = visible.filter((n) => !n.admin)
    const byGroup = new Map<string, Map<string, Node[]>>()
    for (const n of rest) {
      const g = n.group?.trim() || ''
      const sg = n.subgroup?.trim() || ''
      if (!byGroup.has(g)) byGroup.set(g, new Map())
      const subs = byGroup.get(g)!
      if (!subs.has(sg)) subs.set(sg, [])
      subs.get(sg)!.push(n)
    }
    const ord = (a: string, b: string) =>
      a === '' ? 1 : b === '' ? -1 : a.localeCompare(b) // «без группы» всегда последней
    return {
      admins,
      groups: [...byGroup.entries()]
        .sort((a, b) => ord(a[0], b[0]))
        .map(([g, subs]) => ({
          group: g,
          count: [...subs.values()].reduce((acc, l) => acc + l.length, 0),
          subs: [...subs.entries()]
            .sort((a, b) => ord(a[0], b[0]))
            .map(([sg, list]) => ({ subgroup: sg, list })),
        })),
    }
  }, [visible])

  const filtering = !!search.trim() || filter !== 'all'
  const listed = useMemo(() => [
    ...sections.admins,
    ...sections.groups.flatMap((g) =>
      filtering || !collapsed.has(g.group || '__none__')
        ? g.subs.flatMap((sub) => sub.list)
        : [],
    ),
    ...(hiddenOpen ? hiddenVisible : []),
  ], [sections, filtering, collapsed, hiddenOpen, hiddenVisible])
  const openingLink = requestedOpen.current !== null
  const linkedNode = linkedNodeId === detailNodeId
    ? (nodes ?? []).find((n) => n.id === linkedNodeId && n.kind !== kind)
    : undefined
  const selectedNode = listed.find((n) => n.id === detailNodeId) ?? linkedNode
  const detailNode = selectedNode ?? (!narrow ? listed[0] : undefined)

  useEffect(() => {
    if (error || nodes === null || !requestedOpen.current) return
    const target = nodes.find((n) => n.id === requestedOpen.current)
    requestedOpen.current = null
    if (target) {
      if (target.hidden) setHiddenOpen(true)
      setCollapsed((prev) => {
        const next = new Set(prev)
        next.delete(target.group?.trim() || '__none__')
        return next
      })
      setDetailNodeId(target.id)
      setMobileDetailOpen(true)
    } else {
      setLinkedNodeId(null)
      setMobileDetailOpen(false)
    }
    onOpened?.()
  }, [nodes, error, onOpened])

  useEffect(() => {
    if (nodes === null || openingLink) return
    if (selectedNode) return
    setDetailNodeId(narrow ? null : (listed[0]?.id ?? null))
    setMobileDetailOpen(false)
    setLinkedNodeId(null)
  }, [nodes, listed, selectedNode, narrow, openingLink])

  useEffect(() => {
    if (narrow && mobileDetailOpen && detailNode) {
      detailPanel.current?.focus({ preventScroll: true })
      detailPanel.current?.scrollIntoView?.({ block: 'start' })
    } else if (returnFocus.current) {
      returnFocus.current = false
      rowButtons.current.get(detailNodeId ?? '')?.focus({ preventScroll: true })
    }
  }, [narrow, mobileDetailOpen, detailNode?.id, detailNodeId])

  useEffect(() => {
    if (!narrow || !mobileDetailOpen) {
      rowButtons.current.get(detailNodeId ?? '')?.scrollIntoView?.({ block: 'nearest' })
    }
  }, [detailNodeId, narrow, mobileDetailOpen])

  function selectNode(n: Node) {
    setLinkedNodeId(null)
    setDetailNodeId(n.id)
    setMobileDetailOpen(true)
  }

  function backToList() {
    returnFocus.current = true
    setMobileDetailOpen(false)
    setLinkedNodeId(null)
  }

  // Меняем только hidden, остальные настройки сохраняются на сервере.
  async function doHide(n: Node, hidden: boolean) {
    if (hideBusy.current.has(n.id)) return
    hideBusy.current.add(n.id)
    setNodes((prev) => (prev ?? []).map((x) => (x.id === n.id ? { ...x, hidden } : x)))
    try {
      await setNodeMeta(n.id, { hidden })
      await load()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized()
        return
      }
      await load()
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      hideBusy.current.delete(n.id)
    }
  }

  const startDrag = (id: string) => {
    dragRef.current = id
    setDragId(id)
  }
  const endDrag = () => {
    dragRef.current = null
    setDragId(null)
    setDragOver(null)
  }

  // Частичный запрос не перезапишет параллельные изменения доступа и алертов.
  async function dropOn(group: string, subgroup: string) {
    const id = dragRef.current
    endDrag()
    const n = (nodes ?? []).find((x) => x.id === id)
    if (!id || !n) return
    if ((n.group ?? '') === group && (n.subgroup ?? '') === subgroup) return
    // оптимистично — чтобы карточка не «прыгала» на время запроса
    setNodes((prev) => (prev ?? []).map((x) => (x.id === id ? { ...x, group, subgroup } : x)))
    try {
      await setNodeMeta(id, { group, subgroup })
    } catch (err) {
      handleErr(err)
    }
    load()
  }

  function handleErr(err: unknown) {
    if (err instanceof ApiError && err.status === 401) onUnauthorized()
    else window.alert(err instanceof Error ? err.message : t('Ошибка'))
  }

  async function doExpire(n: Node) {
    if (
      !window.confirm(
        t(
          'Отозвать ключ ноды «{name}»? Она отключится и не подключится обратно, пока её не переподключить заново.',
          { name: n.name },
        ),
      )
    )
      return
    try {
      await expireNode(n.id)
      load()
    } catch (err) {
      handleErr(err)
    }
  }

  async function doDelete(n: Node) {
    if (
      !window.confirm(
        t('Удалить ноду «{name}»? Она будет удалена из тайлнета.', { name: n.name }),
      )
    )
      return
    try {
      await deleteNode(n.id)
      load()
    } catch (err) {
      handleErr(err)
    }
  }

  const renderRow = (n: Node) => (
    <div
      key={n.id}
      className={
        'node-row' +
        (detailNode?.id === n.id ? ' selected' : '') +
        (dragId === n.id ? ' dragging' : '') +
        (!n.online && !n.hidden ? ' node-off' : '') +
        (n.hidden ? ' node-hidden' : '')
      }
    >
      {!n.admin && (
        <span
          className="nr-grip"
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = 'move'
            e.dataTransfer.setData('text/plain', n.id)
            startDrag(n.id)
          }}
          onDragEnd={endDrag}
          title={t('Перетащите в другую группу')}
          aria-label={t('Перетащите в другую группу')}
        >
          ⠿
        </span>
      )}
      <button
        type="button"
        className="node-main node-row-select"
        aria-label={`${t('Открыть')} ${n.name || t('без имени')}`}
        aria-current={detailNode?.id === n.id ? 'true' : undefined}
        onClick={() => selectNode(n)}
        ref={(element) => {
          if (element) rowButtons.current.set(n.id, element)
          else rowButtons.current.delete(n.id)
        }}
      >
        <span className={`dot ${n.online ? 'dot-ok' : 'dot-fail'}`} />
        <span className="node-info">
          <span className="node-row-heading">
            {n.os && <OsIcon os={n.os} size={16} />}
            <CountryFlag code={n.country} />
            <span className="node-name">{n.name || t('без имени')}</span>
          </span>
          <span className="node-meta">
            {n.ip_addresses.filter((ip) => !ip.includes(':')).map((ip) => (
              <span key={ip} className="chip">{ip}</span>
            ))}
            {!n.online && (
              <span className="node-row-status">
                {n.last_seen
                  ? t('оффлайн {ago}', { ago: fmtAgo(n.last_seen, t) })
                  : t('оффлайн')}
              </span>
            )}
            {n.admin && <span className="pill-admin">{t('админ')}</span>}
            {n.hidden && (
              <span className="pill-muted" title={t('Нода скрыта: не в счётчиках и без алертов')}>
                {t('скрыта')}
              </span>
            )}
            {n.muted && (
              <span className="pill-muted" title={t('Алерты по этой ноде выключены')}>
                {t('без алертов')}
              </span>
            )}
            {n.key_expired && <span className="pill-warn">{t('Ключ истёк')}</span>}
            {n.exit_gateway && (
              <span className="pill-ok" title={t('Шлюз выхода в интернет')}>
                {t('шлюз')}
              </span>
            )}
            {!n.exit_gateway && n.is_exit_node && <span className="pill-ok">exit</span>}
            {n.force_exit ? (
              <span className="pill-warn" title={t('Весь трафик этой ноды принудительно через шлюз')}>
                {t('туннель')}
              </span>
            ) : (
              n.exit_via.length > 0 && (
                <span className="pill-admin" title={t('выход через {n}', { n: n.exit_via.length })}>
                  exit · {n.exit_via.length}
                </span>
              )
            )}
          </span>
        </span>
      </button>
      <div className="node-actions">
        {(n.advertises_exit_node && !n.is_exit_node) || n.available_routes.some(
          (r) => !EXIT_CIDRS.includes(r) && !n.approved_routes.includes(r),
        ) ? (
          <button
            type="button"
            className="ghost icon-btn node-pending-action"
            title={t('Одобрить маршруты')}
            aria-label={t('Одобрить маршруты')}
            onClick={() => setRoutesNode(n)}
          >
            ↗
          </button>
        ) : null}
        <Menu
          className="ghost icon-btn"
          caret={false}
          align="right"
          title={t('Изменить')}
          label={<span className="menu-gear">⋯</span>}
          items={[
            { label: t('Изменить'), onClick: () => setEditNode(n) },
            { label: t('Маршруты'), onClick: () => setRoutesNode(n) },
            {
              label: n.hidden ? t('Вернуть в список') : t('Скрыть: пока не нужна'),
              onClick: () => doHide(n, !n.hidden),
            },
            { label: t('Отозвать ключ'), onClick: () => doExpire(n) },
            { divider: true },
            { label: t('Удалить'), danger: true, onClick: () => doDelete(n) },
          ]}
        />
      </div>
    </div>
  )

  return (
    <>
      <div className="page-head nodes-page-head">
        <h2>{kind === 'server' ? t('Серверы') : t('Устройства')}</h2>
        <div className="page-head-actions">
          {/* Ноды с агентом прошлого релиза: по карточке на ноду - занятие,
              которое не доделывают, и часть сети остаётся на старом скрипте. */}
          {outdated > 0 && (
            <button className="ghost" onClick={upgradeAll} disabled={upgBusy}>
              {upgBusy
                ? t('Отправляем…')
                : t('Обновить агентов ({count})', { count: outdated })}
            </button>
          )}
          <button className="ghost" onClick={load}>
            {t('Обновить')}
          </button>
          <button onClick={() => setAddOpen(true)}>
            {kind === 'server' ? t('+ Добавить сервер') : t('+ Добавить устройство')}
          </button>
        </div>
      </div>


      {error && <p className="form-error">{error}</p>}

      <div className={`nodes-workspace${mobileDetailOpen && detailNode ? ' detail-open' : ''}`}>
      <aside className="nodes-sidebar" aria-label={kind === 'server' ? t('Серверы') : t('Устройства')}>
      <div className="nodes-side-tools">
        <input
          type="search"
          className="search-box"
          value={search}
          onChange={(e) => {
            setLinkedNodeId(null)
            setSearch(e.target.value)
          }}
          aria-label={t('Поиск: имя, IP, тег…')}
          placeholder={t('Поиск: имя, IP, тег…')}
        />
      <div className="nodes-filters" aria-label={t('Статус')}>
        {(
          [
            ['all', t('Все'), counts.total],
            ['online', t('Онлайн'), counts.online],
            ['offline', t('Оффлайн'), counts.offline],
          ] as const
        ).map(([key, label, val]) => (
          <button
            key={key}
            type="button"
            className={`nodes-filter${filter === key ? ' active' : ''}`}
            aria-pressed={filter === key}
            onClick={() => {
              setLinkedNodeId(null)
              setFilter(key)
            }}
          >
            {label} <b>{val}</b>
          </button>
        ))}
      </div>
      </div>

      <div className="nodes-side-list">
      {nodes === null ? (
        <p className="muted">{t('загрузка…')}</p>
      ) : all.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <span className="empty-emoji">{kind === 'server' ? '🖥️' : '💻'}</span>
            <h3>{kind === 'server' ? t('Серверов пока нет') : t('Устройств пока нет')}</h3>
            <p>
              {kind === 'server'
                ? t('Добавьте сервер или пометьте существующую ноду типом «сервер».')
                : t('Добавьте устройство или пометьте ноду типом «устройство» в «Изменить ноду».')}
            </p>
            <button onClick={() => setAddOpen(true)}>
              {kind === 'server' ? t('+ Добавить сервер') : t('+ Добавить устройство')}
            </button>
          </div>
        </div>
      ) : visible.length === 0 ? (
        // Все ноды отложены - это не «ничего нет»: иначе страница предлагала
        // добавить сервер, а прямо под ней висел раздел «Скрытые (4)».
        scoped.length === 0 && hiddenList.length > 0 ? (
          <p className="muted">
            {t('Все ноды в разделе «Скрытые» ниже')}
          </p>
        ) : (
          <p className="muted">{t('Ничего не найдено')}</p>
        )
      ) : (
        <>
          {sections.admins.length > 0 && (
            <div className="node-group node-group-admin">
              <div className="node-group-head">
                <span className="node-group-name">{t('Админские')}</span>
                <span className="muted small">{sections.admins.length}</span>
              </div>
              <div className="node-rows">{sections.admins.map(renderRow)}</div>
            </div>
          )}
          {sections.groups.map((g) => {
            const gk = g.group || '__none__'
            const isOpen = filtering || !collapsed.has(gk)
            return (
              <div
                key={gk}
                // drop на саму группу: ставим группу и СБРАСЫВАЕМ подгруппу —
                // иначе нода утащила бы чужую подгруппу в новую организацию
                className={`node-group${dragId && dragOver === gk ? ' group-drag-over' : ''}`}
                onDragEnter={() => dragId && setDragOver(gk)}
                onDragOver={(e) => dragId && e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault()
                  dropOn(g.group, '')
                }}
              >
                <button
                  type="button"
                  className="node-group-head"
                  aria-expanded={isOpen}
                  disabled={filtering}
                  onClick={() => toggleCollapse(gk)}
                >
                  <span className="node-group-caret">{isOpen ? '▾' : '▸'}</span>
                  <span className="node-group-name">
                    {g.group || <span className="muted">{t('Без группы')}</span>}
                  </span>
                  <span className="muted small">{g.count}</span>
                </button>
                {isOpen &&
                  g.subs.map((sub) => {
                    const sk = `${gk}\u0000${sub.subgroup}`
                    return (
                      <div
                        key={sub.subgroup || '__none__'}
                        className={`node-subgroup${
                          dragId && dragOver === sk ? ' group-drag-over' : ''
                        }`}
                        onDragEnter={(e) => {
                          e.stopPropagation()
                          if (dragId) setDragOver(sk)
                        }}
                        onDragOver={(e) => dragId && e.preventDefault()}
                        onDrop={(e) => {
                          // важнее внешнего drop на группу — иначе подгруппа терялась бы
                          e.preventDefault()
                          e.stopPropagation()
                          dropOn(g.group, sub.subgroup)
                        }}
                      >
                        {/* подзаголовок нужен только если подгруппы реально используются */}
                        {(sub.subgroup || g.subs.length > 1) && (
                          <div className="node-subgroup-head">
                            {sub.subgroup || <span className="muted">{t('без подгруппы')}</span>}
                            <span className="node-group-count">{sub.list.length}</span>
                          </div>
                        )}
                        <div className="node-rows">{sub.list.map(renderRow)}</div>
                      </div>
                    )
                  })}
              </div>
            )
          })}
        </>
      )}

      {hiddenVisible.length > 0 && (
        <div className="node-group node-group-hidden">
          {/* настоящая кнопка, как у групп выше: самодельная из div понимала
              только Enter, а role="button" обязан отзываться и на пробел */}
          <button
            type="button"
            className="node-group-head"
            aria-expanded={hiddenOpen}
            title={t('не в счётчиках и без алертов, пока не вернёте')}
            onClick={toggleHidden}
          >
            <span className="node-group-caret">{hiddenOpen ? '▾' : '▸'}</span>
            <span className="node-group-name">{t('Скрытые')}</span>
            <span className="muted small">{hiddenVisible.length}</span>
          </button>
          {hiddenOpen && <div className="node-rows">{hiddenVisible.map(renderRow)}</div>}
        </div>
      )}
      </div>
      </aside>

      <div className="nodes-detail-panel" ref={detailPanel} tabIndex={-1} aria-label={t('Карточка ноды')}>
        {detailNode ? (
          <NodeDetail
            key={detailNode.id}
            embedded
            node={detailNode}
            nodes={nodes ?? []}
            onBack={backToList}
            onChanged={load}
            onUnauthorized={onUnauthorized}
          />
        ) : (
          <div className="card node-detail-empty">
            <span className="node-empty-symbol" aria-hidden="true">◇</span>
            <h3>{nodes === null ? t('загрузка…') : t('Выберите ноду')}</h3>
            <p>{t('Откройте сервер или устройство в списке слева')}</p>
          </div>
        )}
      </div>
      </div>

      {editNode && (
        <NodeEditModal
          node={editNode}
          nodes={nodes ?? []}
            onClose={() => setEditNode(null)}
          onSaved={() => {
            setEditNode(null)
            load()
          }}
          onUnauthorized={onUnauthorized}
        />
      )}

      {routesNode && (
        <RoutesModal
          node={routesNode}
          onClose={() => setRoutesNode(null)}
          onSaved={() => {
            setRoutesNode(null)
            load()
          }}
          onUnauthorized={onUnauthorized}
        />
      )}

      {addOpen && (
        <AddNodeModal
          kind={kind}
          // нода могла подключиться (или ей проставился тип), пока модалка была
          // открыта — перечитываем список на закрытии, чтобы не жать F5
          onClose={() => {
            setAddOpen(false)
            load()
          }}
          onEnrolled={load}
          onUnauthorized={onUnauthorized}
        />
      )}
    </>
  )
}
