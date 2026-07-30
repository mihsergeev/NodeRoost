const TOKEN_KEY = 'noderoost_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token)
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(path, { ...options, headers })
  if (res.status === 204) return undefined as T

  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const detail =
      typeof body?.detail === 'string' ? body.detail : `Ошибка HTTP ${res.status}`
    throw new ApiError(detail, res.status)
  }
  return body as T
}

// Смена пароля: возвращает новый токен (старые инвалидируются на бэкенде).
export async function changePassword(
  current_password: string,
  new_password: string,
): Promise<void> {
  const r = await api<{ access_token: string }>('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ current_password, new_password }),
  })
  setToken(r.access_token)
}

export type PanelConfig = {
  panel_ip: string
  headscale_server_url: string
  headscale_configured: boolean
}

export function getConfig(): Promise<PanelConfig> {
  return api<PanelConfig>('/api/config')
}

export type Health = {
  status: string
  db: string
  headscale: string
  version: string
}

// --- Ноды ---

export type Node = {
  id: string
  name: string
  hostname: string
  ip_addresses: string[]
  online: boolean
  last_seen: string | null
  expiry: string | null
  key_expired: boolean
  forced_tags: string[]
  tags: string[]
  created_at: string | null
  // сообщил о себе клиент Tailscale; могут быть пустыми (API headscale их не отдаёт)
  client_version: string
  os: string
  arch: string
  container: boolean
  endpoint: string
  country: string
  direct_ok: boolean
  available_routes: string[]
  approved_routes: string[]
  subnet_routes: string[]
  is_exit_node: boolean
  advertises_exit_node: boolean
  description: string
  kind: 'server' | 'device'
  admin: boolean
  muted: boolean
  exit_gateway: boolean  // сервер — шлюз выхода в интернет (exit-нода с уникальным тегом)
  exit_via: string[]     // устройство: id серверов-шлюзов, через которые ему разрешён выход
  force_exit: string     // id шлюза: весь трафик этой ноды принудительно через него (exit-node)
  group: string      // группировка списков: организация…
  subgroup: string   // …и проект внутри неё
}

export function listNodes(): Promise<Node[]> {
  return api<Node[]>('/api/nodes')
}

