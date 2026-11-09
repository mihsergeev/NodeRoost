import { useCallback, useEffect, useMemo, useState } from 'react'
import { listNodes, type AclSelector, type Node } from './api'
import { groupGrants, portLabel, selLabel, toggleRule } from './aclui'
import { BulkGrantModal } from './BulkGrantModal'
import { useI18n } from './i18n'
import { useAclRules } from './useAclRules'

type Lens = 'who' | 'server'
type Tr = (s: string, p?: Record<string, string | number>) => string

function kindWord(sel: AclSelector, t: Tr) {
  return sel.kind === 'node' ? t('нода') : t('все')
}

function entityLabel(sel: AclSelector, nodes: Node[], t: Tr) {
  return selLabel(sel, nodes, t)
}

// Обзор всех выдач + массовая выдача. Точечно правится доступ внутри ноды.
export function AccessPage({ onUnauthorized }: { onUnauthorized: () => void }) {
  const { t } = useI18n()
  const { rules, loading, saving, error, reload, save } = useAclRules(onUnauthorized)
  const [nodes, setNodes] = useState<Node[]>([])
  const [lens, setLens] = useState<Lens>('who')
  const [addOpen, setAddOpen] = useState(false)

  const loadNodes = useCallback(() => {
    listNodes().then(setNodes).catch(() => setNodes([]))
  }, [])

  useEffect(() => {
    loadNodes()
  }, [loadNodes])

  const busy = loading || saving
  const grants = useMemo(() => (rules ? groupGrants(rules, lens) : []), [rules, lens])

  return (
    <>
      <div className="page-head">
        <h2>{t('Доступы')}</h2>
        <div className="page-head-actions">
          <button className="ghost" disabled={busy} onClick={() => {
            loadNodes()
            void reload()
          }}>
            {t('Обновить')}
          </button>
          <button onClick={() => setAddOpen(true)} disabled={busy || rules === null}>
            {t('+ Выдать доступ')}
          </button>
        </div>
      </div>

      <div className="grid-toggle access-lens">
        <button className={lens === 'who' ? 'seg-active' : ''} onClick={() => setLens('who')}>
          {t('По кому')}
        </button>
        <button className={lens === 'server' ? 'seg-active' : ''} onClick={() => setLens('server')}>
          {t('По серверам')}
        </button>
      </div>
      <p className="muted small access-hint">
        {t('Убрать доступ — крестиком на нужном чипе. Выдать сразу нескольким — кнопкой выше; точечно — внутри карточки ноды.')}
      </p>
      {error && <p className="form-error" role="alert">{error}</p>}

      {rules === null ? (
        loading && <p className="muted">{t('загрузка…')}</p>
      ) : grants.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <span className="empty-emoji">🔒</span>
            <h3>{t('Доступов пока нет')}</h3>
            <p>{t('Всё запрещено. Нажмите «Выдать доступ», чтобы разрешить первую связь.')}</p>
            <button disabled={busy} onClick={() => setAddOpen(true)}>{t('+ Выдать доступ')}</button>
          </div>
        </div>
      ) : (
        <div className="grant-list">
          {grants.map((g) => (
            <div key={`${g.entity.kind}:${g.entity.value}`} className="card grant-card">
              <div className="grant-head">
                <span className={`ent-dot ent-${g.entity.kind}`} />
                <span className="grant-name">{entityLabel(g.entity, nodes, t)}</span>
                <span className="muted small">{kindWord(g.entity, t)}</span>
              </div>
              {g.rows.map((row) => (
                <div key={row.port} className="grant-row">
                  <span className="port-badge">{portLabel(row.port, t)}</span>
                  <span className="grant-arrow">{lens === 'who' ? '→' : '←'}</span>
                  <span className="ent-chips">
                    {row.others.map((o) => (
                      <span key={`${o.kind}:${o.value}`} className="ent-chip">
                        {entityLabel(o, nodes, t)}
                        <button
                          className="chip-x"
                          disabled={busy}
                          aria-label={t('Убрать доступ')}
                          title={t('Убрать доступ')}
                          onClick={() =>
                            save(
                              // в разрезе «по кому» карточка — источник, чип — цель;
                              // в разрезе «по серверам» наоборот
                              toggleRule(
                                rules,
                                lens === 'who' ? g.entity : o,
                                lens === 'who' ? o : g.entity,
                                row.port,
                                false,
                              ),
                            )
                          }
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {addOpen && rules && (
        <BulkGrantModal
          nodes={nodes}
          rules={rules}
          onClose={() => setAddOpen(false)}
          onApply={(next) => {
            setAddOpen(false)
            save(next)
          }}
        />
      )}
    </>
  )
}
