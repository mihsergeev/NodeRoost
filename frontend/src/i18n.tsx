import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'

export type Lang = 'ru' | 'en'

// Ключ = русская строка (дефолт). Значение = английский перевод.
// Отсутствующий ключ → возвращается русский (мягкий фолбэк).
const EN: Record<string, string> = {
  // --- шапка / навигация ---
  'Ноды': 'Nodes',
  'Пользователи': 'Users',
  'Выйти': 'Log out',
  'На главную': 'Home',
  'Тема': 'Theme',
  'Язык': 'Language',
  'Меню': 'Menu',
  'Сменить пароль': 'Change password',
  'Исходный код (BSD-3-Clause)': 'Source code (BSD-3-Clause)',

  // --- вход ---
  'Вход в панель': 'Sign in',
  'Войти': 'Log in',
  'Проверка…': 'Checking…',
  'Не удалось войти': 'Sign in failed',
  'Логин': 'Login',
  'Пароль': 'Password',
  'Неверный логин или пароль': 'Wrong login or password',
  'Код из приложения (2FA)': 'Code from the app (2FA)',
  'Неверный код 2FA': 'Invalid 2FA code',

  // --- заглушки разделов ---
  'Скоро': 'Coming soon',
  'Управление нодами': 'Node management',
  'Список нод, теги, маршруты, exit-node, сроки ключей — появятся на этапе 2.':
    'Node list, tags, routes, exit nodes and key expiry — arriving in stage 2.',
  'Управление пользователями и pre-auth-ключами появится на этапе 3.':
    'User and pre-auth key management arrives in stage 3.',
  'Подключение control-сервера': 'Control server connection',
  'headscale доступен': 'headscale reachable',
  'headscale недоступен': 'headscale unreachable',
  'API-ключ headscale не задан': 'headscale API key not set',
  'Адрес для подключения нод:': 'Login server for nodes:',
  'Команда подключения ноды:': 'Node join command:',
  'загрузка…': 'loading…',

  // --- ноды ---
  'Обновить': 'Refresh',
  'Всего': 'Total',
  'Онлайн': 'Online',
  'Оффлайн': 'Offline',
  'Поиск по имени, IP, тегу…': 'Search by name, IP, tag…',
  'Поиск: имя, IP, тег…': 'Search: name, IP, tag…',
  'Нод пока нет': 'No nodes yet',
  'Подключите первую ноду командой ниже — она появится здесь.':
    'Connect your first node with the command below — it will appear here.',
  'Ничего не найдено': 'Nothing found',
  'Не удалось загрузить ноды': 'Failed to load nodes',
  'Изменить': 'Edit',
  'Отозвать ключ': 'Revoke key',
  'онлайн': 'online',
  'оффлайн': 'offline',
  'видели {ago}': 'seen {ago}',
  'только что': 'just now',
  '{n} мин назад': '{n} min ago',
  '{n} ч назад': '{n} h ago',
  '{n} дн назад': '{n} d ago',
  'Владелец': 'Owner',
  'Теги': 'Tags',
  'Ключ истёк': 'Key expired',
  'без имени': 'unnamed',
  'нет тегов': 'no tags',
  'Изменить ноду': 'Edit node',
  'Группа (например организация)': 'Group (e.g. organization)',
  'Подгруппа (например проект)': 'Subgroup (e.g. project)',
  'например: Acme': 'e.g. Acme',
  'например: billing': 'e.g. billing',
  'Админские': 'Admin',
  'Без группы': 'No group',
  'Перетащите в другую группу': 'Drag to another group',
  'без подгруппы': 'no subgroup',
  'Имя': 'Name',
  'Теги (через запятую, префикс tag: необязателен)':
    'Tags (comma-separated, tag: prefix optional)',
  'Владелец (пользователь)': 'Owner (user)',
  'Сохранить': 'Save',
  'Сохранение…': 'Saving…',
  'Отозвать ключ ноды «{name}»? Она отключится и не подключится обратно, пока её не переподключить заново.':
    'Revoke the key of node “{name}”? It will disconnect and stay out until you re-enroll it.',
  'Удалить ноду «{name}»? Она будет удалена из тайлнета.':
    'Delete node “{name}”? It will be removed from the tailnet.',
  'Удалить': 'Delete',
  'control-сервер:': 'control server:',
  'Нажмите «+ Добавить ноду», чтобы подключить первую машину.':
    'Click “+ Add node” to connect your first machine.',

  // --- добавление ноды (enroll) ---
  '+ Добавить ноду': '+ Add node',
  'Добавить ноду': 'Add node',
  'Нода добавлена': 'Node added',
  'Скрипт подключения готов': 'Enrollment script ready',
  'Пока создан только одноразовый ключ. Нода появится в списке, когда подключится.':
    'So far only a one-time key was issued. The node will appear in the list once it connects.',
  'Имя ноды': 'Node name',
  'Меш закрыт: укажите публичный IP ноды — он откроется на фаерволе control-сервера. Без IP нода подключится только с уже разрешённого адреса.':
    'The mesh is closed: enter the node’s public IP — it will be opened on the control-server firewall. Without an IP the node can only connect from an already-allowed address.',
  'Операционная система': 'Operating system',
  'Создать': 'Create',
  'Создание…': 'Creating…',
  'Выполните скрипт на ноде под root. Ключ одноразовый.':
    'Run the script on the node as root. The key is single-use.',
  'Выполните скрипт на ноде в PowerShell от администратора. Ключ одноразовый.':
    'Run the script on the node in an administrator PowerShell. The key is single-use.',
  'Выполните скрипт на Mac в Терминале (нужен Homebrew). Ключ одноразовый.':
    'Run the script on the Mac in Terminal (Homebrew required). The key is single-use.',
  'На Android скрипта нет — выполните шаги в приложении Tailscale. Ключ одноразовый.':
    'There is no script on Android — follow the steps in the Tailscale app. The key is single-use.',
  'Ключ не попадёт в историю шелла — скрипт первой строкой отключает её запись. Ваша история при этом сохраняется. В zsh (macOS) опции нет: там надёжнее сохранить скрипт в файл и запустить.':
    'The key stays out of shell history — the script disables recording on its first line. Your own history is kept. zsh (macOS) has no such option: there it is safer to save the script to a file and run it.',
  'Скопировать скрипт': 'Copy script',
  'Скопировано ✓': 'Copied ✓',
  'IP добавлен в вайтлист — фаервол откроется в течение ~минуты.':
    'IP added to the allow-list — the firewall opens within ~a minute.',
  'Ждём подключения ноды — статус обновится сам…':
    'Waiting for the node to connect — this updates automatically…',
  'Нода подключена ✓': 'Node connected ✓',
  'Храните архив как приватный ключ: внутри секрет второго фактора, хеш пароля администратора, токены агентов и ключи control-сервера. Кто получил файл — получил и сеть.':
    "Keep the archive as you would a private key: it holds the second-factor secret, the admin's password hash, the agent tokens and the control server's own keys. Whoever gets the file gets the network.",
  'headscale не принимает ключ панели — выпустите новый и впишите в .env':
    "headscale rejects the panel's key — issue a new one and put it in .env",
  'Изменения записаны, но headscale не перезапущен: на хосте нет помощника. Выполните на сервере `sudo ops/update.sh` (он его поставит) или перезапустите вручную: `docker compose restart headscale`.':
    'The change is saved but headscale has not restarted: the host helper is missing. Run `sudo ops/update.sh` on the server (it installs one) or restart it by hand: `docker compose restart headscale`.',
  'Создайте бота через @BotFather, вставьте его токен и chat_id (свой ID узнаете у @userinfobot).':
    'Create a bot with @BotFather, paste its token and a chat_id (@userinfobot tells you yours).',
  'Выбор exit-ноды — на стороне клиента. Отдайте это пользователю.':
    'Picking an exit node happens in the client. Hand this to the person using it.',
  'Windows (PowerShell от администратора)':
    'Windows (PowerShell as administrator)',
  'То же самое мышкой: иконка Tailscale в трее → Exit nodes → «{name}»; выключить — «None».':
    'The same by mouse: the Tailscale tray icon → Exit nodes → “{name}”; to switch off, “None”.',
  'Android / iOS: в приложении Tailscale → меню → Exit Node → «{name}»; выключить — пункт «None».':
    'Android / iOS: in the Tailscale app → menu → Exit Node → “{name}”; to switch off, “None”.',
  'Все устройства':
    'All devices',
  'Порт':
    'Port',
  'Использовать только эти серверы': 'Use these servers only',
  'Без галочки ноды продолжают пользоваться своим DNS, а эти серверы добавляются к нему. С галочкой весь DNS ноды идёт только сюда — сервер перестанет видеть внутренние имена, которые знал его прежний резолвер.':
    'Left unticked, nodes keep their own DNS and these servers are added to it. Ticked, all of a node’s DNS goes here only — the server stops seeing the internal names its previous resolver knew.',
  'удалённая нода': 'deleted node',
  'Эта машина уже была в сети под именем': 'This machine was already on the network as',
  'её запись переиспользована и переименована. Прежние настройки доступа и маршруты сохранены.':
    'its record has been reused and renamed. Its access settings and routes are kept.',

  // --- маршруты / exit-node ---
  'Маршруты': 'Routes',
  'Маршруты · {name}': 'Routes · {name}',
  'Нода ничего не анонсирует. Выполните на ней: tailscale set --advertise-routes=10.0.0.0/24 или --advertise-exit-node.':
    'The node advertises nothing. Run on it: tailscale set --advertise-routes=10.0.0.0/24 or --advertise-exit-node.',
  'Exit-node': 'Exit node',
  'ВСЁ или НИЧЕГО: клиент, выбравший эту ноду, гонит через неё весь свой трафик. «Только определённые сайты через VPN» так не делается — для этого маршрут ниже.':
    'ALL or NOTHING: a client that picks this node sends all of its traffic through it. “Only certain sites over the VPN” is not done this way — use the route below.',
  'Subnet-маршруты': 'Subnet routes',
  'активен': 'active',
  'маршруты ожидают': 'routes pending',
  'Одобрить маршруты': 'Approve routes',
  'Пустить через эту ноду только конкретный адрес или подсеть (а не весь трафик, как exit):':
    'Route only a specific address or subnet through this node (not all traffic, as an exit node does):',
  'Чтобы управлять маршрутами прямо отсюда, поставьте на ноду агента — он раз в минуту забирает настройки из панели и применяет их сам. Выполните на ноде под root один раз:':
    'To manage routes from here, install the agent on the node — once a minute it pulls the settings from the panel and applies them itself. Run once on the node as root:',
  'Статус обновится сам, как только агент отзовётся.':
    'The status updates by itself as soon as the agent reports in.',
  'Агент установлен — настройки применяются автоматически':
    'Agent installed — settings are applied automatically',
  'Через эту ноду ходить только на эти адреса (остальной трафик клиента — напрямую):':
    'Route only these addresses through this node (the rest of the client traffic goes direct):',
  'маршрутов нет': 'no routes',
  '+ Маршрут': '+ Route',
  'Нода применит изменения в течение минуты, одобрение произойдёт само.':
    'The node applies changes within a minute; approval happens on its own.',
  'Снять агента с ноды': 'Remove the agent from the node',
  'Нода анонсирует сама (не из панели) — одобрить:':
    'Advertised by the node itself (not from the panel) — approve:',
  'Собрать команду': 'Build command',
  'Выполните на самой ноде, затем обновите — маршрут появится здесь для одобрения:':
    'Run this on the node itself, then refresh — the route will show up here for approval:',
  'не удалось определить адрес': 'could not resolve an address',
  'Есть неодобренные маршруты': 'There are unapproved routes',
  'Система': 'System',
  'Клиент Tailscale': 'Tailscale client',
  'Виден с адреса': 'Seen from',
  'прямое соединение': 'direct connection',
  'только через DERP': 'relayed via DERP only',
  'контейнер': 'container',
  'Добавлена': 'Added',
  'Была в сети': 'Last seen',
  'Срок ключа': 'Key expires',
  'не истекает': 'never expires',
  'Скопировать IP': 'Copy IP',
  'Скопировать «имя (IP)»': 'Copy “name (IP)”',
  'имя + IP': 'name + IP',
  'Exit-нода': 'Exit node',
  'клиенты смогут выходить в интернет через неё; скрипт включит и закрепит ip_forward. Одобрить exit в «Маршрутах» после подключения.':
    'clients can reach the internet through it; the script enables and persists ip_forward. Approve the exit route in “Routes” after it connects.',
  'Затем одобри exit-маршрут в «Маршрутах».': 'Then approve the exit route in “Routes”.',
  'Чтобы сделать ноду exit-нодой, выполни на самой ноде (Linux, под sudo), затем обнови список:':
    'To make this an exit node, run on the node itself (Linux, with sudo), then refresh:',
  'Для subnet-маршрутов на ноде: tailscale set --advertise-routes=10.0.0.0/24.':
    'For subnet routes, run on the node: tailscale set --advertise-routes=10.0.0.0/24.',

  // --- политика (ACL) ---
  'Политика': 'Policy',
  'Политика доступа': 'Access policy',
  'Отменить': 'Discard',
  'Правила доступа тайлнета в формате HuJSON (JSON с комментариями). Проверяется headscale при сохранении.':
    'Tailnet access rules in HuJSON (JSON with comments). Validated by headscale on save.',
  'Политика ещё не задана — сохраните шаблон, чтобы инициализировать.':
    'No policy set yet — save the template to initialize it.',
  'Политика сохранена.': 'Policy saved.',
  'Обновлена:': 'Updated:',
  'По умолчанию весь трафик между нодами ЗАПРЕЩЁН. Разрешайте нужное правилами: Источник → Назначение по указанным портам. Правила направленные — обратный доступ нужно разрешать отдельно.':
    'By default all traffic between nodes is DENIED. Allow what you need with rules: Source → Destination on the given ports. Rules are directional — the reverse direction must be allowed separately.',
  'Правил нет — всё запрещено. Добавьте первое правило ниже.':
    'No rules — everything is denied. Add the first rule below.',
  'Любой (*)': 'Any (*)',
  'Любой': 'Any',
  'любой порт': 'any port',
  'порт(ы) {p}': 'port(s) {p}',
  'Источник': 'Source',
  'Назначение': 'Destination',
  'Порт / сервис': 'Port / service',
  'Свой…': 'Custom…',
  'Порт(ы)': 'Port(s)',
  '+ Добавить правило': '+ Add rule',
  'Показать сгенерированный HuJSON': 'Show generated HuJSON',
  'Скрыть HuJSON': 'Hide HuJSON',
  // --- деталь ноды (read-only сводка доступа) ---
  'Открыть': 'Open',
  'Доступ': 'Access',
  'Описание': 'Description',
  'например: сервер мониторинга': 'e.g. monitoring server',
  // серверы / устройства
  'Серверы': 'Servers',
  'Устройства': 'Devices',
  '+ Добавить сервер': '+ Add server',
  '+ Добавить устройство': '+ Add device',
  'Серверов пока нет': 'No servers yet',
  'Устройств пока нет': 'No devices yet',
  'Добавьте сервер или пометьте существующую ноду типом «сервер».':
    'Add a server or mark an existing node as “server”.',
  'Добавьте устройство или пометьте ноду типом «устройство» в «Изменить ноду».':
    'Add a device or mark a node as “device” in Edit node.',
  'Тип': 'Type',
  'Сервер': 'Server',
  'Устройство': 'Device',
  'админ': 'admin',
  'Админ — полный доступ ко всем серверам': 'Admin — full access to all servers',
  'Админ-устройство: полный доступ ко всем серверам. К нему самому — никто.':
    'Admin device: full access to all servers. Nothing can reach it.',
  'Админ-устройство: полный доступ ко всем серверам. Подключиться к нему нельзя.':
    'Admin device: full access to all servers. It cannot be connected to.',
  'никто — к устройствам подключаться нельзя, они не видят друг друга':
    'nobody — devices cannot be connected to, and they never see each other',
  'никто — админ недостижим': 'no one — the admin is unreachable',
  'IP для вайтлиста на серверах:': 'IP to whitelist on servers:',
  '— он стабилен, пока нода существует.': '— it stays stable while the node exists.',
  'все серверы': 'all servers',
  'Все серверы': 'All servers',
  'Роли (теги)': 'Roles (tags)',
  'ролей ещё нет — создайте ниже': 'no roles yet — create one below',
  'новая роль, напр. web': 'new role, e.g. web',
  '+ роль': '+ role',
  'Роль = группа серверов. Доступ выдаётся на роль сразу для всех её серверов.':
    'A role is a group of servers. Grant access to a role for all its servers at once.',

  // --- журнал / диагностика ---
  'Журнал и диагностика': 'Log and diagnostics',
  'Сводка': 'Summary',
  'Журнал действий': 'Activity log',
  'Логи headscale': 'headscale logs',
  'Версия панели': 'Panel version',
  'control-сервер': 'control server',
  'доступен': 'up',
  'недоступен': 'down',
  'встроенный': 'embedded',
  '{total} · серверов {s} · устройств {d} · онлайн {o}':
    '{total} · servers {s} · devices {d} · online {o}',
  'Последний бэкап': 'Last backup',
  'Записей пока нет.': 'No entries yet.',
  'Время': 'Time',
  'Кто': 'Who',
  'Действие': 'Action',
  'Объект': 'Object',
  'Собираю…': 'Collecting…',
  'Собираю логи headscale (до ~5 c)…': 'Collecting headscale logs (up to ~5 s)…',
  // метки действий журнала
  'Вход': 'Login',
  'Вход заблокирован': 'Login blocked',
  'Смена пароля': 'Password change',
  'Добавлена нода': 'Node added',
  'Переименована нода': 'Node renamed',
  'Роли/теги ноды': 'Node roles/tags',
  'Изменена нода': 'Node updated',
  'Описание ноды': 'Node description',
  'Смена владельца': 'Owner changed',
  'Маршруты ноды': 'Node routes',
  'Истёк ключ ноды': 'Node key expired',
  'Удалена нода': 'Node deleted',
  'Изменён доступ': 'Access changed',
  'Изменена политика': 'Policy changed',
  'Изменён DNS': 'DNS changed',
  'Создан API-ключ': 'API key created',
  'Истёк API-ключ': 'API key expired',
  'Создан пользователь': 'User created',
  'Переименован пользователь': 'User renamed',
  'Удалён пользователь': 'User deleted',
  'Истёк ключ подключения': 'Pre-auth key expired',
  'Бэкап': 'Backup',
  'Добавить сервер': 'Add server',
  'Добавить устройство': 'Add device',
  '— выберите —': '— select —',
  '＋ Новый пользователь…': '＋ New user…',
  'Имя нового пользователя': 'New user name',
  'Устройство привяжется к этому человеку — доступ ему выдаётся сразу на все его устройства.':
    'The device will belong to this person — access is granted to them for all their devices at once.',

  // --- доступ внутри ноды (редактируется) ---
  'Кто может подключаться сюда': 'Who can connect here',
  'Куда может ходить эта нода': 'Where this node can reach',
  'никто (всё запрещено)': 'no one (all denied)',
  'никуда (всё запрещено)': 'nowhere (all denied)',
  '+ Разрешить': '+ Allow',
  'Разрешить': 'Allow',
  'Убрать': 'Remove',
  'Разрешить доступ к «{name}»': 'Allow access to “{name}”',
  'Куда может ходить «{name}»': 'Where “{name}” can reach',
  'Кому разрешить': 'Allow whom',
  'На какие сервера': 'To which servers',
  'все серверы (админ)': 'all servers (admin)',
  'Клиентам: как ходить в интернет через эту exit-ноду': 'For clients: how to route internet through this exit node',
  'Чтобы эта exit-нода появилась у клиента, отметьте её «Шлюзом выхода» на этом сервере и разрешите нужным устройствам (в «Изменить ноду» — здесь же или в карточке устройства). Без этого клиент увидит «No exit node available».':
    'For this exit node to appear on the client, mark it an “exit gateway” on this server and allow the devices you want (in “Edit node” — here or on the device card). Otherwise the client sees “No exit node available”.',
  'Linux / macOS (в терминале)': 'Linux / macOS (in a terminal)',
  'Скопировать команду установки': 'Copy install command',
  'Windows / Android / iOS: в приложении Tailscale → меню → Exit Node → «{name}»; выключить — пункт «None».':
    'Windows / Android / iOS: in the Tailscale app → menu → Exit Node → “{name}”; to disable, choose “None”.',
  'Маршрутизация': 'Routing',
  'весь трафик': 'all traffic',
  'Полный туннель — весь трафик устройства через эту ноду': 'Full tunnel — all of the device’s traffic through this node',
  'Как exit-нода, но привязано к устройству: только выбранные ходят через эту ноду, остальные — нет. Трафик к другим нодам меша остаётся прямым.':
    'Like an exit node, but bound to the device: only the chosen ones go through this node, others do not. Traffic to other mesh nodes stays direct.',
  'Весь интернет-трафик выбранных уходит через эту ноду, включая ОБРАТНЫЙ путь — поэтому доступ к источнику по его ПУБЛИЧНОМУ IP (например SSH к серверу) оборвётся. По тайнет-адресу (100.x) доступ остаётся: он мешевый и не туннелируется. Годится для клиентских устройств (ноутбук/телефон), а не для сервера, которым вы управляете по внешнему IP.':
    'All internet traffic of the chosen ones goes through this node, including the RETURN path — so reaching the source over its PUBLIC IP (e.g. SSH to a server) will drop. Access over the tailnet address (100.x) stays: it is mesh traffic and is not tunnelled. Best for client devices (laptop/phone), not a server you administer over its public IP.',
  'ходят на': 'go to',
  '1 · Кто ходит': '1 · Who goes',
  '2 · Куда ходят': '2 · Where to',
  '3 · Через какую ноду': '3 · Through which node',
  'На конкретный адрес': 'To a specific address',
  'Весь трафик (полный туннель)': 'All traffic (full tunnel)',
  'Как exit-нода, но привязано к устройству: только выбранные ходят через эту ноду, остальные — нет. Мешевый трафик (в т.ч. SSH к самой ноде) и её собственный интернет остаются прямыми — доступ к ней не пропадёт.':
    'Like an exit node, but bound to the device: only the chosen ones go through this node, others do not. Mesh traffic (including SSH to the node itself) and its own internet stay direct — you won’t lose access to it.',
  'выберите сервер…': 'pick a server…',
  'порт': 'port',
  'весь трафик (устарело)': 'all traffic (deprecated)',
  'Полный туннель через subnet-маршруты убран как небезопасный — удалите это направление. Весь трафик через узел делается exit-нодой (см. подсказку выше).':
    'The subnet-route full tunnel was removed as unsafe — delete this direction. Routing all traffic through a node is done with an exit node (see the note above).',
  'Весь трафик сервера через другой узел — это не направление, а exit-нода: пометьте узел «Шлюзом выхода», разрешите его источнику, затем на источнике выполните tailscale set --exit-node. Так трафик не течёт на другие ноды. Управляйте сервером в это время по его тайнет-адресу (100.x).':
    'Routing a server’s whole traffic through another node is not a direction but an exit node: mark the node an “exit gateway”, allow the source, then on the source run tailscale set --exit-node. That way traffic does not leak to other nodes. While active, manage the server over its tailnet address (100.x).',
  'Отметьте кто, куда и через какую ноду — здесь появится итог':
    'Tick who, where and through which node — the result appears here',
  'Кто ходит: все устройства': 'Who goes there: all devices',
  'Кто ходит: все серверы': 'Who goes there: all servers',
  'Кто ходит: отмечено {n}': 'Who goes there: {n} selected',
  'поиск ноды…': 'search a node…',
  'сервер': 'server',
  'устройство': 'device',
  'Не слать алерты по этой ноде': 'Do not alert about this node',
  'Наблюдение продолжается: статус и история в панели остаются, молчат только уведомления. Нода будет помечена в списке — заглушённый сервер, о котором забыли, опаснее шумного.':
    'Monitoring continues: status and history stay in the panel, only the notifications go quiet. The node is marked in the list — a muted server nobody remembers is worse than a noisy one.',
  'без алертов': 'muted',
  'Алерты по этой ноде выключены': 'Alerts for this node are off',
  // --- шлюз выхода в интернет (via) ---
  'шлюз': 'gateway',
  'выход через {n}': 'exit via {n}',
  'Шлюз выхода в интернет': 'Internet exit gateway',
  'Сервер становится exit-нодой. Отметьте устройства, которым разрешён выход в интернет через него — это та же связь, что и в карточке устройства, просто с этой стороны. Сам себе выход шлюз не открывает.':
    'The server becomes an exit node. Tick the devices allowed to reach the internet through it — the same link you can edit from the device card, just from this side. The gateway does not open an exit to itself.',
  'Разрешить выход через этот шлюз устройствам': 'Allow these devices to exit through this gateway',
  'Устройств пока нет.': 'No devices yet.',
  'Принудительно весь трафик через шлюз': 'Force all traffic through a gateway',
  '— не форсировать': '— do not force',
  'Весь ИСХОДЯЩИЙ трафик этой ноды пойдёт через шлюз (exit-node, на другие ноды не течёт). Нужен агент на этой ноде — он же сохраняет доступ к ноде по её ПУБЛИЧНОМУ IP: входящие соединения (SSH, сервисы) отвечают напрямую, мимо шлюза.':
    'All of this node’s OUTBOUND traffic will go through the gateway (an exit node, no leak to other nodes). Needs the agent on this node — which also keeps the node reachable on its PUBLIC IP: inbound connections (SSH, services) reply directly, bypassing the gateway.',
  'Нет агента? Показать команду установки': 'No agent? Show the install command',
  'туннель': 'tunnel',
  'Весь трафик этой ноды принудительно через шлюз': 'All of this node’s traffic forced through a gateway',
  'Выход в интернет': 'Internet exit',
  'Весь трафик этой ноды принудительно через шлюз (exit-node)':
    'All of this node’s traffic forced through a gateway (exit node)',
  'весь трафик через {gw} (принудительно)': 'all traffic via {gw} (forced)',
  'через шлюзы: {list}': 'via gateways: {list}',
  'Выход в интернет через': 'Internet exit via',
  'Нет шлюзов выхода. Отметьте «Шлюз выхода в интернет» на нужном сервере.':
    'No exit gateways. Tick “Internet exit gateway” on the server you want.',
  'Ничего не отмечено — выход через шлюз запрещён: в трее Tailscale устройству не видно ни одной exit-ноды, оно ходит в интернет только своим каналом.':
    'Nothing ticked — exit via a gateway is denied: the device sees no exit node in the Tailscale tray and reaches the internet only over its own link.',
  'В трее Tailscale устройству видны и доступны только отмеченные шлюзы. Какой из них включить — выбирает пользователь на клиенте.':
    'In the Tailscale tray the device sees and can use only the ticked gateways. Which one to turn on is up to the user on the client.',
  'Нода не выдаёт доступ сама себе — выдавать нечего. Выберите другой источник или другую цель.':
    'A node does not grant access to itself — there is nothing to grant. Pick a different source or target.',
  'Пары «нода сама на себя» пропущены — внутри себя доступ не выдаётся.':
    'Node-to-itself pairs are skipped — access within a node is not granted.',
  'Работает, только если какая-то нода раздаёт маршрут к этому адресу — тогда правило решает, кому этим маршрутом можно пользоваться. Свой выход ноды в интернет ACL не контролирует: туда она ходит напрямую в любом случае. Нужен маршрут — заведите направление в «Маршрутизации».':
    'This only does anything if some node advertises a route to that address — then the rule decides who may use it. A node’s own way out to the internet is not governed by the ACL: it goes there directly regardless. Need a route? Add a direction under Routing.',
  'Ни одна нода не раздаёт маршрут к этому адресу — правило ничего не изменит.':
    'No node advertises a route to this address — the rule will change nothing.',
  'Убрать доступ': 'Revoke access',
  'Убрать доступ — крестиком на нужном чипе. Выдать сразу нескольким — кнопкой выше; точечно — внутри карточки ноды.':
    'Revoke with the cross on a chip. Grant to several at once with the button above; one by one from a node’s card.',
  'Кому разрешить и куда. Слева можно отметить несколько, справа — один вариант назначения.':
    'Who is allowed, and where. Tick as many as you like on the left; pick one kind of destination on the right.',
  'Отдельные серверы': 'Specific servers',
  'Внешний адрес': 'External address',
  'Админ и так ходит на все серверы. Выдавать ему имеет смысл только внешний адрес (выход в интернет — в «Изменить ноду»).':
    'An admin already reaches every server. The only thing worth granting here is an external address (internet exit is set in “Edit node”).',
  'Все отмеченные — админы, а они и так ходят на любой сервер: эта выдача ничего не изменит. Админу здесь выдают только внешний адрес.':
    'Everyone ticked is an admin, and admins already reach any server: this grant changes nothing. Here you only grant an admin an external address.',
  'Любой — все ноды': 'Anyone — all nodes',
  'Куда': 'Where',
  '1 · Кому': '1 · To whom',
  '2 · Куда': '2 · Where',
  '3 · По порту': '3 · On port',
  '…или отдельные серверы': '…or individual servers',
  '…или внешний адрес': '…or an external address',
  'Внешний адрес: 8.8.8.8, целая сеть 10.0.0.0/8 или сайт — панель сама превратит имя в адрес. Чтобы трафик шёл к нему через конкретную ноду, заведите направление в «Маршрутизации».':
    'An external address: 8.8.8.8, a whole network 10.0.0.0/8, or a site — the panel resolves the name for you. To send traffic there through a particular node, add a direction under Routing.',
  'Отметьте, кому и куда — здесь появится итог': 'Tick who and where — the result appears here',
  'любой': 'anyone',
  'интернет': 'the internet',
  'выбрать ноды…': 'pick nodes…',
  'все устройства': 'all devices',
  'Кто ходит — отметьте ноды:': 'Who goes there — tick the nodes:',
  'отметить все': 'select all',
  'снять все': 'clear',
  '{n} нод': '{n} nodes',
  'сайт (80 и 443)': 'website (80 and 443)',
  'только HTTPS (443)': 'HTTPS only (443)',
  'Чужие релеи': 'Third-party relays',
  'нет — только свой': 'none — ours only',
  'Тянуть чужую карту релеев': 'Fetch third-party relay map',
  'Ноды соединяются напрямую': 'Nodes connecting directly',
  '{n} из {total}': '{n} of {total}',
  'Релей нужен только там, где ноды не смогли соединиться напрямую (NAT). Пока все ходят напрямую, он почти не используется. Чужих релеев нет и автообновление выключено намеренно: иначе подтянулась бы публичная карта Tailscale и трафик пошёл бы через сторонние серверы. Меняется в config.yaml headscale — в панели не даём, это разовая настройка всей сети.':
    'A relay is only used where nodes could not connect directly (NAT). While everything is direct, it barely matters. Third-party relays are absent and auto-update is off on purpose: otherwise Tailscale’s public map would be pulled in and traffic would pass through outside servers. Changed in headscale’s config.yaml — not exposed here, it is a one-time setting for the whole network.',
  'вышла {v}': '{v} released',
  'Подставить эту версию в поле': 'Put this version into the field',
  'Чтобы управлять маршрутами этого сервера из панели, поставьте на него агента. Выполните на СЕРВЕРЕ под root, один раз — иначе панель сможет только показывать маршруты, но не применять их.':
    'To manage this server’s routes from the panel, install the agent on it. Run this ON THE SERVER as root, once — without it the panel can only show routes, not apply them.',
  'Показать команду установки агента': 'Show the agent install command',
  'На ноде «{name}» нет агента — без него она не применит маршрут. Выполните на НЕЙ САМОЙ под root, один раз:':
    'Node “{name}” has no agent — without it the route will not be applied. Run this ON THAT NODE as root, once:',
  'свой порт…': 'custom port…',
  '«Любой порт» — все порты; протокол правило не ограничивает, пройдут и TCP, и UDP.':
    '“Any port” means all ports; the rule does not restrict the protocol, so both TCP and UDP pass.',
  'фильтр по имени или адресу': 'filter by name or address',
  'Под фильтр ничего не подошло.': 'Nothing matches the filter.',
  'Проверить адреса': 'Re-check addresses',
  'Заставляет выбранное устройство ходить на конкретный адрес через конкретную ноду. Остальной трафик устройства идёт напрямую — это не exit-нода, через которую уходит всё.':
    'Makes the chosen device reach a specific address through a specific node. The rest of the device traffic goes direct — this is not an exit node that takes everything.',
  'кто…': 'who…',
  'ноду…': 'node…',
  'домен, IP или подсеть': 'domain, IP or subnet',
  'через': 'via',
  'Направить': 'Route it',
  'Направлений пока нет.': 'No routing directions yet.',
  'Через': 'Via',
  'Статус': 'Status',
  'адрес не проверить': 'address unresolved',
  'нет агента': 'no agent',
  'Без агента нода не применит маршрут — поставьте его в «Маршрутах» этой ноды.':
    'Without the agent the node will not apply the route — install it from that node’s Routes.',
  'работает': 'working',
  'применяется…': 'applying…',
  'Адрес домена панель перепроверяет сама и обновляет маршрут, если сайт переехал.':
    'The panel re-checks the domain itself and updates the route if the site moves.',
  'Важно: на устройстве-источнике должно быть включено принятие маршрутов, иначе направление молча не сработает (панель покажет «работает» — это про сторону ноды-выхода). Новые ноды NodeRoost включают его сами; уже подключённой достаточно один раз выполнить:':
    'Important: the source device must accept routes, otherwise the direction silently does nothing (the panel will still show “works” — that reflects the exit-node side). New NodeRoost nodes enable it themselves; on an already-connected one run once:',
  'Куда (сервера / интернет / IP)': 'Where (servers / internet / IP)',
  '🌐 интернет': '🌐 internet',
  '🌐 Интернет (через exit-node)': '🌐 Internet (via exit node)',
  '…или IP / подсеть / сайт': '…or IP / subnet / site',
  'Сайт резолвится в IP (ACL умеет только IP, не URL). IP может смениться — тогда правило пересоздайте.':
    'A site is resolved to an IP (ACL matches IPs, not URLs). The IP may change — re-create the rule if so.',
  'IP/подсеть — прямой доступ к этим адресам в тайлнете (напр. subnet-маршрут, конкретный хост). Сайт резолвится в IP. Обычный выход в интернет так НЕ настраивается — для него в «Изменить ноду» есть «Выход в интернет через».':
    'IP/subnet — direct access to these addresses over the tailnet (e.g. a subnet route, a specific host). A site is resolved to an IP. Ordinary internet exit is not configured here — for that, use “Internet exit via” in “Edit node”.',
  'IP/подсеть — прямой доступ (subnet-маршрут, хост). Для выхода в интернет — «🌐 Интернет» (+ exit-node на ноде); скоуп по порту да, по сайту — нет.':
    'IP/subnet — direct access (subnet route, host). For internet egress use “🌐 Internet” (+ an exit node on the device); scoping by port yes, by site no.',
  'Не удалось разрешить адрес': 'Could not resolve the address',
  'Любой (кто угодно)': 'Any (anyone)',
  'Любой (куда угодно)': 'Any (anywhere)',
  'По порту': 'On port',
  'порт {p} — обычно {name}': 'port {p} — usually {name}',
  'порт {p} — нестандартный': 'port {p} — non-standard',
  'неверный порт': 'invalid port',
  'поиск…': 'search…',
  'поиск сервера…': 'search server…',

  // --- раздел «Доступы» (обзор + массовая выдача) ---
  'Доступы': 'Access',
  'По кому': 'By principal',
  'По серверам': 'By server',
  'Массовая выдача — кнопкой выше. Точечно — внутри ноды: Ноды → нода → Доступ.':
    'Bulk grant with the button above. Pinpoint edits inside a node: Nodes → node → Access.',
  'Доступов пока нет': 'No access granted yet',
  'Всё запрещено. Нажмите «Выдать доступ», чтобы разрешить первую связь.':
    'Everything is denied. Click “Grant access” to allow the first connection.',
  '+ Выдать доступ': '+ Grant access',
  'Выдать доступ': 'Grant access',
  'Выдать': 'Grant',
  'Кому': 'To whom',
  'Любой (все сервера)': 'Any (all servers)',
  '…уточните поиск': '…refine search',
  'Кому разрешить, по какому порту и на какие сервера. Можно отметить несколько.':
    'Whom to allow, on which port, and to which servers. You can select several.',
  'пользователь': 'user',
  'нода': 'node',
  'все': 'all',
  'всё': 'all',

  // --- настройки (API-ключи / DNS / DERP) ---
  'Настройки': 'Settings',
  'API-ключи headscale': 'headscale API keys',
  'Создать ключ': 'Create key',
  'Ключи доступа к управляющему API headscale (их использует эта панель и сторонние клиенты).':
    'Keys for the headscale management API (used by this panel and third-party clients).',
  'Срок действия': 'Expiry',
  'дн': 'd',
  'лет': 'yrs',
  'Ключ создан — скопируйте сейчас, снова он не покажется:':
    'Key created — copy it now, it will not be shown again:',
  'Скопировать': 'Copy',
  'Ключей нет.': 'No keys.',
  'Префикс': 'Prefix',
  'Создан': 'Created',
  'Истекает': 'Expires',
  'Использован': 'Last used',
  'панель': 'panel',
  'Отозвать': 'Revoke',
  'Отозвать ключ {prefix}…? Приложения с ним потеряют доступ.':
    'Expire key {prefix}…? Apps using it will lose access.',
  'вкл': 'on',
  'выкл': 'off',
  'Базовый домен': 'Base domain',
  'DNS-серверы': 'DNS servers',
  'Search-домены': 'Search domains',
  'DNS-серверы (через запятую)': 'DNS servers (comma-separated)',
  'Применить': 'Apply',
  'Применение…': 'Applying…',
  'Сохранено. headscale перезапускается…': 'Saved. headscale is restarting…',
  'Применить изменения DNS? headscale перезапустится (~10–15 c), на это время регистрация нод приостановится. Смена базового домена меняет MagicDNS-имена всех нод.':
    'Apply DNS changes? headscale will restart (~10–15 s), pausing node registration meanwhile. Changing the base domain changes the MagicDNS names of all nodes.',
  'Применяется с перезапуском headscale (~10–15 c). Смена базового домена меняет MagicDNS-имена всех нод.':
    'Applied by restarting headscale (~10–15 s). Changing the base domain changes the MagicDNS names of all nodes.',
  'Имена внутри сети': 'Names inside the network',
  'имя': 'name',
  'на какой ноде': 'on which node',
  'адрес в сети': 'address on the network',
  'ведёт наружу': 'leads outside',
  'Резолверы и MagicDNS': 'Resolvers and MagicDNS',
  'Короткие имена самих нод (MagicDNS) и то, какими DNS-серверами ноды пользуются. Настраивается один раз при разворачивании сети.':
    'The short names of the nodes themselves (MagicDNS) and which DNS servers the nodes use. Set once, when the network is stood up.',
  'Имён пока нет. Обычный случай: панель или админка живёт на своём имени и закрыта снаружи — добавьте это имя и укажите ноду, на которой она стоит. Второй случай: имя для того, что стоит за нодой и куда клиента не поставить (NAS, IPMI, камера) — тогда вместо ноды укажите адрес вручную.':
    'No names yet. The usual case: a panel or an admin page lives on a name of its own and is closed from outside — add that name and point it at the node it runs on. The other case: a name for something that sits behind a node and takes no client of its own (a NAS, an IPMI board, a camera) — then give an address by hand instead of a node.',
  'пока не настроено': 'not set up yet',
  'записано, ждёт перезапуска headscale': 'written, waiting for headscale to restart',
  'раздаётся нодам': 'handed out to nodes',
  'Имя ведёт на адрес в сети — но только для машин сети. Публичный DNS панель не трогает: снаружи имя ведёт туда же, куда вело, и кто не в сети — заходит как раньше. Так к сервису, закрытому вайтлистом, ходят по внутреннему адресу.':
    'A name points at an address on the network — but only for machines on it. Public DNS is left alone: from outside the name leads where it always did, and whoever is not on the network gets in as before. That is how you reach a service closed behind an address allowlist by its internal address.',
  'Галочка слева переключает имя между «внутрь сети» и «как снаружи» — снятая оставляет запись в списке, но нодам её не раздаёт. Переключается для всей сети сразу: адресно, по машинам, headscale раздавать имена не умеет. Имя получают и те машины, которым доступ к этому серверу не открыт: у них оно перестанет открываться совсем, наружу за ним они больше не пойдут. Правки применяются без перезапуска headscale; исключение — самое первое имя.':
    'The tick on the left switches a name between “inside the network” and “as from outside” — unticked, the record stays in the list but is not handed to the nodes. It switches for the whole network at once: headscale cannot hand names out machine by machine. Machines with no access to that server get the name too: for them it stops opening at all, as they will no longer go out to the internet for it. Edits apply without restarting headscale; the very first name is the exception.',
  'Вести внутрь сети': 'Point inside the network',
  'Первое имя нужно один раз показать headscale: он перезапустится (~10–15 c), на это время регистрация нод приостановится. Дальнейшие правки применяются без перезапуска.':
    'The first name has to be shown to headscale once: it will restart (~10–15 s), pausing node registration meanwhile. Later edits apply without a restart.',
  'Сохранено — имена уже раздаются нодам': 'Saved — the names are already going out to the nodes',
  'адрес вручную': 'address by hand',
  'На ноде агент от прошлого релиза — новых возможностей панели он не понимает. Обновление ставится только с подписью: нода проверит её ключом, вшитым при установке, и чужой скрипт не примет.':
    'The node runs an agent from an earlier release — it does not understand the panel’s newer abilities. An update installs only when signed: the node checks it against the key baked in at install time and refuses anything else.',
  'Обновить агента': 'Update the agent',
  'Отправляем…': 'Sending…',
  '…или командой на ноде': '…or by command on the node',
  'Заказано — нода проверит подпись и обновится в течение минуты':
    'Asked — the node will check the signature and update within a minute',
  'Корневой сертификат': 'Root certificate',
  'Действует до': 'Valid until',
  'Отпечаток SHA-256': 'SHA-256 fingerprint',
  'Настроить': 'Configure',
  'Свернуть': 'Collapse',
  'Домены': 'Domains',
  'любые (ограничений нет)': 'any (no constraints)',
  'Домены (через запятую)': 'Domains (comma-separated)',
  'Срок, лет': 'Valid for, years',
  'Корень подписывает имена только в этих доменах — этим ограничена и власть панели: на чужой домен сертификат она не выпишет. Сохранение выпускает корень заново: сертификаты имён панель закажет сама, а на ноутбуках и телефонах корень надо будет поставить заново.':
    'The root signs names in these domains only — which is also the limit of the panel’s power: it cannot issue a certificate for someone else’s domain. Saving issues a new root: the panel reorders the names’ certificates itself, but on laptops and phones the root has to be installed again.',
  'Выпустить корень заново? Старый перестанет действовать.':
    'Issue a new root? The old one stops working.',
  'Выпустить заново': 'Issue a new root',
  'Как поставить на устройство': 'How to install it on a device',
  'Ноды панель обслуживает сама. Вручную — только то, что подключали не скриптом: Windows — «Доверенные корневые центры сертификации» (Локальный компьютер), macOS — Связка ключей → «Система» → «Всегда доверять», Linux — /usr/local/share/ca-certificates + update-ca-certificates, Android и iOS — установить профиль и включить полное доверие. После установки сверьте отпечаток.':
    'Nodes are handled by the panel itself. By hand you only do the machines you joined without the script: Windows — “Trusted Root Certification Authorities” (Local Machine), macOS — Keychain → “System” → “Always Trust”, Linux — /usr/local/share/ca-certificates then update-ca-certificates, Android and iOS — install the profile and switch on full trust. Check the fingerprint afterwards.',
  'сертификат': 'certificate',
  'Выпустить сертификат для этого имени': 'Issue a certificate for this name',
  'Сертификат подписывает панель своим корнем, а ключ генерится на самой ноде и никуда с неё не уезжает. Продление идёт само за месяц до конца; файлы лежат на ноде в /etc/noderoost/certs, и после смены агент запускает /lib65/noderoost-agent/cert-hook.sh, если вы его туда положили.':
    'The panel signs the certificate with its own root; the key is generated on the node and never leaves it. Renewal happens on its own a month before the end; the files land on the node in /etc/noderoost/certs, and after a change the agent runs /lib65/noderoost-agent/cert-hook.sh if you put one there.',
  'ставить корень на ноды автоматически': 'install the root on nodes automatically',
  'Сертификат возможен только для имени, ведущего на ноду':
    'A certificate is only possible for a name that points at a node',
  'сертификат до {date}': 'certificate until {date}',
  'сертификат не выдан: {err}': 'certificate not issued: {err}',
  'сертификат заказан — нода заберёт его в течение минуты':
    'certificate ordered — the node will pick it up within a minute',
  'СТАРЫЙ КЛЮЧ': 
    'The panel orders the certificate; the key is generated on the node itself and never leaves it. For this to work you need one DNS record, once and for all: your internal-name mask (say *.int.example.com) as an A record pointing at the panel, plus NODEROOST_CERT_DOMAIN in its .env. Renewal happens on its own a month before the end; the files land on the node in /etc/noderoost/certs, and after a change the agent runs /lib65/noderoost-agent/cert-hook.sh if you put one there.',
  'нода не найдена': 'node not found',
  'Добавить имя': 'Add a name',
  'Сеть меша (IP-диапазон)': 'Mesh network (IP range)',
  'получится: {n} адресов ({from} – {to})': 'result: {n} addresses ({from} – {to})',
  'Примеры:': 'Examples:',

  // --- переподключение ноды ---
  'Переподключить': 'Reconnect',
  'Переподключить «{name}»': 'Reconnect “{name}”',
  'Переподключить «{name}»? Её текущая запись будет удалена, и после запуска скрипта на ноде она подключится заново с новым IP.':
    'Reconnect “{name}”? Its current record is deleted; after running the script on the node it reconnects with a new IP.',
  'Нода будет удалена из headscale и получит новый IP из текущего диапазона при повторном подключении. Имя и владелец сохранятся.':
    'The node is removed from headscale and gets a new IP from the current range on reconnect. Its name and owner are kept.',
  'Готовлю…': 'Preparing…',
  'Переподключена ✓ — новый IP: {ip}': 'Reconnected ✓ — new IP: {ip}',
  'Ждём переподключения ноды — статус обновится сам…': 'Waiting for the node to reconnect — status updates automatically…',

  // --- версия клиента Tailscale ---
  'Версия клиента Tailscale': 'Tailscale client version',
  'Свою пиновую версию ставят все enroll-скрипты — чтобы обновление официального клиента не сломало подключение. Обновляйте вручную, проверив совместимость.':
    'All enroll scripts install your pinned version, so an official client update won’t break enrollment. Update it manually after checking compatibility.',
  'Текущая:': 'Current:',
  'Из официальной': 'From official',
  'Последняя официальная: {v}': 'Latest official: {v}',
  'Не удалось получить версию': 'Could not fetch the version',
  'Версия {v} доступна для скачивания ✓': 'Version {v} is downloadable ✓',
  'Версия недоступна': 'Version not available',
  'Версия недоступна — сначала проверьте': 'Version not available — check it first',
  'Сохранено. Новые ноды будут ставить {v}.': 'Saved. New nodes will install {v}.',
  '…': '…',
  'Локальный мирор бинарей': 'Local binary mirror',
  'Загрузить в мирор': 'Download to mirror',
  'Загрузка…': 'Downloading…',
  'Enroll-скрипты качают клиент с нашего сервера (hs-домен /pkgs), фолбэк — официальный pkgs.tailscale.com. Скачайте текущую версию в мирор, чтобы не зависеть от tailscale.com.':
    'Enroll scripts fetch the client from our server (hs domain /pkgs), falling back to the official pkgs.tailscale.com. Download the current version into the mirror to avoid depending on tailscale.com.',
  'Мирор обновлён: {n} файлов': 'Mirror updated: {n} files',
  'Не скачались: {n}': 'Failed to download: {n}',
  'нет': 'missing',
  'Доступ к control-серверу (вайтлист IP)': 'Control-server access (IP allow-list)',
  'Меш закрыт: 443 headscale открыт только этим IP. Панель добавляет их при подключении ноды и сама убирает орфанов (нода не подключилась или удалена). Внешние записи — только вручную.':
    'The mesh is closed: headscale 443 is open only to these IPs. The panel adds them when a node is enrolled and auto-removes orphans (node never connected or was deleted). External entries are removed manually only.',
  'Список пуст.': 'The list is empty.',
  'нода: {n}': 'node: {n}',
  'ожидает подключения': 'awaiting connection',
  'орфан — уберётся автоматически': 'orphan — auto-removed',
  'внешний (вручную)': 'external (manual)',
  'Убрать из вайтлиста': 'Remove from allow-list',
  'Убрать {ip} из вайтлиста? Фаервол закроет для него 443 в течение ~минуты.':
    'Remove {ip} from the allow-list? The firewall will close 443 for it within ~a minute.',
  'IPv4-диапазон (внутри 100.64.0.0/10)': 'IPv4 range (within 100.64.0.0/10)',
  'Распределение адресов': 'Address allocation',
  'последовательно (.1, .2, .3…)': 'sequential (.1, .2, .3…)',
  'случайно': 'random',
  'Сменить диапазон меша? headscale перезапустится (~10–15 c). Существующие ноды сохранят старые IP — новый диапазон только для новых нод. Меняйте это на пустой/новой сети.':
    'Change the mesh range? headscale will restart (~10–15 s). Existing nodes keep their old IPs — the new range is for new nodes only. Best done on an empty/new network.',
  'IPv4 — только внутри 100.64.0.0/10 (Tailscale CGNAT). Существующие ноды сохранят старые IP; смена применяется с перезапуском headscale.':
    'IPv4 only within 100.64.0.0/10 (Tailscale CGNAT). Existing nodes keep old IPs; applied by restarting headscale.',
  'Настраивается в config.yaml headscale (API для правки нет).':
    'Configured in headscale config.yaml (no edit API).',
  'Встроенный DERP': 'Embedded DERP',
  'Карта релеев': 'Relay map',
  'Автообновление': 'Auto-update',

  // --- метрики + алерты ---
  'Алерты': 'Alerts',
  'Нод онлайн (24 ч)': 'Nodes online (24h)',
  'пока мало данных для графика': 'not enough data for the chart yet',
  'Панель следит за нодами и шлёт уведомление, когда нода уходит в офлайн или возвращается, а также когда ключ ноды скоро истекает.':
    'The panel watches nodes and notifies you when a node goes offline or comes back, and when a node key is about to expire.',
  'Токен бота': 'Bot token',
  'Адрес Bot API': 'Bot API address',
  'Пусто = api.telegram.org. Укажите зеркало/прокси, если Telegram заблокирован в регионе (напр. https://api-tg.example.com).':
    'Empty = api.telegram.org. Set a mirror/proxy if Telegram is blocked in the region (e.g. https://api-tg.example.com).',
  'Вебхук': 'Webhook',
  'POST с JSON {"text": "…"} на URL (Slack, Mattermost, свой сервис).':
    'POST with JSON {"text": "…"} to a URL (Slack, Mattermost, your own service).',
  'Сохранено.': 'Saved.',
  'Тестовый алерт отправлен — проверьте канал.':
    'Test alert sent — check the channel.',
  'Не отправлено: {err}': 'Not sent: {err}',
  'Отправка…': 'Sending…',
  'Проверить': 'Test',

  // --- бэкапы ---
  'Бэкапы': 'Backups',
  'В бэкап входит снимок состояния headscale (база + config + ключи: ноды, пользователи, ключи, ACL) и настройки панели. Метрики/история — нет.':
    'A backup includes a snapshot of headscale state (DB + config + keys: nodes, users, keys, ACL) and panel settings. Metrics/history are not included.',
  'Создать бэкап сейчас': 'Back up now',
  'Бэкап создан ({size}).': 'Backup created ({size}).',
  'Бэкап создан, но self-тест: {p}': 'Backup created, but self-test: {p}',
  'Автобэкап': 'Auto backup',
  'Каждые': 'Every',
  'выключено': 'disabled',
  'ч': 'h',
  'Хранить копий': 'Keep copies',
  'Копии на сервере': 'Copies on the server',
  'Копий пока нет.': 'No copies yet.',
  'Файл': 'File',
  'Размер': 'Size',
  'Дата': 'Date',
  'Скачать': 'Download',
  'Восстановление headscale — вручную: распакуйте архив в data/headscale и перезапустите стек (docker compose up -d).':
    'Restoring headscale is manual: extract the archive into data/headscale and restart the stack (docker compose up -d).',

  // --- пользователи + pre-auth-ключи ---
  '+ Добавить пользователя': '+ Add user',
  'Имя пользователя': 'Username',
  'Пользователей нет': 'No users',
  'Пользователи — владельцы нод тайлнета. Добавьте первого и выдайте ему ключ подключения.':
    'Users own the tailnet nodes. Add the first one and issue a connection key.',
  'создан': 'created',
  'Ещё': 'More',
  'Ключи подключения': 'Connection keys',
  'Переименовать': 'Rename',
  'Новое имя пользователя': 'New username',
  'Удалить пользователя «{name}»?': 'Delete user “{name}”?',
  'У него {n} нод — удаление может их затронуть.':
    'It owns {n} nodes — deleting may affect them.',
  'Ключи подключения · {name}': 'Connection keys · {name}',
  'Pre-auth-ключи для подключения нод под этим пользователем (tailscale up --authkey). Многоразовый — можно подключить несколько нод.':
    'Pre-auth keys to connect nodes as this user (tailscale up --authkey). Reusable lets you connect several nodes.',
  'Многоразовый': 'Reusable',
  'Эфемерный (нода исчезнет при отключении)':
    'Ephemeral (node disappears on disconnect)',
  'Теги (необязательно)': 'Tags (optional)',
  'Ключ создан. Команда для подключения ноды:':
    'Key created. Node connection command:',
  'Скопировать команду': 'Copy command',
  'Существующие ключи': 'Existing keys',
  'многоразовый': 'reusable',
  'эфемерный': 'ephemeral',
  'использован': 'used',
  'Истечь этот ключ? Он перестанет работать.':
    'Expire this key? It will stop working.',

  // --- 2FA ---
  'Двухфакторная аутентификация': 'Two-factor authentication',
  'Неверный код': 'Invalid code',
  '2FA включена. Вход требует код из приложения.':
    '2FA is on. Login requires a code from the app.',
  'Чтобы отключить, введите текущий код из приложения-аутентификатора.':
    'To turn it off, enter the current code from your authenticator app.',
  'Отключить 2FA': 'Disable 2FA',
  'Отсканируйте QR в приложении-аутентификаторе (Google Authenticator, Aegis, 1Password) или введите ключ вручную, затем подтвердите кодом.':
    'Scan the QR in an authenticator app (Google Authenticator, Aegis, 1Password) or enter the key manually, then confirm with a code.',
  'Ключ:': 'Key:',
  'Включить': 'Enable',
  'генерация QR…': 'generating QR…',
  'Добавьте второй фактор к входу в панель — одноразовый код из приложения-аутентификатора (TOTP).':
    'Add a second factor to panel login — a one-time code from an authenticator app (TOTP).',
  'Включить 2FA': 'Enable 2FA',

  // --- смена пароля ---
  'Текущий пароль': 'Current password',
  'Новый пароль (мин. 8 символов)': 'New password (min. 8 characters)',
  'Повторите новый пароль': 'Repeat new password',
  'Пароль изменён. Все прежние сессии завершены.':
    'Password changed. All previous sessions have been ended.',
  'Смена пароля завершит все другие активные сессии.':
    'Changing the password ends all other active sessions.',
  'Пароль должен быть не короче 8 символов':
    'Password must be at least 8 characters',
  'Пароли не совпадают': 'Passwords do not match',

  // --- общее ---
  'Закрыть': 'Close',
  'Отмена': 'Cancel',
  'Готово': 'Done',
  'Ошибка': 'Error',
  'неизвестно': 'unknown',
}

type Ctx = {
  lang: Lang
  setLang: (l: Lang) => void
  t: (s: string, params?: Record<string, string | number>) => string
}

const I18nContext = createContext<Ctx>({
  lang: 'ru',
  setLang: () => {},
  t: (s) => s,
})

function fill(s: string, params?: Record<string, string | number>): string {
  if (!params) return s
  return s.replace(/\{(\w+)\}/g, (_, k) =>
    k in params ? String(params[k]) : `{${k}}`,
  )
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(
    () => (localStorage.getItem('noderoost_lang') as Lang) || 'ru',
  )
  const setLang = useCallback((l: Lang) => {
    localStorage.setItem('noderoost_lang', l)
    setLangState(l)
  }, [])
  const t = useCallback(
    (s: string, params?: Record<string, string | number>) =>
      fill(lang === 'en' ? EN[s] ?? s : s, params),
    [lang],
  )
  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useI18n = () => useContext(I18nContext)
