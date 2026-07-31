import { useEffect, useState } from 'react'
import { getConfig, getToken, setToken, type PanelConfig } from './api'
import { LoginPage } from './LoginPage'
import { NodesPage } from './NodesPage'
import { AccessPage } from './AccessPage'
import { RoutingPage } from './RoutingPage'
import { SettingsPage } from './SettingsPage'
import { Menu } from './Menu'
import { BrandHorizontal } from './Logo'
import { TwoFAModal } from './TwoFAModal'
import { PasswordModal } from './PasswordModal'
import { AlertsModal } from './AlertsModal'
import { BackupsModal } from './BackupsModal'
import { LogsModal } from './LogsModal'
import { useI18n } from './i18n'
import './App.css'

type View = 'servers' | 'devices' | 'access' | 'routing' | 'settings'

function App() {
  const { t, lang, setLang } = useI18n()
  const [authed, setAuthed] = useState(() => getToken() !== null)
  const [view, setView] = useState<View>('servers')
  // растёт при каждом клике по навигации — используется как key, чтобы страница
  // пересоздавалась (сбрасывала внутреннее состояние, напр. открытую деталь ноды)
  const [navSeq, setNavSeq] = useState(0)
  // Ссылка из алерта ведёт прямо в ноду: panel/#node-<id>. Роутера в панели нет,
  // поэтому разбираем хеш один раз при загрузке и передаём id странице серверов.
  const [deepNodeId, setDeepNodeId] = useState<string | null>(() => {
    const m = /^#node-(\d+)$/.exec(window.location.hash)
    return m ? m[1] : null
  })
  const [version, setVersion] = useState<string | null>(null)
  // статус control-сервера: раньше занимал целую карточку на страницах нод —
  // это фон, а не действие, поэтому живёт точкой в шапке
  const [hs, setHs] = useState<string | null>(null)
  const [cfg, setCfg] = useState<PanelConfig | null>(null)
  const [twoFaOpen, setTwoFaOpen] = useState(false)
  const [pwOpen, setPwOpen] = useState(false)
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [backupsOpen, setBackupsOpen] = useState(false)
  const [logsOpen, setLogsOpen] = useState(false)
  const [theme, setTheme] = useState<'dark' | 'light'>(() =>
    document.documentElement.getAttribute('data-theme') === 'light'
      ? 'light'
      : 'dark',
  )

  // переход по навигации: меняем раздел и всегда показываем список (не деталь)
  function go(v: View) {
    setView(v)
    setNavSeq((s) => s + 1)
  }

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    if (next === 'light') {
      document.documentElement.setAttribute('data-theme', 'light')
    } else {
      document.documentElement.removeAttribute('data-theme')
    }
    localStorage.setItem('noderoost_theme', next)
  }

  // статус control-сервера опрашиваем периодически: индикатор в шапке должен
  // показывать «сейчас», а не момент загрузки страницы
  useEffect(() => {
    const poll = () =>
      fetch('/api/health')
        .then((res) => (res.ok ? res.json() : null))
        .then((body) => {
          setVersion(body?.version ?? null)
          setHs(body?.headscale ?? null)
        })
        .catch(() => setHs('down'))
    poll()
    const id = setInterval(poll, 30000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (!authed) return
    getConfig()
      .then(setCfg)
      .catch(() => {})
  }, [authed])

  function logout() {
    setToken(null)
    setAuthed(false)
  }

  return (
    <main className="page">
      <header className="header">
        {/* на экране входа лого уже есть в карточке — в шапке не дублируем */}
        {authed && (
          <button className="brand" onClick={() => go('servers')} title={t('На главную')}>
            <BrandHorizontal />
          </button>
        )}
        {authed && (
          <nav className="topnav">
            <button
              className={view === 'servers' ? 'navlink navlink-active' : 'navlink'}
              onClick={() => go('servers')}
            >
              {t('Серверы')}
            </button>
            <button
              className={view === 'devices' ? 'navlink navlink-active' : 'navlink'}
              onClick={() => go('devices')}
            >
              {t('Устройства')}
            </button>
            <button
              className={view === 'access' ? 'navlink navlink-active' : 'navlink'}
              onClick={() => go('access')}
            >
              {t('Доступы')}
            </button>
            <button
              className={view === 'routing' ? 'navlink navlink-active' : 'navlink'}
              onClick={() => go('routing')}
            >
              {t('Маршрутизация')}
            </button>
          </nav>
        )}
        <span className="header-right">
          {authed && hs && (
            <span
              className="hs-status"
              title={
                (hs === 'ok'
                  ? t('headscale доступен')
                  : hs === 'down'
                    ? t('headscale недоступен')
                    : hs === 'unauthorized'
                      ? t('headscale не принимает ключ панели — выпустите новый и впишите в .env')
                      : t('API-ключ headscale не задан')) +
                (cfg?.headscale_server_url ? ` · ${cfg.headscale_server_url}` : '')
              }
            >
              <span
                className={`dot ${hs === 'ok' ? 'dot-ok' : hs === 'down' || hs === 'unauthorized' ? 'dot-fail' : 'dot-unknown'}`}
              />
              <span className="muted small hs-status-label">headscale</span>
            </span>
          )}
          {version && (
            <a
              className="muted source-link"
              href="https://github.com/mihsergeev/noderoost"
              target="_blank"
              rel="noopener noreferrer"
              title={t('Исходный код (BSD-3-Clause)')}
            >
              v{version}
            </a>
          )}
          <button className="ghost icon-btn" onClick={toggleTheme} title={t('Тема')}>
            {theme === 'dark' ? '☀' : '☾'}
          </button>
          <button
            className="ghost icon-btn"
            onClick={() => setLang(lang === 'ru' ? 'en' : 'ru')}
            title={t('Язык')}
          >
            {lang === 'ru' ? 'EN' : 'RU'}
          </button>
          {authed && (
            <Menu
              className="ghost icon-btn"
              caret={false}
              title={t('Меню')}
              label={<span className="menu-gear">⚙</span>}
              items={[
                // Настройки — полноценная страница, а не модалка: сюда ходят
                // редко (ключи API, DNS, диапазон меша), и в верхней навигации
                // они занимали место наравне с тем, чем пользуются каждый день
                { label: t('Настройки'), onClick: () => go('settings') },
                { divider: true },
                { label: t('Журнал и диагностика'), onClick: () => setLogsOpen(true) },
                { label: t('Алерты'), onClick: () => setAlertsOpen(true) },
                { label: t('Бэкапы'), onClick: () => setBackupsOpen(true) },
                {
                  label: t('Двухфакторная аутентификация'),
                  onClick: () => setTwoFaOpen(true),
                },
                { label: t('Сменить пароль'), onClick: () => setPwOpen(true) },
                { divider: true },
                { label: t('Выйти'), danger: true, onClick: logout },
              ]}
            />
          )}
        </span>
      </header>

      {authed ? (
        view === 'access' ? (
          <AccessPage onUnauthorized={logout} />
        ) : view === 'routing' ? (
          <RoutingPage key={navSeq} onUnauthorized={logout} />
        ) : view === 'settings' ? (
          <SettingsPage onUnauthorized={logout} />
        ) : view === 'devices' ? (
          <NodesPage key={navSeq} kind="device" onUnauthorized={logout} />
        ) : (
          <NodesPage
            key={navSeq}
            kind="server"
            onUnauthorized={logout}
            openNodeId={deepNodeId}
            onOpened={() => {
              setDeepNodeId(null)
              history.replaceState(null, '', window.location.pathname)
            }}
          />
        )
      ) : (
        <LoginPage onLogin={() => setAuthed(true)} />
      )}

      {twoFaOpen && (
        <TwoFAModal onClose={() => setTwoFaOpen(false)} onUnauthorized={logout} />
      )}

      {pwOpen && (
        <PasswordModal onClose={() => setPwOpen(false)} onUnauthorized={logout} />
      )}

      {alertsOpen && (
        <AlertsModal onClose={() => setAlertsOpen(false)} onUnauthorized={logout} />
      )}

      {backupsOpen && (
        <BackupsModal onClose={() => setBackupsOpen(false)} onUnauthorized={logout} />
      )}

      {logsOpen && <LogsModal onClose={() => setLogsOpen(false)} onUnauthorized={logout} />}
    </main>
  )
}

export default App
