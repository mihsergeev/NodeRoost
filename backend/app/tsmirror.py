"""Локальный мирор бинарей Tailscale-клиента: скачиваем пиновую версию с
pkgs.tailscale.com (с проверкой sha256) в data/tailscale-pkgs, а фронт раздаёт
их публично по <hs-домен>/pkgs. Enroll-скрипты качают с нашего мирора,
фолбэк — на официальный."""

import asyncio
import hashlib
import os

import httpx

BASE = "https://pkgs.tailscale.com/stable"
TGZ_ARCHES = ("amd64", "arm64", "arm")
MSI_ARCHES = ("amd64", "arm64", "x86")


def mirror_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "tailscale-pkgs")


def files_for(version: str) -> list[str]:
    names = [f"tailscale_{version}_{a}.tgz" for a in TGZ_ARCHES]
    names += [f"tailscale-setup-{version}-{a}.msi" for a in MSI_ARCHES]
    return names


async def _download_one(client: httpx.AsyncClient, name: str, dest_dir: str) -> dict:
    url = f"{BASE}/{name}"
    tmp = os.path.join(dest_dir, name + ".tmp")
    try:
        h = hashlib.sha256()
        size = 0
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                return {"name": name, "ok": False, "size": 0, "note": f"HTTP {r.status_code}"}
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
        note = ""
        try:
            rs = await client.get(url + ".sha256")
            if rs.status_code == 200:
                want = rs.text.strip().split()[0].lower()
                if want and want != h.hexdigest():
                    os.remove(tmp)
                    return {"name": name, "ok": False, "size": size, "note": "sha256 mismatch"}
            else:
                note = "без sha256"
        except Exception:  # noqa: BLE001
            note = "без sha256"
        os.replace(tmp, os.path.join(dest_dir, name))
        return {"name": name, "ok": True, "size": size, "note": note}
    except Exception as e:  # noqa: BLE001
        try:
            os.path.exists(tmp) and os.remove(tmp)
        except OSError:
            pass
        return {"name": name, "ok": False, "size": 0, "note": str(e)[:100]}


async def download_mirror(data_dir: str, version: str) -> list[dict]:
    d = mirror_dir(data_dir)
    os.makedirs(d, exist_ok=True)
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        results = await asyncio.gather(
            *[_download_one(client, n, d) for n in files_for(version)]
        )
    return list(results)


def list_mirror(data_dir: str, version: str) -> list[dict]:
    d = mirror_dir(data_dir)
    out: list[dict] = []
    for name in files_for(version):
        p = os.path.join(d, name)
        if os.path.exists(p):
            out.append({"name": name, "ok": True, "size": os.path.getsize(p), "note": ""})
        else:
            out.append({"name": name, "ok": False, "size": 0, "note": "нет"})
    return out