export function renameNode(id: string, name: string): Promise<Node> {
  return api<Node>(`/api/nodes/${id}/rename`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export function setNodeTags(id: string, tags: string[]): Promise<Node> {
  return api<Node>(`/api/nodes/${id}/tags`, {
    method: 'POST',
    body: JSON.stringify({ tags }),
  })
}

export function setNodeMeta(
  id: string,
  meta: {
    description: string
    kind: '' | 'server' | 'device'
    admin?: boolean
    muted?: boolean
    exit_gateway?: boolean
    exit_via?: string[]
    force_exit?: string
    group?: string
    subgroup?: string
  },
): Promise<Node> {
  return api<Node>(`/api/nodes/${id}/meta`, {
    method: 'POST',
    body: JSON.stringify(meta),
  })
}

// серверная сторона выбора выхода: каким устройствам разрешён выход через этот
// шлюз (обратная проекция device.exit_via — та же связь, редактируется с двух сторон)
export function setExitClients(id: string, devices: string[]): Promise<Node> {
  return api<Node>(`/api/nodes/${id}/exit-clients`, {
    method: 'POST',
    body: JSON.stringify({ devices }),
  })
}

export function expireNode(id: string): Promise<Node> {
  return api<Node>(`/api/nodes/${id}/expire`, { method: 'POST' })
}

export function setNodeRoutes(id: string, routes: string[]): Promise<Node> {
  return api<Node>(`/api/nodes/${id}/routes`, {
    method: 'POST',
    body: JSON.stringify({ routes }),
  })
}

export function deleteNode(id: string): Promise<void> {
  return api<void>(`/api/nodes/${id}`, { method: 'DELETE' })
}

// --- ACL-политика ---

export type Policy = {
  policy: string
  updated_at: string | null
  exists: boolean
}

export function getPolicy(): Promise<Policy> {
  return api<Policy>('/api/policy')
}

export function putPolicy(policy: string): Promise<Policy> {
  return api<Policy>('/api/policy', {
    method: 'PUT',
    body: JSON.stringify({ policy }),
  })
}

// --- визуальный конструктор ACL ---

export type AclSelector = {
  // internet = выход в интернет через exit-node (autogroup:internet);
  // cidr = конкретный IP/подсеть (value = «8.8.8.8» или «10.0.0.0/8»)
  kind: 'any' | 'node' | 'tag' | 'servers' | 'internet' | 'cidr'
  value: string
}
export type AclRule = { src: AclSelector; dst: AclSelector; ports: string }
export type AclRules = { rules: AclRule[]; generated: string }

export function getPolicyRules(): Promise<AclRules> {
  return api<AclRules>('/api/policy/rules')
}

export function putPolicyRules(rules: AclRule[]): Promise<AclRules> {
  return api<AclRules>('/api/policy/rules', {
    method: 'PUT',
    body: JSON.stringify({ rules }),
  })
}

// --- агент ноды: маршруты/exit задаются в панели, нода применяет сама ---
export type AgentCfg = {
  routes: string[]
  exit_node: boolean
  token: string
  installed: boolean
  last_poll: string | null
  setup_oneline: string
  remove_oneline: string
}
export function getAgent(nodeId: string): Promise<AgentCfg> {
  return api<AgentCfg>(`/api/agent/${nodeId}`)
}
export function setAgent(
  nodeId: string,
  routes: string[],
  exit_node: boolean,
): Promise<AgentCfg> {
  return api<AgentCfg>(`/api/agent/${nodeId}`, {
    method: 'PUT',
    body: JSON.stringify({ routes, exit_node }),
  })
}

// --- Маршрутизация: направления «кто → куда → через какую ноду» ---

export type Direction = {
  id: string
  // кто ходит: конкретные ноды (src) либо группа целиком, которая раскрывается
  // на стороне панели — новая нода подхватится сама
  src_kind: 'node' | 'devices' | 'servers'
  src: string[]
  full: boolean // УСТАРЕЛО: subnet-туннель убран, у старых записей true — показываем «устарело»
  dst: string // домен / IP / подсеть — то, что ввёл админ
  via: string // id ноды-выхода
  ports: string
  ips: string[] // во что резолвится dst сейчас
  resolved_at: string | null
  error: string // резолв не удался (адреса при этом остались прежними)
  active: boolean // нода-выход реально раздаёт маршрут
  via_agent: boolean // на ноде-выходе стоит агент
}

export function getDirections(): Promise<{ directions: Direction[] }> {
  return api<{ directions: Direction[] }>('/api/routing')
}

export function addDirection(
  src_kind: 'node' | 'devices' | 'servers',
  src: string[],
  dst: string,
  via: string,
  ports: string,
): Promise<Direction> {
  return api<Direction>('/api/routing', {
    method: 'POST',
    body: JSON.stringify({ src_kind, src, dst, via, ports }),
  })
}

export function delDirection(id: string): Promise<void> {
  return api<void>(`/api/routing/${id}`, { method: 'DELETE' })
}

// Перерезолвить адреса прямо сейчас — если сайт переехал и чинить надо сразу.
export function refreshDirections(): Promise<{ directions: Direction[] }> {
  return api<{ directions: Direction[] }>('/api/routing/refresh', { method: 'POST' })
}

export type ResolvedHost = { host: string; ips: string[]; note: string }

// Резолвит домен «сайта» в IP (ACL умеет только IP, не URL). IP/подсеть → как есть.
export function resolveHost(host: string): Promise<ResolvedHost> {
  return api<ResolvedHost>('/api/policy/resolve-host', {
    method: 'POST',
    body: JSON.stringify({ host }),
  })
}

// --- API-ключи headscale + инфо DNS/DERP ---

export type ApiKey = {
  id: string
  prefix: string
  expiration: string | null
  created_at: string | null
  last_seen: string | null
  is_panel: boolean
}

export type ApiKeyCreated = {
  api_key: string
  prefix: string
  expiration: string | null
}

export type HsInfo = {
  server_url: string
  dns: {
    magic_dns: boolean
    base_domain: string
    nameservers: string[]
    search_domains: string[]
    override_local_dns: boolean
  }
  derp: { embedded: boolean; urls: string[]; auto_update: boolean }
  ipv4_prefix: string
  allocation: string
}

export function listApiKeys(): Promise<ApiKey[]> {
  return api<ApiKey[]>('/api/apikeys')
}

export function createApiKey(expiration_days: number): Promise<ApiKeyCreated> {
  return api<ApiKeyCreated>('/api/apikeys', {
    method: 'POST',
    body: JSON.stringify({ expiration_days }),
  })
}

export function expireApiKey(prefix: string): Promise<void> {
  return api<void>('/api/apikeys/expire', {
    method: 'POST',
    body: JSON.stringify({ prefix }),
  })
}

export function getHsInfo(): Promise<HsInfo> {
  return api<HsInfo>('/api/hs-info')
}

export function setHsDns(dns: {
  magic_dns: boolean
  base_domain: string
  nameservers: string[]
}): Promise<HsInfo> {
  return api<HsInfo>('/api/hs-info/dns', {
    method: 'PUT',
    body: JSON.stringify(dns),
  })
}

export function setHsNetwork(net: {
  ipv4_prefix: string
  allocation: string
}): Promise<HsInfo> {
  return api<HsInfo>('/api/hs-info/network', {
    method: 'PUT',
    body: JSON.stringify(net),
  })
}

// --- журнал / логи / сводка ---

export type AuditEntry = {
  ts: string | null
  username: string
  action: string
  target: string
  detail: string
}

export type HeadscaleLogs = { available: boolean; text: string; note: string }

export type Summary = {
  panel_version: string
  headscale_url: string
  headscale_ok: boolean
  magic_dns: boolean
  base_domain: string
  nameservers: string[]
  derp_embedded: boolean
  nodes_total: number
  servers: number
  devices: number
  online: number
  last_backup: string
  last_backup_at: string | null
}

export function getAudit(limit = 200): Promise<AuditEntry[]> {
  return api<AuditEntry[]>(`/api/logs/audit?limit=${limit}`)
}

export function getHeadscaleLogs(): Promise<HeadscaleLogs> {
  return api<HeadscaleLogs>('/api/logs/headscale')
}

export function getSummary(): Promise<Summary> {
  return api<Summary>('/api/logs/summary')
}

// --- метрики + алерты ---

export type HistoryPoint = { ts: string; online: number; total: number }
export type MetricsHistory = { interval_seconds: number; points: HistoryPoint[] }

export function metricsHistory(hours = 24): Promise<MetricsHistory> {
  return api<MetricsHistory>(`/api/metrics/history?hours=${hours}`)
}

export type AlertConfig = {
  telegram_token: string
  telegram_chat: string
  telegram_api: string
  webhook: string
  enabled: boolean
}

export function getAlerts(): Promise<AlertConfig> {
  return api<AlertConfig>('/api/alerts')
}

export function putAlerts(cfg: {
  telegram_token: string
  telegram_chat: string
  telegram_api: string
  webhook: string
}): Promise<AlertConfig> {
  return api<AlertConfig>('/api/alerts', {
    method: 'PUT',
    body: JSON.stringify(cfg),
  })
}

export function testAlerts(): Promise<{ sent: boolean; errors: string[] }> {
  return api<{ sent: boolean; errors: string[] }>('/api/alerts/test', {
    method: 'POST',
  })
}

// --- бэкапы ---

export type BackupFile = { filename: string; size: number; created: string }
export type BackupConfig = { interval_hours: number; keep: number }

export function listBackups(): Promise<BackupFile[]> {
  return api<BackupFile[]>('/api/backup/list')
}

export function runBackup(): Promise<{
  filename: string
  size: number
  problems: string[]
}> {
  return api<{ filename: string; size: number; problems: string[] }>(
    '/api/backup/run',
    { method: 'POST' },
  )
}

export function getBackupConfig(): Promise<BackupConfig> {
  return api<BackupConfig>('/api/backup/config')
}

export function putBackupConfig(cfg: BackupConfig): Promise<BackupConfig> {
  return api<BackupConfig>('/api/backup/config', {
    method: 'PUT',
    body: JSON.stringify(cfg),
  })
}

// Скачивание бинарного архива: fetch с Bearer → blob → <a download>
export async function downloadBackup(name: string): Promise<void> {
  const token = getToken()
  const res = await fetch(`/api/backup/file/${encodeURIComponent(name)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new ApiError('Не удалось скачать бэкап', res.status)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// --- enroll (добавление ноды) ---

export type NodeOs = 'linux' | 'windows' | 'macos' | 'android'

export const OS_TABS: { os: NodeOs; label: string }[] = [
  { os: 'linux', label: 'Linux' },
  { os: 'windows', label: 'Windows' },
  { os: 'macos', label: 'macOS' },
  { os: 'android', label: 'Android' },
]

export type EnrollResult = {
  os: string
  hostname: string
  login_server: string
  script: string
  key_id: string
  expires_at: string
}

export type EnrollStatus = { connected: boolean; node: Node | null }

export function enrollNode(
  name: string,
  os: NodeOs,
  exitNode = false,
): Promise<EnrollResult> {
  return api<EnrollResult>('/api/enroll', {
    method: 'POST',
    body: JSON.stringify({ name, os, exit_node: exitNode }),
  })
}

export function enrollStatus(
  keyId: string,
  hostname: string,
): Promise<EnrollStatus> {
  const q = new URLSearchParams({ key_id: keyId, hostname })
  return api<EnrollStatus>(`/api/enroll/status?${q.toString()}`)
}

export function reconnectNode(id: string, os: NodeOs): Promise<EnrollResult> {
  return api<EnrollResult>(`/api/nodes/${id}/reconnect`, {
    method: 'POST',
    body: JSON.stringify({ os }),
  })
}

// --- версия клиента Tailscale ---
export type TsVersion = { current: string; env_default: string }
export type TsCheck = { ok: boolean; version: string; url: string; note: string }
export type TsLatest = { version: string; note: string }

export function getTsVersion(): Promise<TsVersion> {
  return api<TsVersion>('/api/tailscale-version')
}
export function setTsVersion(version: string): Promise<TsVersion> {
  return api<TsVersion>('/api/tailscale-version', {
    method: 'PUT',
    body: JSON.stringify({ version }),
  })
}
export function tsLatest(): Promise<TsLatest> {
  return api<TsLatest>('/api/tailscale-version/latest')
}
export function tsCheck(version: string): Promise<TsCheck> {
  return api<TsCheck>(`/api/tailscale-version/check?version=${encodeURIComponent(version)}`)
}

// --- локальный мирор бинарей Tailscale ---
export type TsMirrorFile = { name: string; ok: boolean; size: number; note: string }
export type TsMirror = { version: string; files: TsMirrorFile[] }

export function getTsMirror(): Promise<TsMirror> {
  return api<TsMirror>('/api/tailscale-version/mirror')
}
export function downloadTsMirror(): Promise<TsMirror> {
  return api<TsMirror>('/api/tailscale-version/mirror', { method: 'POST' })
}

export type TwoFAStatus = { enabled: boolean }
export type TwoFASetup = { secret: string; otpauth_uri: string }

export function get2FA(): Promise<TwoFAStatus> {
  return api<TwoFAStatus>('/api/auth/2fa')
}

export function setup2FA(): Promise<TwoFASetup> {
  return api<TwoFASetup>('/api/auth/2fa/setup', { method: 'POST' })
}

export function enable2FA(otp: string): Promise<TwoFAStatus> {
  return api<TwoFAStatus>('/api/auth/2fa/enable', {
    method: 'POST',
    body: JSON.stringify({ otp }),
  })
}

export function disable2FA(otp: string): Promise<TwoFAStatus> {
  return api<TwoFAStatus>('/api/auth/2fa/disable', {
    method: 'POST',
    body: JSON.stringify({ otp }),
  })
}
