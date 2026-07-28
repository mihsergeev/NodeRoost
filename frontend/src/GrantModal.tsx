import { useEffect, useMemo, useState } from 'react'
import { resolveHost, type AclRule, type AclSelector, type Node } from './api'
import { PORT_OPTS, parseSel, portHint, roleList, toggleRule } from './aclui'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

// Одна сторона зафиксирована = текущая нода. role='target' — нода-получатель
// (выбираем «кому» разрешить вход), role='source' — нода-источник (выбираем «куда»).
export function GrantModal({
  node,
  role,
  nodes,
  rules,
  onClose,
  onApply,
}: {
  node: Node
  role: 'target' | 'source'
  nodes: Node[]
  rules: AclRule[]
  onClose: () => void
  onApply: (next: AclRule[]) => void
}) {
  const { t } = useI18n()
  useModalDismiss(onClose)

  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [port, setPort] = useState('22')
  const [customPort, setCustomPort] = useState('')
  const [search, setSearch] = useState('')
  // назначение по IP/подсети/сайту (только для «куда ходит», role='source')
  const [dest, setDest] = useState('')
  const [busy, setBusy] = useState(false)
  const [destErr, setDestErr] = useState<string | null>(null)
  const [portTouched, setPortTouched] = useState(false)

  // для IP/подсети разумный порт по умолчанию — «любой» (а не SSH:22), пока
  // пользователь сам не выбрал порт
  useEffect(() => {
    if (!portTouched && dest.trim()) setPort('*')
  }, [dest, portTouched])

  const options = useMemo(() => {
    // role='target' (кто сюда) — любые ноды/юзеры; role='source' (куда) — только серверы
    const others = nodes.filter(
      (n) => n.id !== node.id && (role === 'target' || n.kind === 'server'),
    )
    const opts: {
      key: string
      label: string
      kind: AclSelector['kind']
      admin?: boolean
    }[] = []
    if (role === 'target') {
    }
    for (const r of roleList(nodes)) opts.push({ key: `tag:${r}`, label: `#${r}`, kind: 'tag' })
    for (const n of others)
      opts.push({ key: `node:${n.id}`, label: n.name, kind: 'node', admin: n.admin })
    // источник (кто сюда) может быть «любой»; назначение (куда) — «все серверы».
    // Выход в интернет здесь НЕ задаётся: он настраивается в самой ноде («Изменить
    // ноду» → «Выход в интернет через»), чтобы быть в одном месте, а не дублироваться.
    if (role === 'target') {
      opts.push({ key: 'any:', label: t('Любой (кто угодно)'), kind: 'any' })
    } else {
      opts.push({ key: 'servers:', label: t('Все серверы'), kind: 'servers' })
    }
    const q = search.trim().toLowerCase()
    return q ? opts.filter((o) => o.label.toLowerCase().includes(q)) : opts
  }, [nodes, node.id, role, search, t])

  function toggle(key: string) {
    setPicked((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const portVal = port === 'custom' ? customPort.trim() : port
  const hasDest = role === 'source' && !!dest.trim()
  const canApply = (picked.size > 0 || hasDest) && !!portVal && !busy

  async function apply() {
    if (!canApply) return
    setBusy(true)
    setDestErr(null)
    try {
      const self: AclSelector = { kind: 'node', value: node.id }
      let next = rules
      for (const pk of picked) {
        const other = parseSel(pk)
        const [src, dst] = role === 'target' ? [other, self] : [self, other]
        if (src.kind === 'node' && dst.kind === 'node' && src.value === dst.value) continue
        next = toggleRule(next, src, dst, portVal, true)
      }
      // назначение по IP/подсети/сайту: резолвим (сайт → IP), пиним каждый IP
      if (hasDest) {
        const r = await resolveHost(dest.trim())
        if (!r.ips.length) {
          setDestErr(r.note || t('Не удалось разрешить адрес'))
          return
        }
        for (const ip of r.ips) {
          next = toggleRule(next, self, { kind: 'cidr', value: ip }, portVal, true)
        }
      }
      onApply(next)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="card modal grant-modal" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>
            {role === 'target'
              ? t('Разрешить доступ к «{name}»', { name: node.name })
              : t('Куда может ходить «{name}»', { name: node.name })}
          </h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        <div className="grant-one">
          <div className="grant-col-title">{role === 'target' ? t('Кому разрешить') : t('На какие сервера')}</div>
          <input
            className="search-box"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={role === 'target' ? t('поиск…') : t('поиск сервера…')}
          />
          <div className="pick-list">
            {options.map((o) => (
              <label key={o.key} className="pick-row">
                <input type="checkbox" checked={picked.has(o.key)} onChange={() => toggle(o.key)} />
                <span className={`ent-dot ent-${o.kind}`} />
                <span className="pick-label">{o.label}</span>
                {o.admin && <span className="pill-admin">{t('админ')}</span>}
              </label>
            ))}
          </div>
        </div>

        <div className="grant-port">
          <span className="muted small">{t('По порту')}</span>
          <select
            className="select"
            value={port}
            onChange={(e) => {
              setPort(e.target.value)
              setPortTouched(true)
            }}
          >
            {PORT_OPTS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
            <option value="custom">{t('Свой…')}</option>
          </select>
          {port === 'custom' && (
            <input
              className="port-custom"
              value={customPort}
              onChange={(e) => setCustomPort(e.target.value)}
              placeholder="5430"
            />
          )}
          {port === 'custom' && customPort.trim() && (
            <span className="muted small port-hint">{portHint(customPort, t)}</span>
          )}
        </div>

        {role === 'source' && (
          <label className="field grant-dest">
            <span>{t('…или IP / подсеть / сайт')}</span>
            <input
              value={dest}
              onChange={(e) => setDest(e.target.value)}
              placeholder="8.8.8.8, 10.0.0.0/8, myip.ru"
            />
            <span className="muted small">
              {t('IP/подсеть — прямой доступ к этим адресам в тайлнете (напр. subnet-маршрут, конкретный хост). Сайт резолвится в IP. Обычный выход в интернет так НЕ настраивается — для него в «Изменить ноду» есть «Выход в интернет через».')}
            </span>
            {destErr && <span className="form-error">{destErr}</span>}
          </label>
        )}

        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>
            {t('Отмена')}
          </button>
          <button onClick={apply} disabled={!canApply}>
            {busy ? t('…') : t('Разрешить')}
          </button>
        </div>
      </div>
    </div>
  )
}
