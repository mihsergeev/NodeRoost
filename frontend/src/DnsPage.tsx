import { useCallback, useEffect, useEffectEvent, useState } from 'react'
import {
  ApiError,
  downloadCa,
  setCa,
  getDnsRecords,
  getHsInfo,
  listNodes,
  setDnsRecords,
  setHsDns,
  type DnsRecords,
  type HsInfo,
  type Node,
} from './api'
import { useI18n } from './i18n'
import { useHeadscaleRestart } from './useHeadscaleRestart'

type Props = { onUnauthorized: () => void }

// Грубая проверка «имя в настоящем домене» — только чтобы предупредить заранее.
// Точный список (вся корневая зона IANA) живёт в панели и зашит в сам корень.
const PUBLIC_TLD_HINT =
  /\.(com|net|org|ru|io|dev|app|co|me|info|biz|pro|site|online|xyz|club|shop|cloud|tech|store|su|ua|by|kz|de|fr|uk|us|cn|jp|tv|cc|zip|mov)$/

// строка формы: цель — либо нода (адрес подставляется её текущий), либо адрес
// руками (для того, что стоит ЗА нодой и куда клиента не поставить)
type RecRow = {
  name: string
  node_id: string
  ip: string
  enabled: boolean
  cert: boolean
}

function rowsOf(r: DnsRecords): RecRow[] {
  return r.records.map((x) => ({
    name: x.name,
    node_id: x.node_id,
    ip: x.ip,
    enabled: x.enabled,
    cert: x.cert,
  }))
}

