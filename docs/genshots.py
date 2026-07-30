# Скрины для README: статические моки на РЕАЛЬНОМ собранном CSS панели.
#
# Почему не с живого прода: в кадр попали бы настоящие домены и адреса чужих
# проектов. Моки берут тот же CSS, что уходит в образ (frontend/dist/assets/*.css),
# поэтому расходиться со стилями панели они не могут — а адреса в них из RFC 5737
# (203.0.113.x), то есть заведомо документационные.
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"Z:\ms\NodeRoost")
OUT = ROOT / "docs" / "screenshots"
TMP = Path(os.environ.get("TEMP", "/tmp")) / "nr-shots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CSS = sorted(glob.glob(str(ROOT / "frontend" / "dist" / "assets" / "*.css")))[-1]
LOGO = (ROOT / "landing" / "site" / "logo.svg")   # горизонтальный локап на тёмном
FLAGS = ROOT / "frontend" / "public" / "flags"   # кладёт prebuild из flag-icons

SHELL = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<link rel="stylesheet" href="file:///{css}">
<style>
  /* анимации выключены: иначе кадр ловится на opacity:0 у модалок и карточек */
  *,*::before,*::after{{animation:none!important;transition:none!important}}
  body{{margin:0}}
  /* margin:auto — кадр шире .shot на полосу прокрутки, без этого
     содержимое уезжает влево; поля сверху и снизу держим равными */
  .shot{{width:{width}px;padding:40px 0 0;margin:0 auto}}
</style></head><body class="theme-dark"><div class="shot"><div class="page">{body}</div></div></body></html>"""

HEADER = """
<div class="header">
  <button class="brand brand-h"><span class="brand-img"><img src="file:///{logo}" style="height:34px;display:block"></span></button>
  <nav class="topnav">
    <button class="navlink{t1}">{n1}</button><button class="navlink{t2}">{n2}</button>
    <button class="navlink{t3}">{n3}</button><button class="navlink{t4}">{n4}</button>
  </nav>
  <div class="header-right">
    <span class="hs-state"><span class="dot dot-ok"></span>headscale</span>
    <span class="muted small">v0.1.0</span>
  </div>
