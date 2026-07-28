import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  getAudit,
  getHeadscaleLogs,
  getSummary,
  type AuditEntry,
  type HeadscaleLogs,
  type Summary,
} from './api'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Tab = 'summary' | 'audit' | 'hs'

const ACTIONS: Record<string, string> = {
  login_ok: 'Вход',
  login_blocked: 'Вход заблокирован',
  password_change: 'Смена пароля',
  node_enroll: 'Добавлена нода',
  node_rename: 'Переименована нода',
  node_tags: 'Роли/теги ноды',
  node_meta: 'Изменена нода',
  node_description: 'Описание ноды',
  node_chown: 'Смена владельца',
  node_routes: 'Маршруты ноды',
  node_expire: 'Истёк ключ ноды',
  node_delete: 'Удалена нода',
  acl_rules_set: 'Изменён доступ',
  policy_set: 'Изменена политика',
  dns_update: 'Изменён DNS',
  apikey_create: 'Создан API-ключ',
  apikey_expire: 'Истёк API-ключ',
  user_create: 'Создан пользователь',
  user_rename: 'Переименован пользователь',
  user_delete: 'Удалён пользователь',
  preauthkey_expire: 'Истёк ключ подключения',
  backup_run: 'Бэкап',
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`
}

export function LogsModal({
  onClose,
  onUnauthorized,
}: {
  onClose: () => void
  onUnauthorized: () => void
}) {
  const { t } = useI18n()
  useModalDismiss(onClose)
  const [tab, setTab] = useState<Tab>('summary')
  const [audit, setAudit] = useState<AuditEntry[] | null>(null)
  const [hs, setHs] = useState<HeadscaleLogs | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (which: Tab) => {
      setBusy(true)
      setError(null)
      try {
        if (which === 'audit') setAudit(await getAudit())
        else if (which === 'hs') setHs(await getHeadscaleLogs())
        else setSummary(await getSummary())
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) onUnauthorized()
        else setError(err instanceof Error ? err.message : t('Ошибка'))
      } finally {
        setBusy(false)
      }
    },
    [onUnauthorized, t],
  )

  useEffect(() => {
    load(tab)
  }, [tab, load])

  const sumRows: [string, string][] = summary
    ? [
        [t('Версия панели'), summary.panel_version || '—'],
        [t('control-сервер'), summary.headscale_url || '—'],
        [t('headscale'), summary.headscale_ok ? t('доступен') : t('недоступен')],
        ['MagicDNS', summary.magic_dns ? t('вкл') : t('выкл')],
        [t('Базовый домен'), summary.base_domain || '—'],
        [t('DNS-серверы'), summary.nameservers.join(', ') || '—'],
        ['DERP', summary.derp_embedded ? t('встроенный') : '—'],
        [
          t('Ноды'),
          t('{total} · серверов {s} · устройств {d} · онлайн {o}', {
            total: summary.nodes_total,
            s: summary.servers,
            d: summary.devices,
            o: summary.online,
          }),
        ],
        [
          t('Последний бэкап'),
          summary.last_backup ? `${summary.last_backup} (${fmt(summary.last_backup_at)})` : '—',
        ],
      ]
    : []

  return (
    <div className="modal-backdrop">
      <div className="card modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Журнал и диагностика')}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        <div className="grid-toggle logs-tabs">
          <button className={tab === 'summary' ? 'seg-active' : ''} onClick={() => setTab('summary')}>
            {t('Сводка')}
          </button>
          <button className={tab === 'audit' ? 'seg-active' : ''} onClick={() => setTab('audit')}>
            {t('Журнал действий')}
          </button>
          <button className={tab === 'hs' ? 'seg-active' : ''} onClick={() => setTab('hs')}>
            {t('Логи headscale')}
          </button>
        </div>

        {error && <p className="form-error">{error}</p>}

        {tab === 'summary' && (
          <div className="detail-info logs-summary">
            {busy && !summary ? (
              <p className="muted small">{t('загрузка…')}</p>
            ) : (
              sumRows.map(([k, v]) => (
                <div key={k} className="info-row">
                  <span className="muted small">{k}</span>
                  <span>{v}</span>
                </div>
              ))
            )}
          </div>
        )}

        {tab === 'audit' && (
          <div className="logs-scroll">
            {busy && !audit ? (
              <p className="muted small">{t('загрузка…')}</p>
            ) : !audit || audit.length === 0 ? (
              <p className="muted small">{t('Записей пока нет.')}</p>
            ) : (
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>{t('Время')}</th>
                    <th>{t('Кто')}</th>
                    <th>{t('Действие')}</th>
                    <th>{t('Объект')}</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((a, i) => (
                    <tr key={i}>
                      <td className="mono small">{fmt(a.ts)}</td>
                      <td>{a.username || '—'}</td>
                      <td>{ACTIONS[a.action] ? t(ACTIONS[a.action]) : a.action}</td>
                      <td className="muted small">
                        {a.target}
                        {a.detail ? ` · ${a.detail}` : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {tab === 'hs' && (
          <div>
            <div className="logs-hs-head">
              <button className="ghost small" onClick={() => load('hs')} disabled={busy}>
                {busy ? t('Собираю…') : t('Обновить')}
              </button>
              {hs?.note && <span className="muted small">{hs.note}</span>}
            </div>
            {busy && !hs ? (
              <p className="muted small">{t('Собираю логи headscale (до ~5 c)…')}</p>
            ) : hs && !hs.available ? (
              <p className="muted small">{hs.note}</p>
            ) : (
              <pre className="enroll-script logs-hs">{hs?.text || ''}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
