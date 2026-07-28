import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  deleteNode,
  expireNode,
  getPolicyRules,
  putPolicyRules,
  type AclRule,
  type AclSelector,
  type Node,
} from './api'
import { groupGrants, portLabel, selLabel, toggleRule } from './aclui'
import { GrantModal } from './GrantModal'
import { Menu } from './Menu'
import { NodeEditModal } from './NodeEditModal'
import { ReconnectModal } from './ReconnectModal'
import { RoutesModal } from './RoutesModal'
import { OsIcon } from './OsIcon'
import { useI18n } from './i18n'

const EXIT_CIDRS = ['0.0.0.0/0', '::/0']

// дата+время в локали браузера; пусто → прочерк
function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`
}

type Props = {
  node: Node
  nodes: Node[]
  onBack: () => void
  onChanged: () => void
  onUnauthorized: () => void
}

export function NodeDetail({
  node,
  nodes,
  onBack,
  onChanged,
  onUnauthorized,
}: Props) {
  const { t } = useI18n()
  const [rules, setRules] = useState<AclRule[] | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [routesOpen, setRoutesOpen] = useState(false)
  const [reconnectOpen, setReconnectOpen] = useState(false)
  const [addRole, setAddRole] = useState<'target' | 'source' | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  // «имя (IP)» — удобно вставлять в тикеты/конфиги; берём IPv4 ноды
  const v4 = node.ip_addresses.find((ip) => !ip.includes(':'))
  const nameWithIp = v4 ? `${node.name} (${v4})` : ''

  async function copyValue(text: string, tag: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(tag)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      /* clipboard недоступен (не-HTTPS/нет прав) — пользователь выделит вручную */
    }
  }

  const loadRules = useCallback(async () => {
    try {
      setRules((await getPolicyRules()).rules)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setRules([])
    }
  }, [onUnauthorized])

  useEffect(() => {
    loadRules()
  }, [loadRules])

  // «Кто может подключаться сюда» (входящие, dst=нода) и «Куда ходит эта нода» (исходящие, src=нода).
  const inRows = useMemo(
    () => groupGrants(rules ?? [], 'server').find((g) => g.entity.kind === 'node' && g.entity.value === node.id)?.rows ?? [],
    [rules, node.id],
  )
  const outRows = useMemo(
    () => groupGrants(rules ?? [], 'who').find((g) => g.entity.kind === 'node' && g.entity.value === node.id)?.rows ?? [],
    [rules, node.id],
  )

  function handle(err: unknown) {
    if (err instanceof ApiError && err.status === 401) onUnauthorized()
    else setError(err instanceof Error ? err.message : t('Ошибка'))
  }

  async function saveRules(next: AclRule[]) {
    const prev = rules
    setRules(next)
    setSaving(true)
    setError(null)
    try {
      setRules((await putPolicyRules(next)).rules)
    } catch (err) {
      setRules(prev ?? [])
      handle(err)
    } finally {
      setSaving(false)
    }
  }

  // role 'target' — вход (src=other, dst=нода); 'source' — исход (src=нода, dst=other).
  function removePair(other: AclSelector, port: string, role: 'target' | 'source') {
    const self: AclSelector = { kind: 'node', value: node.id }
    const [src, dst] = role === 'target' ? [other, self] : [self, other]
    saveRules(toggleRule(rules ?? [], src, dst, port, false))
  }

  // редактируемые строки доступа (порт → чипы «×»); role задаёт стрелку и удаление
  const renderRows = (rows: typeof inRows, role: 'target' | 'source') =>
    rows.map((row) => (
      <div key={row.port} className="grant-row">
        <span className="port-badge">{portLabel(row.port, t)}</span>
        <span className="grant-arrow">{role === 'target' ? '←' : '→'}</span>
        <span className="ent-chips">
          {row.others.map((o) => (
            <span key={`${o.kind}:${o.value}`} className="ent-chip">
              {selLabel(o, nodes, t)}
              <button
                className="chip-x"
                disabled={saving}
                aria-label={t('Убрать')}
                onClick={() => removePair(o, row.port, role)}
              >
                ×
              </button>
            </span>
          ))}
        </span>
      </div>
    ))

  async function doExpire() {
    if (!window.confirm(t('Отозвать ключ ноды «{name}»? Она отключится и не подключится обратно, пока её не переподключить заново.', { name: node.name }))) return
    try {
      await expireNode(node.id)
      onChanged()
    } catch (err) {
      handle(err)
    }
  }

  async function doDelete() {
    if (!window.confirm(t('Удалить ноду «{name}»? Она будет удалена из тайлнета.', { name: node.name }))) return
    try {
      await deleteNode(node.id)
      onBack()
      onChanged()
    } catch (err) {
      handle(err)
    }
  }

  return (
    <>
      <button className="linklike detail-back" onClick={onBack}>
        ← {t('Ноды')}
      </button>

      {/* шапка ноды */}
      <div className="page-head">
        <h2 className="detail-title">
          <span className={`dot ${node.online ? 'dot-ok' : 'dot-unknown'}`} />
          {node.name}
          {node.admin && <span className="pill-admin">{t('админ')}</span>}
          {node.is_exit_node && <span className="pill-ok">exit</span>}
          {node.key_expired && <span className="pill-warn">{t('Ключ истёк')}</span>}
          {/* нода что-то анонсирует, но это не одобрено — даём одобрить в один клик */}
          {(node.advertises_exit_node && !node.is_exit_node) ||
          node.available_routes.some(
            (r) => !EXIT_CIDRS.includes(r) && !node.approved_routes.includes(r),
          ) ? (
            <button
              className="pill-warn pill-action"
              title={t('Одобрить маршруты')}
              onClick={() => setRoutesOpen(true)}
            >
              {t('маршруты ожидают')}
            </button>
          ) : null}
        </h2>
        <div className="page-head-actions">
          <button className="ghost" onClick={() => setEditOpen(true)}>
            {t('Изменить')}
          </button>
          <Menu
            className="ghost icon-btn"
            caret={false}
            title={t('Ещё')}
            label={<span className="menu-gear">⋯</span>}
            items={[
              { label: t('Маршруты'), onClick: () => setRoutesOpen(true) },
              { label: t('Переподключить'), onClick: () => setReconnectOpen(true) },
              { label: t('Отозвать ключ'), onClick: doExpire },
              { divider: true },
              { label: t('Удалить'), danger: true, onClick: doDelete },
            ]}
          />
        </div>
      </div>

      {/* инфо: компактная сетка «ярлык сверху — значение снизу».
          Вертикальный список label-слева/значение-справа читался «зигзагом»
          и растягивал карточку на пол-экрана. */}
      <div className="card detail-info">
        <div className="spec-grid">
          {node.description && (
            <div className="spec-cell spec-wide">
              <span className="spec-label">{t('Описание')}</span>
              <span className="spec-value">{node.description}</span>
            </div>
          )}

          {(node.force_exit || node.exit_gateway || node.exit_via.length > 0) && (
            <div className="spec-cell spec-wide">
              <span className="spec-label">{t('Выход в интернет')}</span>
              <span className="spec-value">
                {node.force_exit ? (
                  <span className="pill-warn" title={t('Весь трафик этой ноды принудительно через шлюз (exit-node)')}>
                    {t('весь трафик через {gw} (принудительно)').replace(
                      '{gw}',
                      nodes.find((n) => n.id === node.force_exit)?.name || node.force_exit,
                    )}
                  </span>
                ) : node.exit_gateway ? (
                  <span className="pill-ok">{t('Шлюз выхода в интернет')}</span>
                ) : (
                  <span className="muted small">
                    {t('через шлюзы: {list}').replace(
                      '{list}',
                      node.exit_via
                        .map((id) => nodes.find((n) => n.id === id)?.name || id)
                        .join(', '),
                    )}
                  </span>
                )}
              </span>
            </div>
          )}

          <div className="spec-cell">
            <span className="spec-label">IP</span>
            <span className="spec-value">
              {node.ip_addresses.map((ip) => (
                <button
                  key={ip}
                  type="button"
                  className="chip chip-copy"
                  title={t('Скопировать IP')}
                  onClick={() => copyValue(ip, `ip:${ip}`)}
                >
                  {copied === `ip:${ip}` ? t('Скопировано ✓') : ip}
                </button>
              ))}
              {nameWithIp && (
                <button
                  type="button"
                  className="chip chip-copy"
                  title={t('Скопировать «имя (IP)»')}
                  onClick={() => copyValue(nameWithIp, 'nameip')}
                >
                  {copied === 'nameip' ? t('Скопировано ✓') : t('имя + IP')}
                </button>
              )}
            </span>
          </div>

          {node.os && (
            <div className="spec-cell">
              <span className="spec-label">{t('Система')}</span>
              <span className="spec-value spec-os">
                <OsIcon os={node.os} />
                <span>{node.os}</span>
                {node.arch && <span className="spec-note spec-mono">{node.arch}</span>}
                {node.container && <span className="spec-note">{t('контейнер')}</span>}
              </span>
            </div>
          )}

          {node.client_version && (
            <div className="spec-cell">
              <span className="spec-label">{t('Клиент Tailscale')}</span>
              <span className="spec-value spec-mono">{node.client_version}</span>
            </div>
          )}

          {node.endpoint && (
            <div className="spec-cell">
              <span className="spec-label">{t('Виден с адреса')}</span>
              <span className="spec-value">
                <span className="spec-mono">{node.endpoint}</span>
                <span className="spec-note">
                  {node.direct_ok ? t('прямое соединение') : t('только через DERP')}
                </span>
              </span>
            </div>
          )}

          <div className="spec-cell">
            <span className="spec-label">{t('Добавлена')}</span>
            <span className="spec-value">{fmt(node.created_at)}</span>
          </div>

          {!node.online && (
            <div className="spec-cell">
              <span className="spec-label">{t('Была в сети')}</span>
              <span className="spec-value">{fmt(node.last_seen)}</span>
            </div>
          )}

          <div className="spec-cell">
            <span className="spec-label">{t('Срок ключа')}</span>
            <span className="spec-value">
              {node.expiry ? fmt(node.expiry) : <span className="spec-note">{t('не истекает')}</span>}
            </span>
          </div>

          {node.tags.length > 0 && (
            <div className="spec-cell spec-wide">
              <span className="spec-label">{t('Теги')}</span>
              <span className="spec-value">
                {node.tags.map((tg) => (
                  <span key={tg} className="tag-chip">
                    {tg}
                  </span>
                ))}
              </span>
            </div>
          )}

          {node.subnet_routes.length > 0 && (
            <div className="spec-cell spec-wide">
              <span className="spec-label">{t('Маршруты')}</span>
              <span className="spec-value">
                {node.subnet_routes.map((r) => (
                  <span key={r} className="tag-chip">
                    {r}
                  </span>
                ))}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ДОСТУП — редактируется прямо здесь */}
      <div className="card">
        <h3>{t('Доступ')}</h3>
        {error && <p className="form-error">{error}</p>}
        {node.admin && (
          <>
            <p className="muted small access-admin-note">
              {t('Админ-устройство: полный доступ ко всем серверам. Подключиться к нему нельзя.')}
            </p>
            {node.ip_addresses.find((ip) => !ip.includes(':')) && (
              <p className="muted small">
                {t('IP для вайтлиста на серверах:')}{' '}
                <code className="code-inline">
                  {node.ip_addresses.find((ip) => !ip.includes(':'))}
                </code>{' '}
                {t('— он стабилен, пока нода существует.')}
              </p>
            )}
          </>
        )}
        {rules === null ? (
          <p className="muted small">{t('загрузка…')}</p>
        ) : (
          <>
            <div className="access-block">
              <div className="access-block-head">
                <h4>{t('Кто может подключаться сюда')}</h4>
                {/* Устройство не может быть целью: в Tailscale грант делает стороны
                    взаимно видимыми, поэтому такое правило означало бы, что устройства
                    видят друг друга. Движок его всё равно отбросит — не показываем кнопку. */}
                {!node.admin && node.kind === 'server' && (
                  <button className="ghost small" onClick={() => setAddRole('target')} disabled={saving}>
                    {t('+ Разрешить')}
                  </button>
                )}
              </div>
              {node.admin ? (
                <p className="muted small">{t('никто — админ недостижим')}</p>
              ) : node.kind !== 'server' ? (
                <p className="muted small">
                  {t('никто — к устройствам подключаться нельзя, они не видят друг друга')}
                </p>
              ) : null}
              {inRows.length === 0
                ? !node.admin && node.kind === 'server' && (
                    <p className="muted small">{t('никто (всё запрещено)')}</p>
                  )
                : renderRows(inRows, 'target')}
            </div>

            <div className="access-block">
              <div className="access-block-head">
                <h4>{t('Куда может ходить эта нода')}</h4>
                <button className="ghost small" onClick={() => setAddRole('source')} disabled={saving}>
                  {t('+ Разрешить')}
                </button>
              </div>
              {node.admin && (
                <div className="grant-row">
                  <span className="port-badge">{t('всё')}</span>
                  <span className="grant-arrow">→</span>
                  <span className="ent-chip ent-chip-ro">{t('все серверы (админ)')}</span>
                </div>
              )}
              {outRows.length === 0
                ? !node.admin && <p className="muted small">{t('никуда (всё запрещено)')}</p>
                : renderRows(outRows, 'source')}
            </div>
          </>
        )}
      </div>

      {node.is_exit_node && (
        <div className="card">
          <h3>{t('Клиентам: как ходить в интернет через эту exit-ноду')}</h3>
          <p className="muted small">
            {t('Выбор exit-ноды — на стороне клиента. Отдайте это пользователю.')}
          </p>
          <p className="muted small exit-warn">
            {t('Чтобы эта exit-нода появилась у клиента, отметьте её «Шлюзом выхода» на этом сервере и разрешите нужным устройствам (в «Изменить ноду» — здесь же или в карточке устройства). Без этого клиент увидит «No exit node available».')}
          </p>
          <div className="access-block">
            <h4>{t('Linux / macOS (в терминале)')}</h4>
            <pre className="enroll-script">{`# включить выход через exit-ноду:
