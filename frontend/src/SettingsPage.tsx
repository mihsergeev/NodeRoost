import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  listNodes,
  type Node,
  createApiKey,
  downloadTsMirror,
  expireApiKey,
  getDnsRecords,
  getHsInfo,
  getTsMirror,
  getTsVersion,
  listApiKeys,
  setDnsRecords,
  setHsDns,
  setHsNetwork,
  setTsVersion,
  tsCheck,
  tsLatest,
  type ApiKey,
  type ApiKeyCreated,
  type DnsRecords,
  type HsInfo,
  type TsMirror,
} from './api'
import { useI18n } from './i18n'

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : '—'
}

function fmtBytes(n: number): string {
  if (n <= 0) return '0'
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} КБ`
  return `${(n / 1024 / 1024).toFixed(1)} МБ`
}

// Разбор IPv4-CIDR → сколько адресов и границы диапазона (для подсказки).
function cidrInfo(cidr: string): { count: number; first: string; last: string } | null {
  const m = cidr.trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/)
  if (!m) return null
  const octets = [m[1], m[2], m[3], m[4]].map(Number)
  const p = Number(m[5])
  if (p > 32 || octets.some((o) => o > 255)) return null
  const base = ((octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]) >>> 0
  const mask = p === 0 ? 0 : (0xffffffff << (32 - p)) >>> 0
  const net = (base & mask) >>> 0
  const size = 2 ** (32 - p)
  const last = (net + size - 1) >>> 0
  const toIp = (n: number) => [24, 16, 8, 0].map((s) => (n >>> s) & 255).join('.')
  return { count: size, first: toIp(net), last: toIp(last) }
}

const CIDR_EXAMPLES = ['100.64.0.0/10', '100.80.0.0/12', '100.100.0.0/16', '100.64.7.0/24']

// строка формы «имена внутри сети»: цель — либо нода, либо адрес руками
type RecRow = { name: string; node_id: string; ip: string }

function rowsOf(r: DnsRecords): RecRow[] {
  return r.records.map((x) => ({ name: x.name, node_id: x.node_id, ip: x.ip }))
}

// Сообщение, когда правку записали, а перезапустить headscale некому.
const HELPER_MISSING =
  'Изменения записаны, но headscale не перезапущен: на хосте нет помощника. Выполните на сервере `sudo ops/update.sh` (он его поставит) или перезапустите вручную: `docker compose restart headscale`.'

export function SettingsPage({ onUnauthorized }: { onUnauthorized: () => void }) {
  const { t } = useI18n()
  const [keys, setKeys] = useState<ApiKey[] | null>(null)
  const [info, setInfo] = useState<HsInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [days, setDays] = useState(90)
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState<ApiKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)
  // редактор DNS
  const [dnsMagic, setDnsMagic] = useState(false)
  // подменять ли резолвер на самих нодах (см. подпись у галочки)
  const [dnsOverride, setDnsOverride] = useState(false)
  const [dnsDomain, setDnsDomain] = useState('')
  const [dnsServers, setDnsServers] = useState('')
  const [dnsBusy, setDnsBusy] = useState(false)
  const [dnsMsg, setDnsMsg] = useState<string | null>(null)
  const [dnsErr, setDnsErr] = useState<string | null>(null)
  // редактор сети меша
  const [netV4, setNetV4] = useState('')
  const [netAlloc, setNetAlloc] = useState('sequential')
  const [netBusy, setNetBusy] = useState(false)
  const [netMsg, setNetMsg] = useState<string | null>(null)
  const [netErr, setNetErr] = useState<string | null>(null)
  // версия клиента Tailscale
  const [tsCur, setTsCur] = useState('')
  const [tsInput, setTsInput] = useState('')
  const [tsBusy, setTsBusy] = useState(false)
  const [tsMsg, setTsMsg] = useState<string | null>(null)
  const [tsErr, setTsErr] = useState<string | null>(null)
  // Последняя версия из pkgs.tailscale.com. Тянем при открытии страницы; на
  // бэкенде ответ кэшируется на сутки, так что реальный поход наружу — раз в день.
  const [tsUpstream, setTsUpstream] = useState('')
  // DNS и диапазон меша настраиваются один раз и почти никогда не меняются, а
  // цена случайной правки высокая (рестарт headscale, смена MagicDNS-имён).
  // Поэтому карточки по умолчанию закрыты на «Изменить».
  const [dnsLocked, setDnsLocked] = useState(true)
  const [netLocked, setNetLocked] = useState(true)
  // ноды нужны карточке DERP: релей важен ровно настолько, насколько ноды НЕ
  // смогли соединиться напрямую — это и показываем вместо голого конфига
  const [nodes, setNodes] = useState<Node[]>([])
  // локальный мирор бинарей
  const [mirror, setMirror] = useState<TsMirror | null>(null)
  const [mirrorBusy, setMirrorBusy] = useState(false)
  // имена внутри сети: строки формы и то, что сейчас на сервере
  const [recRows, setRecRows] = useState<RecRow[]>([])
  const [recs, setRecs] = useState<DnsRecords | null>(null)
  const [recBusy, setRecBusy] = useState(false)
  const [recMsg, setRecMsg] = useState<string | null>(null)
  const [recErr, setRecErr] = useState<string | null>(null)

  // заполняет ОБЕ формы — и DNS, и сеть меша: обе читаются из одного hs-info.
  // Прежнее имя seedDns вводило в заблуждение, из-за него «Отменить» в карточке
  // сети сбрасывала соседнюю форму.
  function seedForms(i: HsInfo) {
    setDnsMagic(i.dns.magic_dns)
    setDnsOverride(i.dns.override_local_dns)
    setDnsDomain(i.dns.base_domain)
    setDnsServers(i.dns.nameservers.join(', '))
    setNetV4(i.ipv4_prefix)
    setNetAlloc(i.allocation || 'sequential')
  }

  const loadKeys = useCallback(async () => {
    try {
      setKeys(await listApiKeys())
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setError(err instanceof Error ? err.message : t('Ошибка'))
      setKeys([])
    }
  }, [onUnauthorized, t])

  useEffect(() => {
    loadKeys()
    listNodes()
      .then(setNodes)
      .catch(() => {
        /* не смогли — просто не показываем строку про прямые соединения */
      })
    getHsInfo()
      .then((i) => {
        setInfo(i)
        seedForms(i)
      })
      .catch(() => {})
    getTsVersion()
      .then((v) => {
        setTsCur(v.current)
        setTsInput(v.current)
      })
      .catch(() => {})
    getTsMirror()
      .then(setMirror)
      .catch(() => {})
    getDnsRecords()
      .then((r) => {
        setRecs(r)
        setRecRows(rowsOf(r))
      })
      .catch(() => {})
  }, [loadKeys])

  const parsedServers = dnsServers
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const dnsDirty =
    !!info &&
    (dnsMagic !== info.dns.magic_dns ||
      dnsOverride !== info.dns.override_local_dns ||
      dnsDomain.trim() !== info.dns.base_domain ||
      parsedServers.join(',') !== info.dns.nameservers.join(','))

  async function applyDns() {
    if (
      !window.confirm(
        t(
          'Применить изменения DNS? headscale перезапустится (~10–15 c), на это время регистрация нод приостановится. Смена базового домена меняет MagicDNS-имена всех нод.',
        ),
      )
    )
      return
    setDnsBusy(true)
    setDnsErr(null)
    setDnsMsg(null)
    try {
      const updated = await setHsDns({
        magic_dns: dnsMagic,
        override_local_dns: dnsOverride,
        base_domain: dnsDomain.trim(),
        nameservers: parsedServers,
      })
      setInfo(updated)
      seedForms(updated)
      setDnsLocked(true)
      setDnsMsg(t('Сохранено. headscale перезапускается…'))
      window.setTimeout(() => {
        getHsInfo()
          .then((i) => {
            setInfo(i)
            seedForms(i)
            // Перезапускает headscale хостовый помощник. Если флаг ещё на месте,
            // значит помощника нет (или он сломан): правка записана, но не
            // действует — и сказать об этом должна панель, а не тишина.
            if (i.restart_pending) setDnsErr(t(HELPER_MISSING))
          })
          .catch(() => {})
      }, 9000)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setDnsErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setDnsBusy(false)
    }
  }

  const recDirty =
    !!recs && JSON.stringify(recRows) !== JSON.stringify(rowsOf(recs))

  function updateRec(i: number, patch: Partial<RecRow>) {
    setRecRows(recRows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  // на что имя ведёт прямо сейчас: адрес берётся у ноды, а не запоминается
  function nodeAddr(id: string): string {
    return nodes.find((n) => n.id === id)?.ip_addresses.join(', ') || ''
  }

  async function applyRecords() {
    // Первое имя прописывает путь к файлу в config.yaml — только это и требует
    // перезапуска. Дальше headscale перечитывает файл сам, и предупреждать не о чем.
    if (
      recs &&
      !recs.active &&
      !window.confirm(
        t(
          'Первое имя нужно один раз показать headscale: он перезапустится (~10–15 c), на это время регистрация нод приостановится. Дальнейшие правки применяются без перезапуска.',
        ),
      )
    )
      return
    setRecBusy(true)
    setRecErr(null)
    setRecMsg(null)
    try {
      const rows = recRows
        .map((r) => ({ name: r.name.trim(), node_id: r.node_id, ip: r.ip.trim() }))
        .filter((r) => r.name)
      const updated = await setDnsRecords(rows)
      setRecs(updated)
      setRecRows(rowsOf(updated))
      setRecMsg(
        updated.restart_pending
          ? t('Сохранено. headscale перезапускается…')
          : t('Сохранено — имена уже раздаются нодам'),
      )
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setRecErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setRecBusy(false)
    }
  }

  const netDirty =
    !!info &&
    (netV4.trim() !== info.ipv4_prefix || netAlloc !== (info.allocation || 'sequential'))

  async function applyNet() {
    if (
      !window.confirm(
        t(
          'Сменить диапазон меша? headscale перезапустится (~10–15 c). Существующие ноды сохранят старые IP — новый диапазон только для новых нод. Меняйте это на пустой/новой сети.',
        ),
      )
    )
      return
    setNetBusy(true)
    setNetErr(null)
    setNetMsg(null)
    try {
      const updated = await setHsNetwork({
        ipv4_prefix: netV4.trim(),
        allocation: netAlloc,
      })
      setInfo(updated)
      seedForms(updated)
      setNetLocked(true)
      setNetMsg(t('Сохранено. headscale перезапускается…'))
      window.setTimeout(() => {
        getHsInfo()
          .then((i) => {
            setInfo(i)
            seedForms(i)
            // Перезапускает headscale хостовый помощник. Если флаг ещё на месте,
            // значит помощника нет (или он сломан): правка записана, но не
            // действует — и сказать об этом должна панель, а не тишина.
            if (i.restart_pending) setNetErr(t(HELPER_MISSING))
          })
          .catch(() => {})
      }, 9000)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setNetErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setNetBusy(false)
    }
  }

  useEffect(() => {
    tsLatest()
      .then((r) => setTsUpstream(r.version || ''))
      .catch(() => {
        /* не смогли узнать — просто не показываем подсказку */
      })
  }, [])

  const cmpVer = (a: string, b: string) => {
    const pa = a.split('.').map(Number)
    const pb = b.split('.').map(Number)
    for (let i = 0; i < 3; i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0)
    return 0
  }
  const tsOutdated = !!tsUpstream && !!tsCur && cmpVer(tsUpstream, tsCur) > 0

  const tsDirty = tsInput.trim() !== tsCur && /^\d+\.\d+\.\d+$/.test(tsInput.trim())

  async function tsFetchLatest() {
    setTsBusy(true)
    setTsErr(null)
    setTsMsg(null)
    try {
      const r = await tsLatest()
      if (r.version) {
        setTsInput(r.version)
        setTsMsg(t('Последняя официальная: {v}', { v: r.version }))
      } else setTsErr(r.note || t('Не удалось получить версию'))
    } finally {
      setTsBusy(false)
    }
  }

  async function tsDoCheck() {
    const v = tsInput.trim()
    setTsBusy(true)
    setTsErr(null)
    setTsMsg(null)
    try {
      const r = await tsCheck(v)
      if (r.ok) setTsMsg(t('Версия {v} доступна для скачивания ✓', { v }))
      else setTsErr(r.note || t('Версия недоступна'))
    } finally {
      setTsBusy(false)
    }
  }

  async function tsApply() {
    const v = tsInput.trim()
    setTsBusy(true)
    setTsErr(null)
    setTsMsg(null)
    try {
      const check = await tsCheck(v)
      if (!check.ok) {
        setTsErr(check.note || t('Версия недоступна — сначала проверьте'))
        return
      }
      const r = await setTsVersion(v)
      setTsCur(r.current)
      setTsInput(r.current)
      setTsMsg(t('Сохранено. Новые ноды будут ставить {v}.', { v: r.current }))
      // версия сменилась — статус мирора для новой версии другой
      getTsMirror().then(setMirror).catch(() => {})
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setTsErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setTsBusy(false)
    }
  }

  async function mirrorDownload() {
    setMirrorBusy(true)
    setTsErr(null)
    setTsMsg(null)
    try {
      const r = await downloadTsMirror()
      setMirror(r)
      const bad = r.files.filter((f) => !f.ok)
      if (bad.length) setTsErr(t('Не скачались: {n}', { n: bad.map((f) => f.name).join(', ') }))
      else setTsMsg(t('Мирор обновлён: {n} файлов', { n: r.files.length }))
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setTsErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setMirrorBusy(false)
    }
  }

  async function create() {
    setCreating(true)
    setError(null)
    try {
      setNewKey(await createApiKey(days))
      setShowCreate(false)
      loadKeys()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setCreating(false)
    }
  }

  async function expire(k: ApiKey) {
    if (
      !window.confirm(
        t('Отозвать ключ {prefix}…? Приложения с ним потеряют доступ.', {
          prefix: k.prefix,
        }),
      )
    )
      return
    try {
      await expireApiKey(k.prefix)
      loadKeys()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else window.alert(err instanceof Error ? err.message : t('Ошибка'))
    }
  }

  async function copyKey() {
    if (!newKey) return
    try {
      await navigator.clipboard.writeText(newKey.api_key)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard недоступен */
    }
  }

  return (
    <>
      <div className="page-head">
        <h2>{t('Настройки')}</h2>
      </div>

      {/* --- API-ключи --- */}
      <div className="card settings-section">
        <div className="clients-head">
          <h3>{t('API-ключи headscale')}</h3>
          <button
            onClick={() => {
              setShowCreate((v) => !v)
              setNewKey(null)
            }}
          >
            {t('Создать ключ')}
          </button>
        </div>
        <p className="muted small">
          {t(
            'Ключи доступа к управляющему API headscale (их использует эта панель и сторонние клиенты).',
          )}
        </p>

        {showCreate && (
          <div className="apikey-create">
            <label className="field-inline">
              <span>{t('Срок действия')}</span>
              <select
                className="select"
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
              >
                <option value={30}>30 {t('дн')}</option>
                <option value={90}>90 {t('дн')}</option>
                <option value={365}>365 {t('дн')}</option>
                <option value={3650}>10 {t('лет')}</option>
              </select>
            </label>
            <button onClick={create} disabled={creating}>
              {creating ? t('Создание…') : t('Создать')}
            </button>
          </div>
        )}

        {newKey && (
          <div className="apikey-box">
            <p className="form-ok small">
              {t('Ключ создан — скопируйте сейчас, снова он не покажется:')}
            </p>
            <code className="apikey-value">{newKey.api_key}</code>
            <div className="enroll-actions">
              <button onClick={copyKey}>
                {copied ? t('Скопировано ✓') : t('Скопировать')}
              </button>
              <button className="ghost" onClick={() => setNewKey(null)}>
                {t('Закрыть')}
              </button>
            </div>
          </div>
        )}

        {error && <p className="form-error">{error}</p>}

        {keys === null ? (
          <p className="muted">{t('загрузка…')}</p>
        ) : keys.length === 0 ? (
          <p className="muted small">{t('Ключей нет.')}</p>
        ) : (
          <div className="table-scroll">
            <table className="keys-table">
              <thead>
                <tr>
                  <th>{t('Префикс')}</th>
                  <th>{t('Создан')}</th>
                  <th>{t('Истекает')}</th>
                  <th>{t('Использован')}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td>
                      <span className="mono">{k.prefix}…</span>{' '}
                      {k.is_panel && <span className="pill-ok">{t('панель')}</span>}
                    </td>
                    <td className="muted">{fmtDate(k.created_at)}</td>
                    <td className="muted">{fmtDate(k.expiration)}</td>
                    <td className="muted">{fmtDate(k.last_seen)}</td>
                    <td>
                      {!k.is_panel && (
                        <button className="ghost danger" onClick={() => expire(k)}>
                          {t('Отозвать')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* --- версия клиента Tailscale --- */}
      <div className="card">
        <h3>{t('Версия клиента Tailscale')}</h3>
        <p className="muted small">
          {t('Свою пиновую версию ставят все enroll-скрипты — чтобы обновление официального клиента не сломало подключение. Обновляйте вручную, проверив совместимость.')}
        </p>
        <div className="ts-ver-row">
          <span className="muted small">{t('Текущая:')}</span>
          <code className="code-inline">{tsCur || '—'}</code>
          {tsOutdated && (
            <button
              className="tag-chip tag-warn tag-action"
              onClick={() => setTsInput(tsUpstream)}
              title={t('Подставить эту версию в поле')}
            >
              {t('вышла {v}', { v: tsUpstream })}
            </button>
          )}
          <input
            className="ts-ver-input"
            value={tsInput}
            onChange={(e) => setTsInput(e.target.value)}
            placeholder="1.98.8"
          />
          <button className="ghost small" onClick={tsFetchLatest} disabled={tsBusy}>
            {t('Из официальной')}
          </button>
          <button className="ghost small" onClick={tsDoCheck} disabled={tsBusy || !tsInput.trim()}>
            {t('Проверить')}
          </button>
          <button onClick={tsApply} disabled={tsBusy || !tsDirty}>
            {tsBusy ? t('…') : t('Применить')}
          </button>
        </div>
        {tsErr && <p className="form-error">{tsErr}</p>}
        {tsMsg && <p className="form-ok">{tsMsg}</p>}

        {/* локальный мирор бинарей — раздаётся с hs-домена (/pkgs), enroll-скрипты
            тянут отсюда, фолбэк — pkgs.tailscale.com */}
        <div className="ts-mirror">
          <div className="ts-mirror-head">
            <span className="muted small">
              {t('Локальный мирор бинарей')}
              {mirror ? <> · <code className="code-inline">{mirror.version}</code></> : null}
            </span>
            <button className="ghost small" onClick={mirrorDownload} disabled={mirrorBusy}>
              {mirrorBusy ? t('Загрузка…') : t('Загрузить в мирор')}
            </button>
          </div>
          <p className="muted small">
            {t('Enroll-скрипты качают клиент с нашего сервера (hs-домен /pkgs), фолбэк — официальный pkgs.tailscale.com. Скачайте текущую версию в мирор, чтобы не зависеть от tailscale.com.')}
          </p>
          {mirror && (
            <ul className="ts-mirror-list">
              {mirror.files.map((f) => (
                <li key={f.name} className={f.ok ? 'ok' : 'miss'}>
                  <span className="dot" aria-hidden>{f.ok ? '●' : '○'}</span>
                  <code>{f.name}</code>
                  <span className="muted small">
                    {f.ok ? fmtBytes(f.size) : f.note || t('нет')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* --- DNS + DERP + сеть меша --- */}
      {info && (
        <>
        <div className="settings-grid">
          <div className="card">
            <div className="clients-head">
              <h3>{t('DNS / MagicDNS')}</h3>
              {dnsLocked ? (
                <button className="ghost small" onClick={() => setDnsLocked(false)}>
                  {t('Изменить')}
                </button>
              ) : (
                <button
                  className="ghost small"
                  onClick={() => {
                    if (info) seedForms(info)
                    setDnsLocked(true)
                  }}
                >
                  {t('Отменить')}
                </button>
              )}
            </div>
            {/* галочка слева от подписи: так она привязана к тексту, а не уезжает
                к правому краю карточки, где выглядит оторванной от него */}
            <label className="field field-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={dnsMagic}
                disabled={dnsLocked}
                onChange={(e) => setDnsMagic(e.target.checked)}
              />
              <span>MagicDNS</span>
            </label>
            <label className="field">
              <span>{t('Базовый домен')}</span>
              <input
                value={dnsDomain}
                disabled={dnsLocked}
                onChange={(e) => setDnsDomain(e.target.value)}
                placeholder="noderoost.internal"
              />
            </label>
            <label className="field">
              <span>{t('DNS-серверы (через запятую)')}</span>
              <input
                value={dnsServers}
                disabled={dnsLocked}
                onChange={(e) => setDnsServers(e.target.value)}
                placeholder="1.1.1.1, 1.0.0.1"
              />
            </label>
            <label className="field field-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={dnsOverride}
                disabled={dnsLocked || parsedServers.length === 0}
                onChange={(e) => setDnsOverride(e.target.checked)}
              />
              <span>{t('Использовать только эти серверы')}</span>
            </label>
            <p className="muted small settings-note">
              {t('Без галочки ноды продолжают пользоваться своим DNS, а эти серверы добавляются к нему. С галочкой весь DNS ноды идёт только сюда — сервер перестанет видеть внутренние имена, которые знал его прежний резолвер.')}
            </p>
            {dnsErr && <p className="form-error">{dnsErr}</p>}
            {dnsMsg && <p className="form-ok">{dnsMsg}</p>}
            <div className="dns-actions">
              <button onClick={applyDns} disabled={dnsBusy || dnsLocked || !dnsDirty}>
                {dnsBusy ? t('Применение…') : t('Применить')}
              </button>
            </div>
            <p className="muted small settings-note">
              {t('Применяется с перезапуском headscale (~10–15 c). Смена базового домена меняет MagicDNS-имена всех нод.')}
            </p>
          </div>

          <div className="card">
            <h3>DERP</h3>
            <div className="info-row">
              <span className="muted small">{t('Встроенный DERP')}</span>
              <span>{info.derp.embedded ? t('вкл') : t('выкл')}</span>
            </div>
            <div className="info-row">
              <span className="muted small">{t('Чужие релеи')}</span>
              <span className={info.derp.urls.length ? 'mono small' : ''}>
                {info.derp.urls.join(', ') || t('нет — только свой')}
              </span>
            </div>
            <div className="info-row">
              <span className="muted small">{t('Тянуть чужую карту релеев')}</span>
              <span>{info.derp.auto_update ? t('вкл') : t('выкл')}</span>
            </div>
            {nodes.length > 0 && (
              <div className="info-row">
                <span className="muted small">{t('Ноды соединяются напрямую')}</span>
                <span>
                  {t('{n} из {total}', {
                    n: nodes.filter((n) => n.direct_ok).length,
                    total: nodes.length,
                  })}
                </span>
              </div>
            )}
            <p className="muted small settings-note">
              {t('Релей нужен только там, где ноды не смогли соединиться напрямую (NAT). Пока все ходят напрямую, он почти не используется. Чужих релеев нет и автообновление выключено намеренно: иначе подтянулась бы публичная карта Tailscale и трафик пошёл бы через сторонние серверы. Меняется в config.yaml headscale — в панели не даём, это разовая настройка всей сети.')}
            </p>
          </div>
        </div>

        <div className="card">
          <div className="clients-head">
            <h3>{t('Имена внутри сети')}</h3>
            <span className="muted small">
              {!recs || !recs.active
                ? t('пока не настроено')
                : recs.restart_pending
                  ? t('записано, ждёт перезапуска headscale')
                  : t('раздаётся нодам')}
            </span>
          </div>
          <p className="muted small settings-note">
            {t(
              'Имя ведёт на адрес в сети — но только для машин сети. Публичный DNS панель не трогает: снаружи имя ведёт туда же, куда вело, и кто не в сети — заходит как раньше. Так к сервису, закрытому вайтлистом, ходят по внутреннему адресу.',
            )}
          </p>
          {recRows.map((r, i) => (
            <div className="dns-rec-row" key={i}>
              <input
                type="text"
                value={r.name}
                placeholder="panel.example.com"
                onChange={(e) => updateRec(i, { name: e.target.value })}
              />
              <select
                value={r.node_id || 'ip'}
                onChange={(e) =>
                  updateRec(
                    i,
                    e.target.value === 'ip'
                      ? { node_id: '', ip: '' }
                      : { node_id: e.target.value, ip: '' },
                  )
                }
              >
                {nodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
                <option value="ip">{t('адрес вручную')}</option>
              </select>
              {r.node_id ? (
                <span className="dns-rec-addr mono small">
                  {nodeAddr(r.node_id) || t('нода не найдена')}
                </span>
              ) : (
                <input
                  type="text"
                  value={r.ip}
                  placeholder="192.168.1.10"
                  onChange={(e) => updateRec(i, { ip: e.target.value })}
                />
              )}
              <button
                className="ghost small dns-rec-drop"
                onClick={() => setRecRows(recRows.filter((_, j) => j !== i))}
              >
                {t('Убрать')}
              </button>
            </div>
          ))}
          {recErr && <p className="form-error">{recErr}</p>}
          {recMsg && <p className="form-ok">{recMsg}</p>}
          <div className="dns-actions">
            <button
              className="ghost small"
              onClick={() =>
                // новая строка целится в ноду: имя на адрес ноды — обычный случай,
                // ручной адрес нужен для того, что стоит ЗА нодой (NAS, IPMI, камера)
                setRecRows([...recRows, { name: '', node_id: nodes[0]?.id ?? '', ip: '' }])
              }
            >
              {t('Добавить имя')}
            </button>
            <button onClick={applyRecords} disabled={recBusy || !recDirty}>
              {recBusy ? t('Сохранение…') : t('Сохранить')}
            </button>
          </div>
          <p className="muted small settings-note">
            {t(
              'Имя получают все машины сети — включая те, которым доступ к этому серверу не открыт: у них оно перестанет открываться совсем, наружу за ним они больше не пойдут. Записи применяются без перезапуска headscale; исключение — самое первое имя.',
            )}
          </p>
        </div>

        <div className="card mesh-net-card">
          <div className="clients-head">
            <h3>{t('Сеть меша (IP-диапазон)')}</h3>
            {netLocked ? (
              <button className="ghost small" onClick={() => setNetLocked(false)}>
                {t('Изменить')}
              </button>
            ) : (
              <button
                className="ghost small"
                onClick={() => {
                  if (info) seedForms(info)
                  setNetLocked(true)
                }}
              >
                {t('Отменить')}
              </button>
            )}
          </div>
          <label className="field">
            <span>{t('IPv4-диапазон (внутри 100.64.0.0/10)')}</span>
            <input
              value={netV4}
              disabled={netLocked}
              onChange={(e) => setNetV4(e.target.value)}
              placeholder="100.64.0.0/10"
            />
            {(() => {
              const info = cidrInfo(netV4)
              return info ? (
                <span className="muted small">
                  {t('получится: {n} адресов ({from} – {to})', {
                    n: info.count.toLocaleString('ru-RU'),
                    from: info.first,
                    to: info.last,
                  })}
                </span>
              ) : null
            })()}
          </label>
          <div className="cidr-examples">
            <span className="muted small">{t('Примеры:')}</span>
            {CIDR_EXAMPLES.map((ex) => {
              const info = cidrInfo(ex)
              return (
                <button
                  key={ex}
                  type="button"
                  className="cidr-chip"
                  disabled={netLocked}
                  onClick={() => setNetV4(ex)}
                  title={info ? `${info.first} – ${info.last}` : ex}
                >
                  {ex} · {info ? info.count.toLocaleString('ru-RU') : '—'}
                </button>
              )
            })}
          </div>
          <label className="field">
            <span>{t('Распределение адресов')}</span>
            <select
              className="select"
              value={netAlloc}
              disabled={netLocked}
              onChange={(e) => setNetAlloc(e.target.value)}
            >
              <option value="sequential">{t('последовательно (.1, .2, .3…)')}</option>
              <option value="random">{t('случайно')}</option>
            </select>
          </label>
          {netErr && <p className="form-error">{netErr}</p>}
          {netMsg && <p className="form-ok">{netMsg}</p>}
          <div className="dns-actions">
            <button onClick={applyNet} disabled={netBusy || netLocked || !netDirty}>
              {netBusy ? t('Применение…') : t('Применить')}
            </button>
          </div>
          <p className="muted small settings-note">
            {t('IPv4 — только внутри 100.64.0.0/10 (Tailscale CGNAT). Существующие ноды сохранят старые IP; смена применяется с перезапуском headscale.')}
          </p>
        </div>
        </>
      )}
    </>
  )
}
