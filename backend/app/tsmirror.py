"""Локальный мирор бинарей Tailscale-клиента: скачиваем пиновую версию с
pkgs.tailscale.com (с проверкой sha256) в data/tailscale-pkgs, а фронт раздаёт
их публично по <hs-домен>/pkgs. Enroll-скрипты качают с нашего мирора,
фолбэк — на официальный."""

import asyncio
import hashlib
import logging
import os

import httpx

log = logging.getLogger(__name__)

BASE = "https://pkgs.tailscale.com/stable"
TGZ_ARCHES = ("amd64", "arm64", "arm")
MSI_ARCHES = ("amd64", "arm64", "x86")

# Ход текущей загрузки. Живёт в памяти процесса: это не состояние системы, а
# то, что происходит прямо сейчас, и после перезапуска панели показывать нечего.
# Шесть файлов по 35 МБ с другого континента качаются минуты, и всё это время
# вместо прогресса была надпись «Загрузка…» - непонятно, идёт ли дело вообще.
_state: dict = {"running": False, "version": "", "files": {}}


def _total(files: dict) -> int:
    """Сколько байт всего, или 0, если этого пока не знаем.

    Длину файла называет сервер в content-length, и до начала загрузки её нет.
    У файла, который уже дошёл или сорвался, за размер берём набранное: иначе
    одна осечка посреди загрузки обнуляла бы общий размер и полоса, дойдя до
    середины, сбрасывалась бы в неизвестность.
    """
    total = 0
    for f in files.values():
        n = f.get("total") or 0
        if not n:
            if f.get("state") not in ("ok", "fail"):
                return 0
            n = f.get("done") or 0
        total += n
    return total


def progress() -> dict:
    """Снимок хода загрузки: сколько байт у каждого файла и всего."""
    files = {n: dict(v) for n, v in _state["files"].items()}
    return {
        "running": _state["running"],
        "version": _state["version"],
        "files": files,
        "done": sum(f.get("done", 0) for f in files.values()),
        "total": _total(files),
    }


def mirror_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "tailscale-pkgs")


def files_for(version: str) -> list[str]:
    names = [f"tailscale_{version}_{a}.tgz" for a in TGZ_ARCHES]
    names += [f"tailscale-setup-{version}-{a}.msi" for a in MSI_ARCHES]
    return names


def _mark(name: str, **fields) -> None:
    _state["files"].setdefault(name, {"done": 0, "total": 0, "state": "wait", "note": ""})
    _state["files"][name].update(fields)


async def _download_one(client: httpx.AsyncClient, name: str, dest_dir: str) -> dict:
    url = f"{BASE}/{name}"
    tmp = os.path.join(dest_dir, name + ".tmp")
    try:
        h = hashlib.sha256()
        size = 0
        _mark(name, state="load", done=0, total=0, note="")
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                _mark(name, state="fail", note=f"HTTP {r.status_code}")
                return {"name": name, "ok": False, "size": 0, "note": f"HTTP {r.status_code}"}
            try:
                _mark(name, total=int(r.headers.get("content-length") or 0))
            except ValueError:
                _mark(name, total=0)
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
                    _mark(name, done=size)
        # Контрольная сумма обязательна. Раньше при недоступном .sha256 файл
        # всё равно публиковался с пометкой «без sha256» - то есть проверка
        # открывалась при сбое, а раздаётся этот файл нодам, которые ставят из
        # него tailscaled под root.
        try:
            rs = await client.get(url + ".sha256")
        except Exception as e:  # noqa: BLE001
            os.remove(tmp)
            _mark(name, state="fail", note=f"нет sha256: {e}"[:100])
            return {"name": name, "ok": False, "size": size, "note": f"нет sha256: {e}"[:100]}
        if rs.status_code != 200:
            os.remove(tmp)
            _mark(name, state="fail", note=f"нет sha256 (HTTP {rs.status_code})")
            return {
                "name": name, "ok": False, "size": size,
                "note": f"нет sha256 (HTTP {rs.status_code})",
            }
        want = rs.text.strip().split()[0].lower()
        if not want or want != h.hexdigest():
            os.remove(tmp)
            _mark(name, state="fail", note="sha256 mismatch")
            return {"name": name, "ok": False, "size": size, "note": "sha256 mismatch"}
        # Кладём сумму рядом: по ней сверяется нода при установке клиента.
        with open(os.path.join(dest_dir, name + ".sha256"), "w", encoding="utf-8") as f:
            f.write(f"{want}  {name}\n")
        os.replace(tmp, os.path.join(dest_dir, name))
        _mark(name, state="ok", done=size, total=size or _state["files"][name]["total"])
        return {"name": name, "ok": True, "size": size, "note": ""}
    except Exception as e:  # noqa: BLE001
        try:
            os.path.exists(tmp) and os.remove(tmp)
        except OSError:
            pass
        _mark(name, state="fail", note=str(e)[:100])
        return {"name": name, "ok": False, "size": 0, "note": str(e)[:100]}


async def download_mirror(data_dir: str, version: str) -> list[dict]:
    d = mirror_dir(data_dir)
    os.makedirs(d, exist_ok=True)
    _state["running"] = True
    _state["version"] = version
    _state["files"] = {
        n: {"done": 0, "total": 0, "state": "wait", "note": ""} for n in files_for(version)
    }
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            results = await asyncio.gather(
                *[_download_one(client, n, d) for n in files_for(version)]
            )
        return list(results)
    finally:
        _state["running"] = False


async def start_download(data_dir: str, version: str) -> bool:
    """Запустить загрузку в фоне. False - уже идёт, второй раз не начинаем.

    Ответ отдаём сразу, не дожидаясь шести файлов: раньше запрос висел минутами,
    упирался в таймаут обратного прокси и выглядел как сломанная кнопка, хотя
    загрузка при этом шла.
    """
    if _state["running"]:
        return False
    _state["running"] = True  # занимаем место до старта задачи, иначе два клика подряд
    _state["version"] = version
    _state["files"] = {
        n: {"done": 0, "total": 0, "state": "wait", "note": ""} for n in files_for(version)
    }

    async def run() -> None:
        try:
            await download_mirror(data_dir, version)
        except Exception as e:  # noqa: BLE001
            log.warning("мирор: загрузка не удалась: %s", e)
        finally:
            _state["running"] = False

    asyncio.create_task(run())
    return True


def list_mirror(data_dir: str, version: str) -> list[dict]:
    """Что лежит в мироре, вместе с ходом текущей загрузки по каждому файлу."""
    d = mirror_dir(data_dir)
    prog = _state["files"] if _state["version"] == version else {}
    out: list[dict] = []
    for name in files_for(version):
        p = os.path.join(d, name)
        pr = prog.get(name) or {}
        item = {
            "name": name,
            "loading": pr.get("state") == "load",
            "done": int(pr.get("done") or 0),
            "total": int(pr.get("total") or 0),
        }
        if os.path.exists(p):
            item.update({"ok": True, "size": os.path.getsize(p), "note": ""})
        else:
            item.update({"ok": False, "size": 0, "note": pr.get("note") or "нет"})
        out.append(item)
    return out
