import type { AclRule, AclSelector, Node } from './api'

type Tr = (s: string, p?: Record<string, string | number>) => string

// Пресеты портов для выбора сервиса.
export const PORT_OPTS: [string, string][] = [
  ['22', 'SSH (22)'],
  ['5432', 'PostgreSQL (5432)'],
  ['80', 'HTTP (80)'],
  ['443', 'HTTPS (443)'],
  ['3389', 'RDP (3389)'],
  ['*', 'любой порт'],
]
const PORT_ORDER = new Map(PORT_OPTS.map(([p], i) => [p, i] as const))

export function selKey(sel: AclSelector): string {
  return `${sel.kind}:${sel.value}`
}

export function parseSel(s: string): AclSelector {
  const i = s.indexOf(':')
  const kind = (s.slice(0, i) || 'any') as AclSelector['kind']
  return { kind, value: s.slice(i + 1) }
}

export function selLabel(sel: AclSelector, nodes: Node[], t: Tr): string {
  if (sel.kind === 'any') return t('Любой')
  if (sel.kind === 'servers') return t('все серверы')
  if (sel.kind === 'internet') return t('🌐 интернет')
  if (sel.kind === 'cidr') return sel.value
  if (sel.kind === 'tag') return `#${sel.value}`
  return nodes.find((n) => n.id === sel.value)?.name ?? `#${sel.value}`
}

// Список ролей (тегов) во всей сети — из forced_tags нод, без префикса «tag:».
export function roleList(nodes: Node[]): string[] {
  const set = new Set<string>()
  for (const n of nodes) for (const tg of n.forced_tags) set.add(tg.replace(/^tag:/, ''))
  return [...set].sort()
}

export function portLabel(port: string, t: Tr): string {
  if (port === '*') return t('всё')
  const p = PORT_OPTS.find(([v]) => v === port)
  // показываем номер: пресет → «SSH (22)», свой порт → как есть (напр. «2221»)
  return p ? p[1] : port
}

// Известные порты — подсказка «что это за порт» при вводе своего.
const WELL_KNOWN: Record<string, string> = {
  '21': 'FTP', '22': 'SSH', '23': 'Telnet', '25': 'SMTP', '53': 'DNS',
  '80': 'HTTP', '110': 'POP3', '123': 'NTP', '143': 'IMAP', '161': 'SNMP',
  '389': 'LDAP', '443': 'HTTPS', '445': 'SMB', '465': 'SMTPS', '587': 'SMTP',
  '636': 'LDAPS', '993': 'IMAPS', '995': 'POP3S', '1194': 'OpenVPN',
  '1433': 'MS SQL', '1521': 'Oracle', '2049': 'NFS', '3306': 'MySQL/MariaDB',
  '3389': 'RDP', '5432': 'PostgreSQL', '5672': 'RabbitMQ', '5900': 'VNC',
  '6379': 'Redis', '8080': 'HTTP (alt)', '8443': 'HTTPS (alt)', '9090': 'Prometheus',
  '9200': 'Elasticsearch', '11211': 'Memcached', '27017': 'MongoDB', '51820': 'WireGuard',
}

// Подсказка под полем своего порта: что это за порт (или что он нестандартный).
export function portHint(port: string, t: Tr): string {
  const p = port.trim()
  if (!p) return ''
  if (!/^\d{1,5}$/.test(p) || Number(p) < 1 || Number(p) > 65535) return t('неверный порт')
  return WELL_KNOWN[p]
    ? t('порт {p} — обычно {name}', { p, name: WELL_KNOWN[p] })
    : t('порт {p} — нестандартный', { p })
}

// Разворачиваем правила в «одно правило = один порт» (сплит по запятой) с дедупом.
export function expandRules(rules: AclRule[]): AclRule[] {
  const out: AclRule[] = []
  const seen = new Set<string>()
  for (const r of rules) {
    for (const p of String(r.ports)
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)) {
      const k = `${selKey(r.src)}>${selKey(r.dst)}|${p}`
      if (seen.has(k)) continue
      seen.add(k)
      out.push({ src: r.src, dst: r.dst, ports: p })
    }
  }
  return out
}

// Включить/выключить связь src→dst:port, вернуть новый (развёрнутый) набор.
export function toggleRule(
  rules: AclRule[],
  src: AclSelector,
  dst: AclSelector,
  port: string,
  on: boolean,
): AclRule[] {
  const sk = selKey(src)
  const dk = selKey(dst)
  const next = expandRules(rules).filter(
    (r) => !(selKey(r.src) === sk && selKey(r.dst) === dk && r.ports === port),
  )
  if (on) next.push({ src, dst, ports: port })
  return next
}

// Группировка выдач: lens 'who' — по источнику (кому дали), 'server' — по назначению (кто ходит сюда).
export type GrantRow = { port: string; others: AclSelector[] }
export type Grant = { entity: AclSelector; rows: GrantRow[] }

const KIND_ORDER: Record<AclSelector['kind'], number> = {
  tag: 1,
  servers: 2,
  node: 3,
  internet: 4,
  cidr: 5,
  any: 6,
}

export function groupGrants(rules: AclRule[], lens: 'who' | 'server'): Grant[] {
  const map = new Map<
    string,
    { entity: AclSelector; ports: Map<string, Map<string, AclSelector>> }
  >()
  for (const r of expandRules(rules)) {
    const entity = lens === 'who' ? r.src : r.dst
    const other = lens === 'who' ? r.dst : r.src
    const ek = selKey(entity)
    let g = map.get(ek)
    if (!g) {
      g = { entity, ports: new Map() }
      map.set(ek, g)
    }
    let pm = g.ports.get(r.ports)
    if (!pm) {
      pm = new Map()
      g.ports.set(r.ports, pm)
    }
    pm.set(selKey(other), other)
  }
  const grants = [...map.values()].map((g) => ({
    entity: g.entity,
    rows: [...g.ports.entries()]
      .map(([port, others]) => ({ port, others: [...others.values()] }))
      .sort((a, b) => (PORT_ORDER.get(a.port) ?? 99) - (PORT_ORDER.get(b.port) ?? 99)),
  }))
  return grants.sort((a, b) => {
    const k = KIND_ORDER[a.entity.kind] - KIND_ORDER[b.entity.kind]
    return k !== 0 ? k : selKey(a.entity).localeCompare(selKey(b.entity))
  })
}