tailscale set --exit-node=${node.ip_addresses.find((ip) => !ip.includes(':')) ?? node.name} --exit-node-allow-lan-access

# выключить:
tailscale set --exit-node=`}</pre>
          </div>
          <div className="access-block">
            <h4>{t('Windows (PowerShell от администратора)')}</h4>
            <pre className="enroll-script">{`$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"

# включить выход через exit-ноду:
& $ts set --exit-node=${node.ip_addresses.find((ip) => !ip.includes(':')) ?? node.name} --exit-node-allow-lan-access

# выключить:
& $ts set --exit-node=`}</pre>
            <p className="muted small">
              {t('То же самое мышкой: иконка Tailscale в трее → Exit nodes → «{name}»; выключить — «None».', {
                name: node.name,
              })}
            </p>
          </div>
          <p className="muted small">
            {t('Android / iOS: в приложении Tailscale → меню → Exit Node → «{name}»; выключить — пункт «None».', {
              name: node.name,
            })}
          </p>
        </div>
      )}


      {editOpen && (
        <NodeEditModal
          node={node}
          nodes={nodes}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false)
            onChanged()
          }}
          onUnauthorized={onUnauthorized}
        />
      )}
      {routesOpen && (
        <RoutesModal
          node={node}
          onClose={() => setRoutesOpen(false)}
          onSaved={() => {
            setRoutesOpen(false)
            onChanged()
          }}
          onUnauthorized={onUnauthorized}
        />
      )}
      {reconnectOpen && (
        <ReconnectModal
          node={node}
          // нода могла переоформиться (новый id/IP), пока модалка была открыта
          onClose={() => {
            setReconnectOpen(false)
            onChanged()
          }}
          onDone={onChanged}
          onUnauthorized={onUnauthorized}
        />
      )}
      {addRole && rules && (
        <GrantModal
          node={node}
          role={addRole}
          nodes={nodes}
          rules={rules}
          onClose={() => setAddRole(null)}
          onApply={(next) => {
            setAddRole(null)
            saveRules(next)
          }}
        />
      )}
    </>
  )
}
