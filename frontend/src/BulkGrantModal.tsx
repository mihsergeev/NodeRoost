import { useEffect, useMemo, useState } from 'react'
import { resolveHost, type AclRule, type AclSelector, type Node } from './api'
import { PORT_OPTS, parseSel, portHint, roleList, toggleRule } from './aclui'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

// Массовая выдача: мультивыбор «кому» (юзеры/ноды/любой) × мультивыбор серверов × порт.
export function BulkGrantModal({
  nodes,
  rules,
  onClose,
  onApply,
}: {
  nodes: Node[]
  rules: AclRule[]
  onClose: () => void
  onApply: (next: AclRule[]) => void
}) {
  const { t } = useI18n()
  useModalDismiss(onClose)

  const [who, setWho] = useState<Set<string>>(new Set())
  const [targets, setTargets] = useState<Set<string>>(new Set())
  // Тип назначения ОДИН на выдачу, а не набор. Причина не в эстетике: порт тут
  // один на всё, и «все серверы + интернет : 22» — бессмыслица (к серверам по 22
  // осмысленно, в интернет нет). Плюс подписи «…или» наконец перестают врать.
  // Выход в интернет здесь больше НЕ задаётся — он настраивается в самой ноде
  // («Изменить ноду» → «Выход в интернет через»), чтобы жить в одном месте.
  const [destKind, setDestKind] = useState<'all' | 'servers' | 'external'>(
    'servers',
  )
  const [dest, setDest] = useState('') // IP / подсеть / сайт
  // Порт НЕ предугадываем: заранее выбранный SSH тихо выдавал доступ на 22 там,
  // где имелся в виду другой порт. Пусть выбор будет осознанным — «Свой…» с
  // пустым полем не даёт нажать «Выдать», пока порт не назван.
  const [port, setPort] = useState('custom')
  const [customPort, setCustomPort] = useState('')
  const [whoSearch, setWhoSearch] = useState('')
  const [tgtSearch, setTgtSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [destErr, setDestErr] = useState<string | null>(null)
  const [portTouched, setPortTouched] = useState(false)

  const allTargets = destKind === 'all'

  // внешний адрес → порт по умолчанию «любой» (а не SSH:22), пока не выбрали сами
  useEffect(() => {
    if (!portTouched && destKind === 'external') setPort('*')
  }, [destKind, portTouched])

  const roles = useMemo(() => roleList(nodes), [nodes])

  // Правило на внешний адрес работает, ТОЛЬКО если кто-то в меше раздаёт к нему
  // маршрут: свой выход ноды в интернет ACL не контролирует вовсе. Без маршрута
  // это правило-пустышка, и молчать об этом нельзя.
  const ip2n = (ip: string) =>
    ip.split('.').reduce((a, o) => (a << 8) + (Number(o) & 255), 0) >>> 0
  const inCidr = (ip: string, cidr: string) => {
    const [net, bitsRaw] = cidr.split('/')
    const bits = bitsRaw === undefined ? 32 : Number(bitsRaw)
    if (!/^[\d.]+$/.test(ip) || !/^[\d.]+$/.test(net) || bits < 0 || bits > 32) return false
    const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0
    return (ip2n(ip) & mask) === (ip2n(net) & mask)
  }
  const routed = useMemo(
    () => nodes.flatMap((n) => n.subnet_routes),
    [nodes],
  )
  const destLiteral = dest.trim()
  const destIsLiteral = /^[\d.]+(\/\d+)?$/.test(destLiteral)
  const destUnrouted =
    destKind === 'external' &&
    destIsLiteral &&
    !routed.some((r) => inCidr(destLiteral.split('/')[0], r))

  const label = (key: string) =>
    key === 'any:'
      ? t('любой')
      : key.startsWith('tag:')
        ? `#${key.slice(4)}`
        : nodes.find((n) => n.id === key.slice(5))?.name || key
  const whoText = [...who].map(label).join(', ')
  // Нода сама себе доступ не выдаёт: внутри себя ACL не действует, и apply()
  // такие пары пропускает. Считаем, сколько правил РЕАЛЬНО получится, иначе
  // итоговая строка обещает то, чего не произойдёт.
  const selfPairs =
    destKind === 'servers'
      ? [...who].filter((w) => w.startsWith('node:') && targets.has(w)).length
      : 0
  const totalPairs =
    destKind === 'servers' ? who.size * targets.size - selfPairs : who.size

  // выдача админу на серверы — пустая работа: авто-правило уже даёт ему всё это
  const adminIds = new Set(nodes.filter((n) => n.admin).map((n) => `node:${n.id}`))
  const pointless =
    who.size > 0 &&
    [...who].every((k) => adminIds.has(k)) &&
    (destKind === 'all' || (destKind === 'servers' && targets.size > 0))
  const destText =
    destKind === 'all'
      ? t('все серверы')
      : destKind === 'servers'
        ? [...targets]
            // если источник ровно один и он же цель — показывать эту пару нельзя,
            // она не создастся
            .filter((tg) => !(who.size === 1 && who.has(tg)))
            .map(label)
            .join(', ')
        : dest.trim()
  const portText =
    (port === 'custom' ? customPort.trim() : port) === '*'
      ? t('любой порт')
      : port === 'custom'
        ? customPort.trim()
        : PORT_OPTS.find(([v]) => v === port)?.[1] || port

  const whoOptions = useMemo(() => {
    const opts: {
      key: string
      label: string
      kind: AclSelector['kind']
      admin?: boolean
    }[] = [
      ...roles.map((r) => ({ key: `tag:${r}`, label: `#${r}`, kind: 'tag' as const })),
      // обычные ноды выше, админские ниже: админ и так достаёт все серверы,
      // выдавать ему туда нечего — ему нужны только внешние адреса
      ...nodes
        .filter((n) => !n.admin)
        .map((n) => ({
          key: `node:${n.id}`,
          label: n.name,
          kind: 'node' as const,
          admin: false,
        })),
      ...nodes
        .filter((n) => n.admin)
        .map((n) => ({
          key: `node:${n.id}`,
          label: n.name,
          kind: 'node' as const,
          admin: true,
        })),
      // «Любой» — группа, а не машина; в самом низу, отдельно от имён
      { key: 'any:', label: t('Любой — все ноды'), kind: 'any' as const },
    ]
    const q = whoSearch.trim().toLowerCase()
    return q ? opts.filter((o) => o.label.toLowerCase().includes(q)) : opts
  }, [nodes, roles, whoSearch, t])

  // цель = роль (группа серверов) или конкретный сервер
  const tgtOptions = useMemo(() => {
    const opts = [
      ...roles.map((r) => ({ key: `tag:${r}`, label: `#${r}`, kind: 'tag' as const })),
      ...nodes
        .filter((n) => n.kind === 'server')
        .map((n) => ({ key: `node:${n.id}`, label: n.name, kind: 'node' as const })),
    ]
    const q = tgtSearch.trim().toLowerCase()
    return (q ? opts.filter((o) => o.label.toLowerCase().includes(q)) : opts).slice(0, 80)
  }, [nodes, roles, tgtSearch])

  function tog(set: Set<string>, key: string, upd: (s: Set<string>) => void) {
    const next = new Set(set)
    next.has(key) ? next.delete(key) : next.add(key)
    upd(next)
  }

  const portVal = port === 'custom' ? customPort.trim() : port
  const hasTarget =
    destKind === 'all' ||
    (destKind === 'servers' && targets.size > 0) ||
    (destKind === 'external' && !!dest.trim())
  const canApply = who.size > 0 && hasTarget && !!portVal && totalPairs > 0 && !busy

  async function apply() {
    if (!canApply) return
    setBusy(true)
    setDestErr(null)
    try {
      const dsts: AclSelector[] = []
      if (destKind === 'all') {
        // «все серверы» = только серверы, не устройства
        dsts.push({ kind: 'servers', value: '' })
      } else if (destKind === 'servers') {
        dsts.push(...[...targets].map((k) => parseSel(k)))
      } else {
        const r = await resolveHost(dest.trim())
        if (!r.ips.length) {
          setDestErr(r.note || t('Не удалось разрешить адрес'))
          return
        }
        for (const ip of r.ips) dsts.push({ kind: 'cidr', value: ip })
      }
      let next = rules
      for (const wk of who) {
        const src = parseSel(wk)
        for (const dst of dsts) {
          if (src.kind === 'node' && dst.kind === 'node' && src.value === dst.value) continue
          next = toggleRule(next, src, dst, portVal, true)
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
          <h3>{t('Выдать доступ')}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>
        <p className="muted small">
          {t('Кому разрешить и куда. Слева можно отметить несколько, справа — один вариант назначения.')}
        </p>

        <div className="grant-cols">
          <div className="grant-col">
            <div className="grant-col-title dir-step-label">{t('1 · Кому')}</div>
            <input
              className="search-box"
              value={whoSearch}
              onChange={(e) => setWhoSearch(e.target.value)}
              placeholder={t('поиск…')}
            />
            <div className="pick-list">
              {whoOptions.map((o) => (
                <label key={o.key} className="pick-row">
                  <input type="checkbox" checked={who.has(o.key)} onChange={() => tog(who, o.key, setWho)} />
                  <span className={`ent-dot ent-${o.kind}`} />
                  <span className="pick-label">{o.label}</span>
                  {/* админ и так достаёт все серверы по авто-правилу — пусть это
                      будет видно, чтобы не выдавать ему лишнего вручную */}
                  {o.admin && (
                    <span
                      className="pill-admin"
                      title={t('Админ и так ходит на все серверы. Выдавать ему имеет смысл только внешний адрес (выход в интернет — в «Изменить ноду»).')}
                    >
                      {t('админ')}
                    </span>
                  )}
                </label>
              ))}
            </div>
          </div>

          <div className="grant-col">
            <div className="grant-col-title dir-step-label">{t('2 · Куда')}</div>
            {/* радио, а не галочки: тип назначения один на выдачу */}
            <label className="pick-row pick-all">
              <input
                type="radio"
                name="destkind"
                checked={destKind === 'all'}
                onChange={() => setDestKind('all')}
              />
              <span className="pick-label">{t('Все серверы')}</span>
            </label>
            <label className="pick-row">
              <input
                type="radio"
                name="destkind"
                checked={destKind === 'servers'}
                onChange={() => setDestKind('servers')}
              />
              <span className="pick-label">{t('Отдельные серверы')}</span>
            </label>
            {destKind === 'servers' && (
              <>
                <input
                  className="search-box"
                  value={tgtSearch}
                  onChange={(e) => setTgtSearch(e.target.value)}
                  placeholder={t('поиск сервера…')}
                />
                <div className="pick-list">
                  {tgtOptions.map((o) => (
                    <label key={o.key} className="pick-row">
                      <input
                        type="checkbox"
                        checked={targets.has(o.key)}
                        onChange={() => tog(targets, o.key, setTargets)}
                      />
                      <span className={`ent-dot ent-${o.kind}`} />
                      <span className="pick-label">{o.label}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
            <label className="pick-row">
              <input
                type="radio"
                name="destkind"
                checked={destKind === 'external'}
                onChange={() => setDestKind('external')}
              />
              <span className="pick-label">{t('Внешний адрес')}</span>
            </label>
            {/* поле и пояснение принадлежат своему варианту — в остальных они
                только занимают место и выглядят частью чужого выбора */}
            {destKind === 'external' && (
              <label className="field grant-dest">
                <input
                  value={dest}
                  autoFocus
                  onChange={(e) => setDest(e.target.value)}
                  placeholder="8.8.8.8, 10.0.0.0/8, myip.ru"
                />
                <span className="muted small">
                  {t('Работает, только если какая-то нода раздаёт маршрут к этому адресу — тогда правило решает, кому этим маршрутом можно пользоваться. Свой выход ноды в интернет ACL не контролирует: туда она ходит напрямую в любом случае. Нужен маршрут — заведите направление в «Маршрутизации».')}
                </span>
                {destUnrouted && (
                  <span className="muted small grant-warn">
                    {t('Ни одна нода не раздаёт маршрут к этому адресу — правило ничего не изменит.')}
                  </span>
                )}
                {destErr && <span className="form-error">{destErr}</span>}
              </label>
            )}
          </div>
        </div>

        <div className="grant-port">
          <span className="dir-step-label">{t('3 · По порту')}</span>
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
              autoFocus
              value={customPort}
              onChange={(e) => setCustomPort(e.target.value)}
              placeholder="5430"
            />
          )}
          {port === 'custom' && customPort.trim() && (
            <span className="muted small port-hint">{portHint(customPort, t)}</span>
          )}
        </div>

        {/* то, что получится, одной строкой: иначе из двух списков и трёх
            переключателей не собирается понимание, что именно сейчас выдастся */}
        <p className="grant-summary">
          {who.size === 0 || (!allTargets && !targets.size && !dest.trim())
            ? t('Отметьте, кому и куда — здесь появится итог')
            : `${whoText} → ${destText} : ${portText}`}
        </p>
        {totalPairs === 0 && who.size > 0 && hasTarget && (
          <p className="muted small grant-note grant-warn">
            {t('Нода не выдаёт доступ сама себе — выдавать нечего. Выберите другой источник или другую цель.')}
          </p>
        )}
        {selfPairs > 0 && totalPairs > 0 && (
          <p className="muted small grant-note">
            {t('Пары «нода сама на себя» пропущены — внутри себя доступ не выдаётся.')}
          </p>
        )}
        {pointless && (
          <p className="muted small grant-note">
            {t('Все отмеченные — админы, а они и так ходят на любой сервер: эта выдача ничего не изменит. Админу здесь выдают только внешний адрес.')}
          </p>
        )}

        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>
            {t('Отмена')}
          </button>
          <button onClick={apply} disabled={!canApply}>
            {busy ? t('…') : t('Выдать')}
          </button>
        </div>
      </div>
    </div>
  )
}