export function DnsPage({ onUnauthorized }: Props) {
  const { t } = useI18n()
  const [recs, setRecs] = useState<DnsRecords | null>(null)
  const [rows, setRows] = useState<RecRow[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [caErr, setCaErr] = useState<string | null>(null)
  // перевыпуск корня: открывается по кнопке и требует подтверждения — он
  // обесценивает всё, что подписано, и заставляет обойти устройства заново
  const [rotOpen, setRotOpen] = useState(false)
  const [years, setYears] = useState(20)
  const [caBusy, setCaBusy] = useState(false)

  // резолверы и MagicDNS — вторая половина раздела: настраивается один раз и
  // почти никогда не меняется, поэтому карточка закрыта на «Изменить»
  const [info, setInfo] = useState<HsInfo | null>(null)
  const [magic, setMagic] = useState(false)
  const [override, setOverride] = useState(false)
  const [domain, setDomain] = useState('')
  const [servers, setServers] = useState('')
  const [srvLocked, setSrvLocked] = useState(true)
  const [srvBusy, setSrvBusy] = useState(false)
  const [srvSaved, setSrvSaved] = useState(false)
  const [srvErr, setSrvErr] = useState<string | null>(null)
  const pending = !!(recs?.restart_pending || info?.restart_pending)

  const markApplied = useCallback(() => {
    setInfo((prev) => prev && { ...prev, restart_pending: false })
    setRecs((prev) => prev && { ...prev, restart_pending: false })
  }, [])
  useHeadscaleRestart(pending && !busy && !srvBusy, markApplied, onUnauthorized)

  const handle = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setError(err instanceof Error ? err.message : t('Ошибка'))
    },
    [onUnauthorized, t],
  )
  const handleLoadError = useEffectEvent(handle)

  function seed(i: HsInfo) {
    setMagic(i.dns.magic_dns)
    setOverride(i.dns.override_local_dns)
    setDomain(i.dns.base_domain)
    setServers(i.dns.nameservers.join(', '))
  }

  useEffect(() => {
    let active = true
    listNodes()
      .then((next) => {
        if (active) setNodes(next)
      })
      .catch(() => {
        /* без списка нод останется только ручной адрес */
      })
    getDnsRecords()
      .then((r) => {
        if (!active) return
        setRecs(r)
        setRows(rowsOf(r))
      })
      .catch((err) => {
        if (active) handleLoadError(err)
      })
    getHsInfo()
      .then((i) => {
        if (!active) return
        setInfo(i)
        seed(i)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [])

  const dirty = !!recs && JSON.stringify(rows) !== JSON.stringify(rowsOf(recs))

  function update(i: number, patch: Partial<RecRow>) {
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  // Корень не подписывает имена в настоящих доменах — иначе он мог бы подделать
  // сайт банка для машин, на которых стоит. Имя вида nas.example.com сертификата
  // не получит, и сказать об этом надо здесь, а не в строке ошибки после.
  const caZones = recs?.ca.suffixes ?? []
  const badNames = rows
    .filter((r) => r.cert && r.name.includes('.'))
    .map((r) => r.name.trim().toLowerCase())
    .filter((n) =>
      caZones.length
        ? !caZones.some((z) => n === z || n.endsWith('.' + z))
        : PUBLIC_TLD_HINT.test(n),
    )

  // на что имя ведёт прямо сейчас: адрес берётся у ноды, а не запоминается
  function nodeAddr(id: string): string {
    return nodes.find((n) => n.id === id)?.ip_addresses.join(', ') || ''
  }

  // Состояние сертификата берём с сервера по имени, а не из строки формы: пока
  // правку не сохранили, у новой строки на сервере ещё ничего нет.
  function certLine(name: string): string {
    const rec = recs?.records.find((x) => x.name === name)
    if (!rec || !rec.cert) return ''
    if (rec.cert_status === 'ok')
      return rec.cert_until ? t('сертификат до {date}', { date: rec.cert_until }) : ''
    if (rec.cert_status === 'error')
      return t('сертификат не выдан: {err}', { err: rec.cert_error })
    return t('сертификат заказан — нода заберёт его в течение минуты')
  }

  async function save() {
    if (!recs || busy || srvBusy) return
    if (!window.confirm(t(
      'Сохранить имена? При первой настройке или изменении маршрутов DNS headscale перезапустится. На это время регистрация нод приостановится.',
    ))) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const updated = await setDnsRecords(
        rows
          .map((r) => ({
            name: r.name.trim(),
            node_id: r.node_id,
            ip: r.ip.trim(),
            enabled: r.enabled,
            cert: r.cert,
          }))
          .filter((r) => r.name),
      )
      setRecs(updated)
      setRows(rowsOf(updated))
      setInfo((prev) => prev && { ...prev, restart_pending: updated.restart_pending })
      setSaved(true)
    } catch (err) {
      handle(err)
    } finally {
      setBusy(false)
    }
  }

  const parsedServers = servers
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const srvDirty =
    !!info &&
    (info.dns.split_repair_required ||
      magic !== info.dns.magic_dns ||
      override !== info.dns.override_local_dns ||
      domain.trim() !== info.dns.base_domain ||
      parsedServers.join(',') !== info.dns.nameservers.join(','))

  async function saveServers() {
    if (busy || srvBusy) return
    // Единственная настройка, которой можно отнять у нод их DNS: спрашиваем о ней
    // прямо, а не надеемся, что человек прочитал пояснение под полем.
    if (
      override &&
      parsedServers.length > 0 &&
      info &&
      (!info.dns.override_local_dns || parsedServers.join(',') !== info.dns.nameservers.join(',')) &&
      !window.confirm(
        t(
          'Свои DNS-серверы заберут у ВСЕХ нод весь DNS: сервер со своим резолвером (корпоративный, облачный, локальный) перестанет видеть внутренние имена, а там, где эти серверы из его сети недоступны, потеряет резолв целиком. Имена внутри сети работают и без этого поля. Всё равно задать?',
        ),
      )
    )
      return
    if (
      !window.confirm(
        t(
          'Применить изменения DNS? headscale перезапустится (~10–15 c), на это время регистрация нод приостановится. Смена базового домена меняет MagicDNS-имена всех нод.',
        ),
      )
    )
      return
    setSrvBusy(true)
    setSrvErr(null)
    setSrvSaved(false)
    try {
      const updated = await setHsDns({
        magic_dns: magic,
        override_local_dns: override,
        base_domain: domain.trim(),
        nameservers: parsedServers,
      })
      setInfo(updated)
      setRecs((prev) => prev && { ...prev, restart_pending: updated.restart_pending })
      seed(updated)
      setSrvLocked(true)
      setSrvSaved(true)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setSrvErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setSrvBusy(false)
    }
  }

  const status = pending ? 'wait' : recs === null ? null : recs.active ? 'active' : 'none'

  return (
    <>
      <div className="page-head">
        <h2>DNS</h2>
      </div>

      <section className="card settings-section">
        <div className="clients-head">
          <h3>{t('Имена внутри сети')}</h3>
          {status && (
            <span className={status === 'active' ? 'pill-ok' : 'pill-muted'} aria-live="polite">
              {status === 'active'
                ? t('настроено')
                : status === 'wait'
                  ? t('записано, ждёт перезапуска headscale')
                  : t('пока не настроено')}
            </span>
          )}
        </div>

        <p className="muted small">
          {t(
            'Имя ведёт на адрес в сети — но только для машин сети. Публичный DNS панель не трогает: снаружи имя ведёт туда же, куда вело, и кто не в сети — заходит как раньше. Так к сервису, закрытому вайтлистом, ходят по внутреннему адресу.',
          )}
        </p>

        {rows.length === 0 ? (
          <p className="muted small dns-empty">
            {t(
              'Имён пока нет. Обычный случай: панель или админка живёт на своём имени и закрыта снаружи — добавьте это имя и укажите ноду, на которой она стоит. Второй случай: имя для того, что стоит за нодой и куда клиента не поставить (NAS, IPMI, камера) — тогда вместо ноды укажите адрес вручную.',
            )}
          </p>
        ) : (
          <div className="dns-names">
            <div className="dns-rec-head">
              <span />
              <span>{t('имя')}</span>
              <span />
              <span>{t('на какой ноде')}</span>
              <span>{t('адрес в сети')}</span>
              <span />
              <span />
            </div>
            {rows.map((r, i) => (
              <div className={r.enabled ? 'dns-rec-row' : 'dns-rec-row off'} key={i}>
                {/* Галочка = «вести внутрь сети». Снятая оставляет имя в списке,
                    но нодам его не раздаёт: внутри сети оно снова ведёт наружу. */}
                <input
                  type="checkbox"
                  className="field-checkbox"
                  checked={r.enabled}
                  disabled={busy}
                  title={t('Вести внутрь сети')}
                  onChange={(e) => update(i, { enabled: e.target.checked })}
                />
                <input
                  type="text"
                  className="dns-rec-name mono"
                  value={r.name}
                  disabled={busy}
                  placeholder="panel.example.com"
                  onChange={(e) => update(i, { name: e.target.value })}
                />
                <span className="dns-rec-arrow" aria-hidden>
                  →
                </span>
                <select
                  value={r.node_id || 'ip'}
                  disabled={busy}
                  onChange={(e) =>
                    update(
                      i,
                      e.target.value === 'ip'
                        ? { node_id: '', ip: '', cert: false }
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
                  <span className="dns-rec-addr small">
                    {!r.enabled ? (
                      t('ведёт наружу')
                    ) : nodeAddr(r.node_id) ? (
                      <code className="chip">{nodeAddr(r.node_id)}</code>
                    ) : (
                      t('нода не найдена')
                    )}
                  </span>
                ) : (
                  <input
                    type="text"
                    className="mono"
                    value={r.ip}
                    disabled={busy}
                    placeholder="192.168.1.10"
                    onChange={(e) => update(i, { ip: e.target.value })}
                  />
                )}
                {/* Сертификат — только у имени, ведущего на ноду: ключ генерится
                    на ней, и положить его больше некуда. */}
                <label
                  className="dns-rec-cert"
                  title={
                    r.node_id
                      ? t('Выпустить сертификат для этого имени')
                      : t('Сертификат возможен только для имени, ведущего на ноду')
                  }
                >
                  <input
                    type="checkbox"
                    className="field-checkbox"
                    disabled={busy || !r.node_id}
                    checked={r.cert}
                    onChange={(e) => update(i, { cert: e.target.checked })}
                  />
                  {t('сертификат')}
                </label>
                <button
                  className="dns-rec-drop"
                  title={t('Убрать')}
                  disabled={busy}
                  onClick={() => setRows(rows.filter((_, j) => j !== i))}
                >
                  ×
                </button>
                {r.cert && certLine(r.name) && (
                  <span className="dns-rec-certline small">{certLine(r.name)}</span>
                )}
              </div>
            ))}
          </div>
        )}

        {badNames.length > 0 && (
          <div className="ca-missing">
            <p className="small">
              {caZones.length
                ? t(
                    'Этому CA разрешены только домены {have} — для {want} сертификат выдан не будет. Выпустите его заново: новый подписывает любые внутренние имена, и добавлять домены больше не придётся.',
                    { have: caZones.join(', '), want: badNames.join(', ') },
                  )
                : t(
                    'Похоже, {want} — имя в настоящем интернет-домене. CA такие не подписывает: иначе он мог бы подделать чужой сайт для ваших же машин. Возьмите внутреннее имя, например loki.lab.',
                    { want: badNames.join(', ') },
                  )}
            </p>
          </div>
        )}

        {/* Свой центр сертификации: одна строка про суть, подробности — под
            «Настроить». Раньше здесь висели три абзаца, из которых не было
            видно ни доменов, ни того, где их менять. */}
        {recs?.ca.exists && (
          <div className="dns-ca">
            <div className="clients-head">
              <h4>{t('Центр сертификации (CA)')}</h4>
              <div className="ca-head-actions">
                <button
                  className="ghost small"
                  onClick={() =>
                    downloadCa().catch((e) =>
                      setCaErr(e instanceof Error ? e.message : t('Ошибка')),
                    )
                  }
                >
                  {t('Скачать сертификат')}
                </button>
                <button className="ghost small" onClick={() => setRotOpen(!rotOpen)}>
                  {rotOpen ? t('Свернуть') : t('Настроить')}
                </button>
              </div>
            </div>

            <p className="muted small">
              {t(
                'Подписывает сертификаты для имён внутри сети. Его сертификат должен стоять на устройствах — иначе браузер ругается на каждое имя.',
              )}
            </p>
            <label className="field-check ca-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={recs.ca.auto}
                disabled={caBusy}
                onChange={async (e) => {
                  const auto = e.target.checked
                  setCaBusy(true)
                  setCaErr(null)
                  try {
                    const info = await setCa({ auto })
                    setRecs((prev) => prev && { ...prev, ca: info })
                  } catch (err) {
                    setCaErr(err instanceof Error ? err.message : t('Ошибка'))
                  } finally {
                    setCaBusy(false)
                  }
                }}
              />
              <span>{t('ставить сертификат на ноды автоматически')}</span>
            </label>

            <div className="info-row">
              <span className="muted small">{t('Подписывает')}</span>
              <span className="small">
                {recs.ca.suffixes.length
                  ? t('только {zones} (CA старого образца)', {
                      zones: recs.ca.suffixes.join(', '),
                    })
                  : recs.ca.blocked
                    ? t('любые внутренние имена, кроме {n} публичных доменов', {
                        n: recs.ca.blocked,
                      })
                    : t('что угодно (ограничений нет)')}
              </span>
            </div>
            <div className="info-row">
              <span className="muted small">{t('Действует до')}</span>
              <span>{recs.ca.not_after}</span>
            </div>
            <div className="info-row">
              <span className="muted small">{t('Отпечаток SHA-256')}</span>
              <span className="mono small ca-fp">{recs.ca.fingerprint}</span>
            </div>

            {rotOpen && (
              <div className="ca-rotate">
                <div className="ca-rotate-fields">
                  <label className="ca-years">
                    {t('Срок, лет')}
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={years}
                      onChange={(e) => setYears(Number(e.target.value))}
                      disabled={caBusy}
                    />
                  </label>
                </div>
                <p className="muted small">
                  {t(
                    'Домены придумываете вы: acme, lab, mesh — что угодно, чего нет в интернете. CA подписывает такие имена сам, поэтому новый проект работает сразу и перевыпускать ничего не нужно. Выпускать заново стоит разве что ради другого срока: прежний сертификат перестанет действовать, ноды подхватят новый за минуту, а на ноутбуках и телефонах его придётся поставить ещё раз.',
                  )}
                </p>
                <div className="dns-actions">
                  <button
                    className="danger small"
                    disabled={caBusy}
                    onClick={async () => {
                      if (
                        !window.confirm(
                          t('Выпустить CA заново? Прежний сертификат перестанет действовать.'),
                        )
                      )
                        return
                      setCaBusy(true)
                      setCaErr(null)
                      try {
                        const info = await setCa({
                          auto: recs.ca.auto,
                          reissue: true,
                          years,
                        })
                        setRecs((prev) => prev && { ...prev, ca: info })
                        setRotOpen(false)
                      } catch (err) {
                        setCaErr(err instanceof Error ? err.message : t('Ошибка'))
                      } finally {
                        setCaBusy(false)
                      }
                    }}
                  >
                    {caBusy ? t('Сохранение…') : t('Выпустить заново')}
                  </button>
                </div>
              </div>
            )}

            {(recs.ca.install_sh || recs.ca.install_ps1) && (
              <div className="ca-install">
                <p className="muted small">
                  {t(
                    'Ноды панель обслуживает сама. Машину, которую подключали руками, проще всего научить доверять одной командой — она снимет прежний сертификат панели и поставит текущий:',
                  )}
                </p>
                {recs.ca.install_ps1 && (
                  <>
                    <span className="muted small">
                      {t('Windows (PowerShell от администратора)')}
                    </span>
                    <pre className="enroll-script cmd-oneline">{recs.ca.install_ps1}</pre>
                  </>
                )}
                {recs.ca.install_sh && (
                  <>
                    <span className="muted small">{t('Linux и macOS')}</span>
                    <pre className="enroll-script cmd-oneline">{recs.ca.install_sh}</pre>
                  </>
                )}
                <p className="muted small">
                  {t(
                    'Скрипт печатает отпечаток поставленного сертификата — сверьте с тем, что выше. Android и iOS скриптом не научить: скачайте сертификат кнопкой, перенесите на устройство и включите полное доверие в настройках.',
                  )}
                </p>
              </div>
            )}
            {caErr && <p className="form-error">{caErr}</p>}
          </div>
        )}

        {rows.some((r) => r.cert) && (
          <p className="muted small settings-note">
            {t(
              'Сертификат подписывает панель своим корнем, а ключ генерится на самой ноде и никуда с неё не уезжает. Продление идёт само за месяц до конца; файлы лежат на ноде в /etc/noderoost/certs, и после смены агент запускает /lib65/noderoost-agent/cert-hook.sh, если вы его туда положили.',
            )}
          </p>
        )}

        {error && <p className="form-error">{error}</p>}
        {saved && !pending && <p className="form-ok">{t('Сохранено')}</p>}

        <div className="dns-actions">
          <button
            className="ghost small"
            disabled={busy}
            onClick={() =>
              // новая строка целится в ноду: имя на адрес ноды — обычный случай,
              // ручной адрес нужен для того, что стоит ЗА нодой
              setRows([
                ...rows,
                {
                  name: '',
                  node_id: nodes[0]?.id ?? '',
                  ip: '',
                  enabled: true,
                  cert: false,
                },
              ])
            }
          >
            {t('Добавить имя')}
          </button>
          <button onClick={save} disabled={busy || srvBusy || !dirty}>
            {busy ? t('Сохранение…') : t('Сохранить')}
          </button>
        </div>

        <p className="muted small settings-note">
          {t(
            'Галочка слева переключает имя между «внутрь сети» и «как снаружи» для всех нод. Имя внутри сети получат и ноды без доступа к указанному серверу. У них адрес перестанет открываться. Первая настройка имен и изменения маршрутов DNS требуют перезапуска headscale.',
          )}
        </p>
      </section>

      {info && (
        <section className="card settings-section">
          <div className="clients-head">
            <h3>{t('Резолверы и MagicDNS')}</h3>
            {srvLocked ? (
              <button className="ghost small" onClick={() => setSrvLocked(false)}>
                {t('Изменить')}
              </button>
            ) : (
              <button
                className="ghost small"
                disabled={srvBusy}
                onClick={() => {
                  seed(info)
                  setSrvLocked(true)
                }}
              >
                {t('Отменить')}
              </button>
            )}
          </div>
          <p className="muted small">
            {t(
              'Короткие имена самих нод (MagicDNS) и то, какими DNS-серверами ноды пользуются. Настраивается один раз при разворачивании сети.',
            )}
          </p>
          <div className="dns-srv-grid">
            <label className="field field-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={magic}
                disabled={srvLocked || srvBusy}
                onChange={(e) => setMagic(e.target.checked)}
              />
              <span>MagicDNS</span>
            </label>
            <label className="field">
              <span>{t('Базовый домен')}</span>
              <input
                value={domain}
                disabled={srvLocked || srvBusy}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="noderoost.internal"
              />
            </label>
            <label className="field">
              <span>{t('DNS-серверы (через запятую)')}</span>
              <input
                value={servers}
                disabled={srvLocked || srvBusy}
                onChange={(e) => setServers(e.target.value)}
                placeholder="1.1.1.1, 1.0.0.1"
              />
            </label>
            {override && parsedServers.length > 0 && (
              <p className="form-error">
                {t('При включенном переопределении ноды с DNS из меша используют эти серверы для всех имен.')}
              </p>
            )}
            <label className="field field-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={override}
                disabled={srvLocked || srvBusy || parsedServers.length === 0}
                onChange={(e) => setOverride(e.target.checked)}
              />
              <span>{t('Использовать только эти серверы')}</span>
            </label>
          </div>
          <p className="muted small settings-note">
            {t(
              'Эти DNS-серверы отвечают за публичные поддомены под переопределенными именами. Если поле пустое, такие запросы получают NXDOMAIN. Опция «Использовать только эти серверы» также направляет к ним остальные DNS-запросы.',
            )}
          </p>
          {info?.dns.split_repair_required && (
            <p className="muted small settings-note">{t('Примените настройки, чтобы обновить старые маршруты DNS.')}</p>
          )}
          {srvErr && <p className="form-error">{srvErr}</p>}
          {srvSaved && (
            <p className={pending ? 'muted small' : 'form-ok'} aria-live="polite">
              {pending ? t('записано, ждёт перезапуска headscale') : t('Сохранено')}
            </p>
          )}
          <div className="dns-actions">
            <button onClick={saveServers} disabled={busy || srvBusy || srvLocked || !srvDirty}>
              {srvBusy ? t('Применение…') : t('Применить')}
            </button>
          </div>
          <p className="muted small settings-note">
            {t(
              'Применяется с перезапуском headscale (~10–15 c). Смена базового домена меняет MagicDNS-имена всех нод.',
            )}
          </p>
        </section>
      )}
    </>
  )
}
