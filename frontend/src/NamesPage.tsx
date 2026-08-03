import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  getDnsRecords,
  listNodes,
  setDnsRecords,
  type DnsRecords,
  type Node,
} from './api'
import { useI18n } from './i18n'

type Props = { onUnauthorized: () => void }

// строка формы: цель — либо нода (адрес подставляется её текущий), либо адрес
// руками (для того, что стоит ЗА нодой и куда клиента не поставить)
type RecRow = { name: string; node_id: string; ip: string; enabled: boolean }

function rowsOf(r: DnsRecords): RecRow[] {
  return r.records.map((x) => ({
    name: x.name,
    node_id: x.node_id,
    ip: x.ip,
    enabled: x.enabled,
  }))
}

export function NamesPage({ onUnauthorized }: Props) {
  const { t } = useI18n()
  const [recs, setRecs] = useState<DnsRecords | null>(null)
  const [rows, setRows] = useState<RecRow[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handle = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setError(err instanceof Error ? err.message : t('Ошибка'))
    },
    [onUnauthorized, t],
  )

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
  }, [handle])

  const dirty = !!recs && JSON.stringify(rows) !== JSON.stringify(rowsOf(recs))

  function update(i: number, patch: Partial<RecRow>) {
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  // на что имя ведёт прямо сейчас: адрес берётся у ноды, а не запоминается
  function nodeAddr(id: string): string {
    return nodes.find((n) => n.id === id)?.ip_addresses.join(', ') || ''
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

  return (
    <section className="card">
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

      <p className="muted small">
        {t(
          'Имя ведёт на адрес в сети — но только для машин сети. Публичный DNS панель не трогает: снаружи имя ведёт туда же, куда вело, и кто не в сети — заходит как раньше. Так к сервису, закрытому вайтлистом, ходят по внутреннему адресу.',
        )}
      </p>

      {rows.length === 0 && (
        <p className="muted small names-empty">
          {t(
            'Имён пока нет. Обычный случай: панель или админка живёт на своём имени и закрыта снаружи — добавьте это имя и укажите ноду, на которой она стоит. Второй случай: имя для того, что стоит за нодой и куда клиента не поставить (NAS, IPMI, камера) — тогда вместо ноды укажите адрес вручную.',
          )}
        </p>
      )}

      {rows.map((r, i) => (
        <div className={r.enabled ? 'dns-rec-row' : 'dns-rec-row off'} key={i}>
          {/* Галочка = «вести внутрь сети». Снятая оставляет имя в списке, но
              нодам его не раздаёт: внутри сети оно снова ведёт наружу. */}
          <input
            type="checkbox"
            className="field-checkbox"
            checked={r.enabled}
            title={t('Вести внутрь сети')}
            onChange={(e) => update(i, { enabled: e.target.checked })}
          />
          <input
            type="text"
            value={r.name}
            placeholder="panel.example.com"
            onChange={(e) => update(i, { name: e.target.value })}
          />
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
            <span className={r.enabled ? 'dns-rec-addr mono small' : 'dns-rec-addr small'}>
              {!r.enabled
                ? t('ведёт наружу, как обычно')
                : nodeAddr(r.node_id) || t('нода не найдена')}
            </span>
          ) : (
            <input
              type="text"
              value={r.ip}
              placeholder="192.168.1.10"
              onChange={(e) => update(i, { ip: e.target.value })}
            />
          )}
          <button
            className="ghost small dns-rec-drop"
            onClick={() => setRows(rows.filter((_, j) => j !== i))}
          >
            {t('Убрать')}
          </button>
        </div>
      ))}

      {error && <p className="form-error">{error}</p>}
      {msg && <p className="form-ok">{msg}</p>}

      <div className="dns-actions">
        <button
          className="ghost small"
          onClick={() =>
            // новая строка целится в ноду: имя на адрес ноды — обычный случай,
            // ручной адрес нужен для того, что стоит ЗА нодой
            setRows([...rows, { name: '', node_id: nodes[0]?.id ?? '', ip: '', enabled: true }])
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
  )
}