</div>"""


def header(active, labels):
    cls = ["", "", "", ""]
    cls[active] = "nav-active"
    return HEADER.format(
        **{f"t{i+1}": (" navlink-active" if i == active else "") for i in range(4)},
        **{f"n{i+1}": labels[i] for i in range(4)},
        logo=str(LOGO).replace("\\", "/"),
    )


def flag(cc):
    """Флаг страны — как в панели: страна там берётся по внешнему IP (app/geoip.py)."""
    if not cc:
        return ""
    src = str(FLAGS / f"{cc.lower()}.svg").replace("\\", "/")
    return f'<img class="country-flag" src="file:///{src}" alt="{cc.upper()}">'


def node_card(name, ip, os_name, badges="", tags="", desc="", lang="ru", cc=""):
    b = "".join(badges)
    online = "онлайн" if lang == "ru" else "online"
    t = tags or (
        '<span class="muted small">нет тегов</span>' if lang == "ru"
        else '<span class="muted small">no tags</span>')
    d = f'<div class="node-desc">{desc}</div>' if desc else ""
    return f"""
    <div class="card node-card node-clickable">
      <div class="node-main">
        <span class="nr-grip">⠿</span><span class="dot dot-ok"></span>
        <div class="node-info">
          <div class="node-title">{flag(cc)}<span class="node-name">{name}</span>{b}</div>{d}
          <div class="node-meta"><span class="chip">{ip}</span>
            <span class="muted small">{online} · {os_name}</span>{t}</div>
        </div>
      </div>
      <div class="node-actions"><button class="ghost icon-btn"><span class="menu-gear">⋯</span></button></div>
    </div>"""


def group(title, count, cards, sub=None):
    s = f'<div class="node-subgroup-head">{sub}</div>' if sub else ""
    return f"""
    <div class="node-group">
      <button class="node-group-head"><span class="node-group-caret">▾</span>
        <span class="node-group-name">{title}</span><span class="muted small">{count}</span></button>
      {s}<div class="node-list">{cards}</div>
    </div>"""


def servers_page(lang):
    L = dict(
        ru=dict(nav=["Серверы", "Устройства", "Доступы", "Маршрутизация"],
                h="Серверы", refresh="Обновить", add="+ Добавить сервер",
                tiles=[("6", "Всего"), ("6", "Онлайн"), ("0", "Оффлайн")],
                search="Поиск: имя, IP, тег…", gw="шлюз", ex="выход через 2"),
        en=dict(nav=["Servers", "Devices", "Access", "Routing"],
                h="Servers", refresh="Refresh", add="+ Add server",
                tiles=[("6", "Total"), ("6", "Online"), ("0", "Offline")],
                search="Search: name, IP, tag…", gw="gateway", ex="exit via 2"),
    )[lang]
    tiles = "".join(
        f'<button class="stat-card stat-tile-btn{" stat-tile-active" if i==0 else ""}">'
        f'<div class="stat-value">{v}</div><div class="stat-label">{n}</div></button>'
        for i, (v, n) in enumerate(L["tiles"])
    )
    g1 = group("Acme", 3,
        node_card("edge-fra-1", "203.0.113.11", "Debian 12",
                  ['<span class="pill-ok">' + L["gw"] + "</span>"],
                  '<span class="tag-chip">tag:web</span>', lang=lang, cc="de"),
        sub="prod")
    g2 = group("Acme · billing", 2,
        node_card("db-fra-1", "203.0.113.12", "Ubuntu 24.04",
                  [], '<span class="tag-chip">tag:db</span>', lang=lang, cc="de")
        + node_card("api-fra-2", "203.0.113.13", "Ubuntu 24.04",
                    ['<span class="pill-warn route-pending pill-action">маршруты ожидают</span>'
                     if lang == "ru" else
                     '<span class="pill-warn route-pending pill-action">routes pending</span>'],
                    lang=lang, cc="fi"))
    return f"""
    {header(0, L['nav'])}
    <div class="page-head"><h2>{L['h']}</h2>
      <div class="page-head-actions"><button class="ghost">{L['refresh']}</button>
      <button>{L['add']}</button></div></div>
    <div class="stat-cards nodes-tiles">{tiles}</div>
    <div class="nodes-toolbar"><input class="search-box" value="" placeholder="{L['search']}"></div>
    {g1}{g2}"""


def access_page(lang):
    L = dict(
        ru=dict(nav=["Серверы", "Устройства", "Доступы", "Маршрутизация"], h="Доступы",
                refresh="Обновить", add="+ Выдать доступ", by=["По кому", "По серверам"],
                hint="Убрать доступ — крестиком на нужном чипе. Выдать сразу нескольким — кнопкой выше; точечно — внутри карточки ноды.",
                kind="нода", ports=["SSH (22)", "HTTPS (443)", "PostgreSQL (5432)"]),
        en=dict(nav=["Servers", "Devices", "Access", "Routing"], h="Access",
                refresh="Refresh", add="+ Grant access", by=["By who", "By server"],
                hint="Revoke with the × on a chip. Grant to several at once with the button above; one-off — inside a node's card.",
                kind="node", ports=["SSH (22)", "HTTPS (443)", "PostgreSQL (5432)"]),
    )[lang]

    def card(name, rows):
        r = "".join(
            f'<div class="grant-row"><span class="port-badge">{p}</span>'
            f'<span class="grant-arrow">→</span><span class="ent-chips">'
            + "".join(f'<span class="ent-chip">{t}<button class="chip-x">×</button></span>' for t in tg)
            + "</span></div>"
            for p, tg in rows)
        return (f'<div class="card grant-card"><div class="grant-head">'
                f'<span class="ent-dot ent-node"></span><span class="grant-name">{name}</span>'
                f'<span class="muted small">{L["kind"]}</span></div>{r}</div>')

    cards = (card("laptop-anna", [(L["ports"][0], ["edge-fra-1"]), (L["ports"][1], ["#web", "api-fra-2"])])
             + card("laptop-boris", [(L["ports"][1], ["#web"])])
             + card("ci-runner", [(L["ports"][2], ["db-fra-1"]), (L["ports"][0], ["api-fra-2"])])
             + card("phone-anna", [(L["ports"][1], ["#web"])]))
    return f"""
    {header(2, L['nav'])}
    <div class="page-head"><h2>{L['h']}</h2>
      <div class="page-head-actions"><button class="ghost">{L['refresh']}</button>
      <button>{L['add']}</button></div></div>
    <div class="grid-toggle access-lens"><button class="seg-active">{L['by'][0]}</button>
      <button>{L['by'][1]}</button></div>
    <p class="muted small access-hint">{L['hint']}</p>
    <div class="grant-list">{cards}</div>"""


def routing_page(lang):
    L = dict(
        ru=dict(nav=["Серверы", "Устройства", "Доступы", "Маршрутизация"], h="Маршрутизация",
                recheck="Проверить адреса",
                lead="Заставляет выбранное устройство ходить на конкретный адрес через конкретную ноду. Остальной трафик устройства идёт напрямую — это не exit-нода, через которую уходит всё.",
                s1="1 · Кто ходит", s2="2 · Куда ходят", s3="3 · Через какую ноду",
                seg=["На конкретный адрес", "Весь трафик (полный туннель)"],
                dst="домен, IP или подсеть", port="порт", anyport="любой порт",
                pick="выберите сервер…", sum="Отметьте кто, куда и через какую ноду — здесь появится итог",
                go="Направить", cols=["Куда", "Через", "Порт", "Статус"],
                works="работает", rm="Убрать", who=["laptop-anna", "phone-anna", "ci-runner", "Все устройства"]),
        en=dict(nav=["Servers", "Devices", "Access", "Routing"], h="Routing",
                recheck="Re-check addresses",
                lead="Makes the chosen device reach a specific address through a specific node. The rest of its traffic stays direct — this is not an exit node that takes everything.",
                s1="1 · Who goes", s2="2 · Where to", s3="3 · Through which node",
                seg=["To a specific address", "All traffic (full tunnel)"],
                dst="domain, IP or subnet", port="port", anyport="any port",
                pick="pick a server…", sum="Tick who, where and through which node — the result appears here",
                go="Route it", cols=["Where", "Through", "Port", "Status"],
                works="works", rm="Remove", who=["laptop-anna", "phone-anna", "ci-runner", "All devices"]),
    )[lang]
    rows = "".join(
        f'<tr><td><span class="mono">{d}</span>'
        f'<span class="muted small dir-ips"> → {ip}</span></td>'
        f'<td>edge-fra-1</td><td>{L["anyport"]}</td>'
        f'<td><span class="tag-chip">{L["works"]}</span></td>'
        f'<td class="row-actions"><button class="ghost small">{L["rm"]}</button></td></tr>'
        for d, ip in [("api.partner.example", "203.0.113.44"), ("stats.example", "203.0.113.51")])
    picks = "".join(
        f'<label class="pick-row"><input type="checkbox"{" checked" if i == 0 else ""}>'
        f'<span class="ent-dot ent-{"any" if i > 2 else "node"}"></span>'
        f'<span class="pick-label">{w}</span>'
        f'<span class="muted small">{"устройство" if lang == "ru" else "device"}</span></label>'
        for i, w in enumerate(L["who"]))
    return f"""
    {header(3, L['nav'])}
    <section class="card">
      <div class="clients-head"><h3>{L['h']}</h3>
        <button class="ghost small">{L['recheck']}</button></div>
      <p class="muted small">{L['lead']}</p>
      <div class="dir-builder">
        <div class="dir-step dir-who">
          <div class="dir-step-head"><span class="dir-step-label">{L['s1']}</span>
            <button class="ghost small">{'снять все' if lang=='ru' else 'clear all'}</button></div>
          <input class="search-box" placeholder="{'поиск ноды…' if lang=='ru' else 'search a node…'}">
          <div class="pick-list dir-pick-list">{picks}</div>
        </div>
        <div class="dir-step dir-what">
          <span class="dir-step-label">{L['s2']}</span>
          <div class="dir-seg"><button class="dir-seg-btn dir-seg-on">{L['seg'][0]}</button>
            <button class="dir-seg-btn">{L['seg'][1]}</button></div>
          <div class="dir-mode-body">
            <input value="api.partner.example" placeholder="{L['dst']}">
            <div class="dir-port-row"><span class="muted small">{L['port']}</span>
              <select class="select"><option>{L['anyport']}</option></select></div>
          </div>
          <span class="dir-step-label">{L['s3']}</span>
          <select class="select dir-via"><option>edge-fra-1</option></select>
          <p class="dir-summary">laptop-anna → api.partner.example : {L['anyport']} {'через' if lang=='ru' else 'through'} edge-fra-1</p>
          <button class="dir-go">{L['go']}</button>
        </div>
      </div>
      <div class="dir-groups"><div class="dir-group">
        <button class="node-group-head"><span class="node-group-caret">▾</span>
          <span class="node-group-name">laptop-anna</span><span class="muted small">2</span></button>
        <table class="keys-table dir-table"><thead><tr>
          <th>{L['cols'][0]}</th><th>{L['cols'][1]}</th><th>{L['cols'][2]}</th><th>{L['cols'][3]}</th><th></th>
        </tr></thead><tbody>{rows}</tbody></table>
      </div></div>
    </section>"""


def node_page(lang):
    L = dict(
        ru=dict(nav=["Серверы", "Устройства", "Доступы", "Маршрутизация"],
                back="← Ноды", edit="Изменить", spec=["IP", "Система", "Клиент Tailscale", "Виден с адреса", "Добавлена", "Срок ключа"],
                vals=["203.0.113.11", "Debian 12", "1.98.9", "203.0.113.200:41641", "12.06.2026 10:14", "не истекает"],
                acc="Доступ", inb="Кто может подключаться сюда", outb="Куда может ходить эта нода",
                allow="+ Разрешить", exitlbl="Выход в интернет", gw="Шлюз выхода в интернет"),
        en=dict(nav=["Servers", "Devices", "Access", "Routing"],
                back="← Nodes", edit="Edit", spec=["IP", "System", "Tailscale client", "Seen from", "Added", "Key expiry"],
                vals=["203.0.113.11", "Debian 12", "1.98.9", "203.0.113.200:41641", "12 Jun 2026 10:14", "never"],
                acc="Access", inb="Who may connect here", outb="Where this node may go",
                allow="+ Allow", exitlbl="Internet exit", gw="Internet exit gateway"),
    )[lang]
    cells = "".join(
        f'<div class="spec-cell"><span class="spec-label">{n}</span>'
        f'<span class="spec-value">{v}</span></div>'
        for n, v in zip(L["spec"], L["vals"]))
    rows_in = "".join(
        f'<div class="grant-row"><span class="port-badge">{p}</span>'
        f'<span class="grant-arrow">←</span><span class="ent-chips">'
        f'<span class="ent-chip">{w}<button class="chip-x">×</button></span></span></div>'
        for p, w in [("SSH (22)", "laptop-anna"), ("HTTPS (443)", "#web")])
    rows_out = (f'<div class="grant-row"><span class="port-badge">PostgreSQL (5432)</span>'
                f'<span class="grant-arrow">→</span><span class="ent-chips">'
                f'<span class="ent-chip">db-fra-1<button class="chip-x">×</button></span></span></div>')
    return f"""
    {header(0, L['nav'])}
    <button class="linklike detail-back">{L['back']}</button>
    <div class="page-head"><h2 class="detail-title"><span class="dot dot-ok"></span>{flag("de")}edge-fra-1
      <span class="pill-ok">exit</span></h2>
      <div class="page-head-actions"><button class="ghost">{L['edit']}</button>
      <button class="ghost icon-btn"><span class="menu-gear">⋯</span></button></div></div>
    <div class="card detail-info"><div class="spec-grid">
      <div class="spec-cell spec-wide"><span class="spec-label">{L['exitlbl']}</span>
        <span class="spec-value"><span class="pill-ok">{L['gw']}</span></span></div>
      {cells}</div></div>
    <div class="card"><h3>{L['acc']}</h3>
      <div class="access-cols">
        <div class="access-block"><div class="access-block-head"><h4>{L['inb']}</h4>
          <button class="ghost small">{L['allow']}</button></div>{rows_in}</div>
        <div class="access-block"><div class="access-block-head"><h4>{L['outb']}</h4>
          <button class="ghost small">{L['allow']}</button></div>{rows_out}</div>
      </div></div>"""


ASSETS = ROOT / "frontend" / "src" / "assets"


def login_page(lang):
    """Экран входа. Разметка повторяет LoginPage.tsx, знак и вордмарк — из тех же
    файлов, что собирает BrandLockup (готовый локап-SVG не берём: в нём зашит
    свой отступ между знаком и надписью)."""
    L = dict(
        ru=dict(title="Вход в панель", user="Логин", pw="Пароль",
                otp="Код из приложения (2FA)", submit="Войти"),
        en=dict(title="Sign in", user="Login", pw="Password",
                otp="Code from the app (2FA)", submit="Log in"),
    )[lang]
    mark = str(ASSETS / "noderoost-mark-on-dark.svg").replace("\\", "/")
    word = str(ASSETS / "noderoost-wordmark-on-dark.svg").replace("\\", "/")
    return f"""
      <style>.login-wrap{{padding-top:0}}</style>
      <div class="login-wrap">
        <form class="card login-card">
          <span class="brand-img brand-v login-logo">
            <span class="brand-line"><img src="file:///{mark}" style="height:96px;display:block"></span>
            <span class="brand-line"><img src="file:///{word}" style="height:26px;display:block;margin-top:14px"></span>
          </span>
          <h2>{L['title']}</h2>
          <label>{L['user']}<input value="admin"></label>
          <label>{L['pw']}<input type="password" value="Ei8shaeR1quohGh"></label>
          <label>{L['otp']}<input placeholder="123456" style="border-color:#00E58A"></label>
          <button type="submit">{L['submit']}</button>
        </form>
      </div>"""


PAGES = {"servers": servers_page, "access": access_page,
         "routing": routing_page, "node": node_page, "login": login_page}
# экран входа — узкая карточка, широкий кадр оставил бы её в пустом поле
WIDTHS = {"login": 460}
TIGHT = {"login"}
# высота кадра: у экрана входа контента на пол-экрана
HEIGHTS = {"login": 700}


def render(lang):
    outdir = OUT if lang == "en" else OUT / "ru"
    outdir.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    for name, fn in PAGES.items():
        width = WIDTHS.get(name, 1360)
        html = SHELL.format(lang=lang, css=CSS.replace("\\", "/"),
                            body=fn(lang), width=width)
        src = TMP / f"{name}-{lang}.html"
        src.write_text(html, encoding="utf-8")
        png = TMP / f"{name}-{lang}.png"
        subprocess.run([
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=2", f"--window-size={width + 40},{HEIGHTS.get(name, 1600)}",
            "--default-background-color=0D111A",
            f"--screenshot={png}", str(src.as_uri()),
        ], check=True, capture_output=True, timeout=120)
        im = Image.open(png).convert("RGB")
        # Обрезка по контенту. Фон страницы — градиент, поэтому сравниваем не
        # с углом кадра, а с началом ТОЙ ЖЕ строки: иначе «отличается от фона»
        # верно для каждой строки и обрезать нечего.
        px = im.load()
        bottom = im.height
        for y in range(im.height - 1, 0, -1):
            row_bg = px[5, y]
            # порог, а не строгое неравенство: фон — градиент, и «не равен началу
            # строки» верно почти для каждого пикселя
            if any(sum(abs(a - b) for a, b in zip(px[x, y], row_bg)) > 24
                   for x in range(0, im.width, 5)):
                bottom = min(im.height, y + 40)
                break
        if name in TIGHT:
            # Узкая карточка: режем по её границам с ОДИНАКОВЫМ полем со всех
            # сторон. Иначе поле сверху задаёт вёрстка, а снизу — обрезка, и
            # карточка в кадре сидит не по центру.
            def has(seq, bg):
                return any(sum(abs(a - b) for a, b in zip(px[x, y], bg)) > 24
                           for x, y in seq)
            cols = [x for x in range(im.width)
                    if has(((x, y) for y in range(0, im.height, 4)), px[x, 3])]
            rows = [y for y in range(im.height)
                    if has(((x, y) for x in range(0, im.width, 4)), px[3, y])]
            m = 106
            im.crop((max(0, cols[0] - m), max(0, rows[0] - m),
                     min(im.width, cols[-1] + 1 + m),
                     min(im.height, rows[-1] + 1 + m))
                    ).save(outdir / f"{name}.png", optimize=True)
        else:
            im.crop((0, 0, im.width, bottom)).save(outdir / f"{name}.png", optimize=True)
        print(f"  {lang}/{name}.png  {(outdir / f'{name}.png').stat().st_size // 1024} КБ")


if __name__ == "__main__":
    for lang in ("en", "ru"):
        print(lang + ":")
        render(lang)
