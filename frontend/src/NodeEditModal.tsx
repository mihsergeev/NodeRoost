import { useMemo, useState } from 'react'
import {
  ApiError,
  getAgent,
  renameNode,
  setExitClients,
  setNodeMeta,
  setNodeTags,
  type Node,
} from './api'
import { roleList } from './aclui'
import { useI18n } from './i18n'
import { useModalDismiss } from './useModalDismiss'

type Props = {
  node: Node
  nodes: Node[]
  onClose: () => void
  onSaved: () => void
  onUnauthorized: () => void
}

const bare = (t: string) => t.replace(/^tag:/, '')

export function NodeEditModal({ node, nodes, onClose, onSaved, onUnauthorized }: Props) {
  const { t } = useI18n()
  const dismiss = useModalDismiss(onClose)
  const [name, setName] = useState(node.name)
  const [kind, setKind] = useState<'server' | 'device'>(node.kind)
  const [admin, setAdmin] = useState(node.admin)
  const [muted, setMuted] = useState(node.muted)
  const [exitGateway, setExitGateway] = useState(node.exit_gateway)
  const [exitVia, setExitVia] = useState<Set<string>>(() => new Set(node.exit_via))
  // принудительный выход: весь трафик этой ноды через выбранный шлюз (exit-node)
  const [forceExit, setForceExit] = useState(node.force_exit || '')
  const [agentCmd, setAgentCmd] = useState<string | null>(null)
  // серверная сторона той же связи: устройства, которым разрешён выход через этот
  // шлюз = те, у кого в exit_via есть id этого сервера
  const [exitClients, setExitClients_] = useState<Set<string>>(
    () => new Set(nodes.filter((n) => n.exit_via.includes(node.id)).map((n) => n.id)),
  )
  const [description, setDescription] = useState(node.description ?? '')
  const [group, setGroup] = useState(node.group ?? '')
  const [subgroup, setSubgroup] = useState(node.subgroup ?? '')
  const [roles, setRoles] = useState<Set<string>>(() => new Set(node.forced_tags.map(bare)))
  const [newRole, setNewRole] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // уже заведённые группы/подгруппы — подсказками в datalist, чтобы не плодить
  // опечатки вроде «Acme» / «acme » разными записями
  const knownGroups = useMemo(
    () => [...new Set(nodes.map((n) => n.group).filter(Boolean))].sort(),
    [nodes],
  )
  const knownSubgroups = useMemo(
    () =>
      [...new Set(
        nodes.filter((n) => !group.trim() || n.group === group.trim()).map((n) => n.subgroup).filter(Boolean),
      )].sort(),
    [nodes, group],
  )

  // серверы-шлюзы выхода — из них устройство выбирает, через что ходить в интернет
  const gateways = useMemo(
    () => nodes.filter((n) => n.exit_gateway).sort((a, b) => a.name.localeCompare(b.name)),
    [nodes],
  )

  function toggleVia(id: string) {
    setExitVia((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  // устройства — из них шлюз выбирает, кому разрешить выход через себя
  const devices = useMemo(
    () => nodes.filter((n) => n.kind === 'device').sort((a, b) => a.name.localeCompare(b.name)),
    [nodes],
  )

  function toggleClient(id: string) {
    setExitClients_((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  // шлюзы, через которые можно ФОРСИРОВАТЬ весь трафик этой ноды (сама себя — нельзя)
  const forceOpts = useMemo(
    () => gateways.filter((g) => g.id !== node.id),
    [gateways, node.id],
  )

  async function loadAgentCmd() {
    try {
      setAgentCmd((await getAgent(node.id)).setup_oneline)
    } catch {
      /* панель недоступна — не критично, покажем позже */
    }
  }

  // известные роли сети + уже назначенные этой ноде (включая только что созданные)
  const known = useMemo(() => {
    const s = new Set(roleList(nodes))
    roles.forEach((r) => s.add(r))
    return [...s].sort()
  }, [nodes, roles])

  function toggleRole(r: string) {
    setRoles((prev) => {
      const n = new Set(prev)
      n.has(r) ? n.delete(r) : n.add(r)
      return n
    })
  }

  function addRole() {
    const r = bare(newRole.trim())
    if (r && /^[a-zA-Z0-9][a-zA-Z0-9-]*$/.test(r)) {
      setRoles((prev) => new Set(prev).add(r))
      setNewRole('')
    }
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const newName = name.trim()
      if (newName && newName !== node.name) {
        await renameNode(node.id, newName)
      }
      const effAdmin = kind === 'device' && admin
      // exit-поля осмысленны только для своего типа: сервер — шлюз, устройство —
      // список шлюзов. Чужие поля обнуляем, чтобы смена типа не оставила висеть
      // старое (напр. сервер стал устройством, а флаг шлюза остался).
      const effGateway = kind === 'server' && exitGateway
      // принудительный выход бессмыслен на самом шлюзе (сам себя не форсит)
      const effForce = effGateway ? '' : forceExit
      // источник форса должен иметь этот шлюз в «разрешённых» (для ACL-гранта)
      const viaSet = new Set(kind === 'device' ? exitVia : [])
      if (effForce) viaSet.add(effForce)
      const effVia = [...viaSet].sort()
      const oldVia = [...node.exit_via].sort()
      if (
        kind !== node.kind ||
        effAdmin !== node.admin ||
        muted !== node.muted ||
        effGateway !== node.exit_gateway ||
        effVia.join(',') !== oldVia.join(',') ||
        effForce !== (node.force_exit || '') ||
        description.trim() !== (node.description ?? '').trim() ||
        group.trim() !== (node.group ?? '').trim() ||
        subgroup.trim() !== (node.subgroup ?? '').trim()
      ) {
        await setNodeMeta(node.id, {
          description: description.trim(),
          kind,
          admin: effAdmin,
          muted,
          exit_gateway: effGateway,
          exit_via: effVia,
          force_exit: effForce,
          group: group.trim(),
          subgroup: subgroup.trim(),
        })
      }
      // Серверная сторона выбора выхода: обновляем, каким устройствам разрешён
      // выход через этот шлюз (правит их exit_via). Идёт ПОСЛЕ set_node_meta —
      // сервер уже помечен шлюзом и его тег объявлен. Снятие галки отдельно не
      // чистим: генератор и так отбрасывает выход через non-gateway.
      if (effGateway) {
        const initialClients = nodes
          .filter((n) => n.exit_via.includes(node.id))
          .map((n) => n.id)
          .sort()
        const nowClients = [...exitClients].sort()
        if (nowClients.join(',') !== initialClients.join(',')) {
          await setExitClients(node.id, nowClients)
        }
      }
      // Роль, набранная в поле и НЕ подтверждённая кнопкой «+ роль», всё равно
      // сохраняется: человек напечатал её и нажал «Сохранить» — это и есть
      // намерение. Заставлять нажимать лишнюю кнопку, а иначе молча терять ввод,
      // — верный способ потерять работу пользователя.
      const pending = bare(newRole.trim())
      const effRoles = pending ? new Set(roles).add(pending) : roles
      const newTags = [...effRoles].map((r) => `tag:${r}`)
      const oldTags = [...node.forced_tags].sort().join(',')
      if (newTags.slice().sort().join(',') !== oldTags) {
        await setNodeTags(node.id, newTags)
      }
      onSaved()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized()
        return
      }
      setError(err instanceof Error ? err.message : t('Ошибка'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <div className="clients-head">
          <h3>{t('Изменить ноду')}</h3>
          <button className="ghost" onClick={onClose}>
            {t('Закрыть')}
          </button>
        </div>

        <label className="field">
          <span>{t('Имя')}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>

        <label className="field">
          <span>{t('Тип')}</span>
          <select
            className="select"
            value={kind}
            onChange={(e) => setKind(e.target.value as 'server' | 'device')}
          >
            <option value="server">{t('Сервер')}</option>
            <option value="device">{t('Устройство')}</option>
          </select>
        </label>

        <label className="field field-check">
          <input
            type="checkbox"
            className="field-checkbox"
            checked={muted}
            onChange={(e) => setMuted(e.target.checked)}
          />
          <span>{t('Не слать алерты по этой ноде')}</span>
        </label>
        {muted && (
          <p className="muted small">
            {t('Наблюдение продолжается: статус и история в панели остаются, молчат только уведомления. Нода будет помечена в списке — заглушённый сервер, о котором забыли, опаснее шумного.')}
          </p>
        )}

        {kind === 'device' && (
          <label className="field field-row">
            <span>{t('Админ — полный доступ ко всем серверам')}</span>
            <input
              type="checkbox"
              className="field-checkbox"
              checked={admin}
              onChange={(e) => setAdmin(e.target.checked)}
            />
          </label>
        )}

        {kind === 'server' && (
          <>
            <label className="field field-check">
              <input
                type="checkbox"
                className="field-checkbox"
                checked={exitGateway}
                onChange={(e) => setExitGateway(e.target.checked)}
              />
              <span>{t('Шлюз выхода в интернет')}</span>
            </label>
            {exitGateway && (
              <>
                <p className="muted small">
                  {t('Сервер становится exit-нодой. Отметьте устройства, которым разрешён выход в интернет через него — это та же связь, что и в карточке устройства, просто с этой стороны. Сам себе выход шлюз не открывает.')}
                </p>
                <div className="field">
                  <span>{t('Разрешить выход через этот шлюз устройствам')}</span>
                  {devices.length === 0 ? (
                    <span className="muted small">{t('Устройств пока нет.')}</span>
                  ) : (
                    <div className="role-chips">
                      {devices.map((d) => (
                        <button
                          key={d.id}
                          type="button"
                          className={`role-chip${exitClients.has(d.id) ? ' role-chip-on' : ''}`}
                          onClick={() => toggleClient(d.id)}
                        >
                          {d.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </>
        )}

        {kind === 'device' && (
          <div className="field">
            <span>{t('Выход в интернет через')}</span>
            {gateways.length === 0 ? (
              <span className="muted small">
                {t('Нет шлюзов выхода. Отметьте «Шлюз выхода в интернет» на нужном сервере.')}
              </span>
            ) : (
              <>
                <div className="role-chips">
                  {gateways.map((g) => (
                    <button
                      key={g.id}
                      type="button"
                      className={`role-chip${exitVia.has(g.id) ? ' role-chip-on' : ''}`}
                      onClick={() => toggleVia(g.id)}
                    >
                      {g.name}
                    </button>
                  ))}
                </div>
                <span className="muted small">
                  {exitVia.size === 0
                    ? t('Ничего не отмечено — выход через шлюз запрещён: в трее Tailscale устройству не видно ни одной exit-ноды, оно ходит в интернет только своим каналом.')
                    : t('В трее Tailscale устройству видны и доступны только отмеченные шлюзы. Какой из них включить — выбирает пользователь на клиенте.')}
                </span>
              </>
            )}
          </div>
        )}

        {forceOpts.length > 0 && (
          <label className="field">
            <span>{t('Принудительно весь трафик через шлюз')}</span>
            <select
              className="select"
              value={forceExit}
              onChange={(e) => {
                const v = e.target.value
                setForceExit(v)
                // форс подразумевает разрешение: отметим шлюз и в «Выход через»
                if (v) setExitVia((prev) => new Set(prev).add(v))
              }}
            >
              <option value="">{t('— не форсировать')}</option>
              {forceOpts.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
            {forceExit && (
              <>
                <span className="muted small">
                  {t('Весь ИСХОДЯЩИЙ трафик этой ноды пойдёт через шлюз (exit-node, на другие ноды не течёт). Нужен агент на этой ноде — он же сохраняет доступ к ноде по её ПУБЛИЧНОМУ IP: входящие соединения (SSH, сервисы) отвечают напрямую, мимо шлюза.')}
                </span>
                {agentCmd ? (
                  <pre className="enroll-script cmd-oneline">{agentCmd}</pre>
                ) : (
                  <button type="button" className="ghost small" onClick={loadAgentCmd}>
                    {t('Нет агента? Показать команду установки')}
                  </button>
                )}
              </>
            )}
          </label>
        )}

        <div className="field-row">
          <label className="field">
            <span>{t('Группа (например организация)')}</span>
            <input
              list="nr-groups"
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              maxLength={63}
              placeholder={t('например: Acme')}
            />
            <datalist id="nr-groups">
              {knownGroups.map((g) => (
                <option key={g} value={g} />
              ))}
            </datalist>
          </label>
          <label className="field">
            <span>{t('Подгруппа (например проект)')}</span>
            <input
              list="nr-subgroups"
              value={subgroup}
              onChange={(e) => setSubgroup(e.target.value)}
              maxLength={63}
              placeholder={t('например: billing')}
            />
            <datalist id="nr-subgroups">
              {knownSubgroups.map((g) => (
                <option key={g} value={g} />
              ))}
            </datalist>
          </label>
        </div>

        <label className="field">
          <span>{t('Описание')}</span>
          <textarea
            className="node-desc-input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            maxLength={500}
            placeholder={t('например: сервер мониторинга')}
          />
        </label>

        <div className="field">
          <span>{t('Роли (теги)')}</span>
          <div className="role-chips">
            {known.length === 0 ? (
              <span className="muted small">{t('ролей ещё нет — создайте ниже')}</span>
            ) : (
              known.map((r) => (
                <button
                  key={r}
                  type="button"
                  className={`role-chip${roles.has(r) ? ' role-chip-on' : ''}`}
                  onClick={() => toggleRole(r)}
                >
                  #{r}
                </button>
              ))
            )}
          </div>
          <div className="role-add">
            <input
              value={newRole}
              onChange={(e) => setNewRole(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addRole()
                }
              }}
              placeholder={t('новая роль, напр. web')}
            />
            <button type="button" className="ghost small" onClick={addRole}>
              {t('+ роль')}
            </button>
          </div>
          <span className="muted small">
            {t('Роль = группа серверов. Доступ выдаётся на роль сразу для всех её серверов.')}
          </span>
        </div>

        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>
            {t('Отмена')}
          </button>
          <button onClick={save} disabled={busy}>
            {busy ? t('Сохранение…') : t('Сохранить')}
          </button>
        </div>
      </div>
    </div>
  )
}
