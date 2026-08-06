import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  downloadCa,
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

type Props = { onUnauthorized: () => void }

// строка формы: цель — либо нода (адрес подставляется её текущий), либо адрес
// руками (для того, что стоит ЗА нодой и куда клиента не поставить)
type RecRow = {
  name: string
  node_id: string
  ip: string
  enabled: boolean
  cert: boolean
  issuer: 'le' | 'ca'
}

function rowsOf(r: DnsRecords): RecRow[] {
  return r.records.map((x) => ({
    name: x.name,
    node_id: x.node_id,
    ip: x.ip,
    enabled: x.enabled,
    cert: x.cert,
    issuer: x.issuer,
  }))
}

// Сообщение, когда правку записали, а перезапустить headscale некому.
const HELPER_MISSING =
  'Изменения записаны, но headscale не перезапущен: на хосте нет помощника. Выполните на сервере `sudo ops/update.sh` (он его поставит) или перезапустите вручную: `docker compose restart headscale`.'

export function DnsPage({ onUnauthorized }: Props) {
  const { t } = useI18n()
  const [recs, setRecs] = useState<DnsRecords | null>(null)
  const [rows, setRows] = useState<RecRow[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [caErr, setCaErr] = useState<string | null>(null)

  // резолверы и MagicDNS — вторая половина раздела: настраивается один раз и
  // почти никогда не меняется, поэтому карточка закрыта на «Изменить»
  const [info, setInfo] = useState<HsInfo | null>(null)
  const [magic, setMagic] = useState(false)
  const [override, setOverride] = useState(false)
  const [domain, setDomain] = useState('')
  const [servers, setServers] = useState('')
  const [srvLocked, setSrvLocked] = useState(true)
  const [srvBusy, setSrvBusy] = useState(false)
  const [srvMsg, setSrvMsg] = useState<string | null>(null)
  const [srvErr, setSrvErr] = useState<string | null>(null)

  const handle = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setError(err instanceof Error ? err.message : t('Ошибка'))
    },
    [onUnauthorized, t],
  )

  function seed(i: HsInfo) {
    setMagic(i.dns.magic_dns)
    setOverride(i.dns.override_local_dns)
    setDomain(i.dns.base_domain)
    setServers(i.dns.nameservers.join(', '))
  }

  useEffect(() => {
    listNodes()
      .then(setNodes)
      .catch(() => {
        /* без списка нод останется только ручной адрес */
      })
    getDnsRecords()
      .then((r) => {
        setRecs(r)
        setRows(rowsOf(r))
      })
      .catch(handle)
    getHsInfo()
      .then((i) => {
        setInfo(i)
        seed(i)
      })
      .catch(() => {})
  }, [handle])

  const dirty = !!recs && JSON.stringify(rows) !== JSON.stringify(rowsOf(recs))

  function update(i: number, patch: Partial<RecRow>) {
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

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
    // Первое имя прописывает путь к файлу в config.yaml — только это и требует
    // перезапуска. Дальше headscale перечитывает файл сам, предупреждать не о чем.
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
    setBusy(true)
    setError(null)
    setMsg(null)
    try {
      const updated = await setDnsRecords(
        rows
          .map((r) => ({
            name: r.name.trim(),
            node_id: r.node_id,
            ip: r.ip.trim(),
            enabled: r.enabled,
            cert: r.cert,
            issuer: r.issuer,
          }))
          .filter((r) => r.name),
      )
      setRecs(updated)
      setRows(rowsOf(updated))
      setMsg(
        updated.restart_pending
          ? t('Сохранено. headscale перезапускается…')
          : t('Сохранено — имена уже раздаются нодам'),
      )
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
    (magic !== info.dns.magic_dns ||
      override !== info.dns.override_local_dns ||
      domain.trim() !== info.dns.base_domain ||
      parsedServers.join(',') !== info.dns.nameservers.join(','))

  async function saveServers() {
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
    setSrvMsg(null)
    try {
      const updated = await setHsDns({
        magic_dns: magic,
        override_local_dns: override,
        base_domain: domain.trim(),
        nameservers: parsedServers,
      })
      setInfo(updated)
      seed(updated)
      setSrvLocked(true)
      setSrvMsg(t('Сохранено. headscale перезапускается…'))
      window.setTimeout(() => {
        getHsInfo()
          .then((i) => {
            setInfo(i)
            seed(i)
            // Перезапускает headscale хостовый помощник. Если флаг ещё на месте,
            // значит помощника нет (или он сломан): правка записана, но не
            // действует — и сказать об этом должна панель, а не тишина.
            if (i.restart_pending) setSrvErr(t(HELPER_MISSING))
          })
          .catch(() => {})
      }, 9000)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setSrvErr(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setSrvBusy(false)
    }
  }

  const status = !recs || !recs.active ? 'none' : recs.restart_pending ? 'wait' : 'live'

  return (
    <>
      <div className="page-head">
        <h2>DNS</h2>
      </div>

      <section className="card settings-section">
        <div className="clients-head">
          <h3>{t('Имена внутри сети')}</h3>
          <span className={status === 'live' ? 'pill-ok' : 'pill-muted'}>
            {status === 'live'
              ? t('раздаётся нодам')
              : status === 'wait'
                ? t('записано, ждёт перезапуска headscale')
                : t('пока не настроено')}
          </span>
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
                  title={t('Вести внутрь сети')}
                  onChange={(e) => update(i, { enabled: e.target.checked })}
                />
                <input
                  type="text"
                  className="dns-rec-name mono"
                  value={r.name}
                  placeholder="panel.example.com"
                  onChange={(e) => update(i, { name: e.target.value })}
                />
                <span className="dns-rec-arrow" aria-hidden>
                  →
                </span>
                <select
                  value={r.node_id || 'ip'}
                  onChange={(e) =>
                    update(
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
                    placeholder="192.168.1.10"
                    onChange={(e) => update(i, { ip: e.target.value })}
                  />
                )}
                {/* Сертификат — только у имени, ведущего на ноду: ключ генерится
                    на ней, и положить его больше некуда. */}
                <select
                  className="dns-rec-cert"
                  disabled={!r.node_id}
                  title={
                    r.node_id
                      ? t('Кто выпускает сертификат для этого имени')
                      : t('Сертификат возможен только для имени, ведущего на ноду')
                  }
                  value={r.cert ? r.issuer : 'off'}
                  onChange={(e) =>
                    update(
                      i,
                      e.target.value === 'off'
                        ? { cert: false }
                        : { cert: true, issuer: e.target.value as 'le' | 'ca' },
                    )
                  }
                >
                  <option value="off">{t('без сертификата')}</option>
                  <option value="le">{t("Let's Encrypt")}</option>
                  <option value="ca">{t('своя CA')}</option>
                </select>
                <button
                  className="dns-rec-drop"
                  title={t('Убрать')}
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

        {/* Корень своей CA. Показываем, только когда он кому-то нужен: пока
            все имена на Let's Encrypt, ставить на устройства нечего. */}
        {recs?.ca.exists && rows.some((r) => r.cert && r.issuer === 'ca') && (
          <div className="dns-ca">
            <div className="clients-head">
              <h4>{t('Корневой сертификат')}</h4>
              <button
                className="ghost small"
                onClick={() =>
                  downloadCa().catch((e) =>
                    setCaErr(e instanceof Error ? e.message : t('Ошибка')),
                  )
                }
              >
                {t('Скачать')}
              </button>
            </div>
            <p className="muted small">
              {t(
                'Имена со своей CA открываются без предупреждений только там, где этот сертификат установлен. Поставьте его один раз на каждое устройство, с которого ходите: Windows — «Доверенные корневые центры сертификации» (Локальный компьютер), macOS — Связка ключей → «Система» с уровнем «Всегда доверять», Linux — /usr/local/share/ca-certificates + update-ca-certificates, Android и iOS — установить профиль и включить полное доверие в настройках.',
              )}
            </p>
            <div className="info-row">
              <span className="muted small">{t('Действует до')}</span>
              <span>{recs.ca.not_after}</span>
            </div>
            <div className="info-row">
              <span className="muted small">{t('Отпечаток SHA-256')}</span>
              <span className="mono small">{recs.ca.fingerprint}</span>
            </div>
            <p className="muted small">
              {t('Сверьте отпечаток после установки: так видно, что на устройстве именно этот корень, а не похожий.')}
            </p>
            {caErr && <p className="form-error">{caErr}</p>}
          </div>
        )}

        {rows.some((r) => r.cert) && (
          <p className="muted small settings-note">
            {t(
              'Сертификат выпускает панель, а ключ генерится на самой ноде и никуда с неё не уезжает. Let’s Encrypt даёт публично доверенный сертификат, но требует, чтобы имя существовало в публичном DNS (одна wildcard-запись на адрес панели плюс NODEROOST_CERT_DOMAIN в её .env), и публикует имя в CT-логах. Своя CA не требует ни домена, ни интернета — имя может быть любым, зато её корень нужно один раз поставить на устройства. Продление идёт само за месяц до конца; файлы лежат на ноде в /etc/noderoost/certs, и после смены агент запускает /lib65/noderoost-agent/cert-hook.sh, если вы его туда положили.',
            )}
          </p>
        )}

        {error && <p className="form-error">{error}</p>}
        {msg && <p className="form-ok">{msg}</p>}

        <div className="dns-actions">
          <button
            className="ghost small"
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
                  issuer: 'le' as const,
                },
              ])
            }
          >
            {t('Добавить имя')}
          </button>
          <button onClick={save} disabled={busy || !dirty}>
            {busy ? t('Сохранение…') : t('Сохранить')}
          </button>
        </div>

        <p className="muted small settings-note">
          {t(
            'Галочка слева переключает имя между «внутрь сети» и «как снаружи» — снятая оставляет запись в списке, но нодам её не раздаёт. Переключается для всей сети сразу: адресно, по машинам, headscale раздавать имена не умеет. Имя получают и те машины, которым доступ к этому серверу не открыт: у них оно перестанет открываться совсем, наружу за ним они больше не пойдут. Правки применяются без перезапуска headscale; исключение — самое первое имя.',
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
                disabled={srvLocked}
                onChange={(e) => setMagic(e.target.checked)}
              />
              <span>MagicDNS</span>
            </label>
            <label className="field">
              <span>{t('Базовый домен')}</span>
              <input
                value={domain}
                disabled={srvLocked}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="noderoost.internal"
              />
            </label>
            <label className="field">
              <span>{t('DNS-серверы (через запятую)')}</span>
              <input
                value={servers}
                disabled={srvLocked}
                onChange={(e) => setServers(e.target.value)}
                placeholder="1.1.1.1, 1.0.0.1"
              />
            </label>
            <label className="field field-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={override}
                disabled={srvLocked || parsedServers.length === 0}
                onChange={(e) => setOverride(e.target.checked)}
              />
              <span>{t('Использовать только эти серверы')}</span>
            </label>
          </div>
          <p className="muted small settings-note">
            {t(
              'Без галочки ноды продолжают пользоваться своим DNS, а эти серверы добавляются к нему. С галочкой весь DNS ноды идёт только сюда — сервер перестанет видеть внутренние имена, которые знал его прежний резолвер.',
            )}
          </p>
          {srvErr && <p className="form-error">{srvErr}</p>}
          {srvMsg && <p className="form-ok">{srvMsg}</p>}
          <div className="dns-actions">
            <button onClick={saveServers} disabled={srvBusy || srvLocked || !srvDirty}>
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
