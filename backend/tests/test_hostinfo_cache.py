"""Кэш host_info не должен прятать новые ноды, пока headscale пишет в WAL.

headscale держит свою SQLite в режиме WAL (write_ahead_log: true) и не закрывает
соединение: новые строки лежат в db.sqlite-wal, а сам db.sqlite не меняется до
контрольной точки. Кэш, который смотрел только на основной файл, отдавал старый
снимок - у только что добавленного сервера не было ни ОС, ни флага.
"""

import json
import sqlite3

import pytest

from app import hostinfo

_HOST_INFO = json.dumps({"OS": "linux", "Distro": "ubuntu", "DistroVersion": "24.04"})
_ENDPOINTS = json.dumps(["192.0.2.10:41641"])


@pytest.fixture
def headscale_db(tmp_path, monkeypatch):
    monkeypatch.setattr(hostinfo, "_cache", None)
    path = tmp_path / "db.sqlite"
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    # как у живого headscale: соединение открыто, контрольная точка не наступила
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, host_info TEXT,"
        " endpoints TEXT, deleted_at DATETIME)"
    )
    con.execute("INSERT INTO nodes VALUES (1, ?, ?, NULL)", (_HOST_INFO, _ENDPOINTS))
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    yield str(path), con
    con.close()


async def test_new_node_visible_while_wal_not_checkpointed(headscale_db):
    path, con = headscale_db
    first = await hostinfo.read_all_async(path)
    assert set(first) == {"1"}

    con.execute("INSERT INTO nodes VALUES (2, ?, ?, NULL)", (_HOST_INFO, _ENDPOINTS))
    con.commit()

    second = await hostinfo.read_all_async(path)
    assert set(second) == {"1", "2"}
    assert second["2"]["os"] == "Ubuntu 24.04"


async def test_unchanged_db_served_from_cache(headscale_db, monkeypatch):
    path, _ = headscale_db
    await hostinfo.read_all_async(path)

    def boom(_path):
        raise AssertionError("неизменившаяся база прочитана повторно")

    monkeypatch.setattr(hostinfo, "read_all", boom)
    assert set(await hostinfo.read_all_async(path)) == {"1"}
