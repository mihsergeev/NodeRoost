"""Ход загрузки мирора: панель спрашивает его, пока бинари качаются."""

import asyncio

import pytest

from app import tsmirror

VERSION = "1.80.0"


@pytest.fixture(autouse=True)
def clean_state():
    empty = {"running": False, "version": "", "files": {}}
    tsmirror._state.update(empty)
    yield
    tsmirror._state.update(empty)


@pytest.mark.asyncio
async def test_download_runs_in_the_background_and_a_second_click_does_nothing(
    tmp_path, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake(data_dir, version):
        started.set()
        tsmirror._mark(tsmirror.files_for(version)[0], state="load", done=10, total=100)
        await release.wait()
        return []

    monkeypatch.setattr(tsmirror, "download_mirror", fake)
    try:
        assert await tsmirror.start_download(str(tmp_path), VERSION) is True
        await asyncio.wait_for(started.wait(), 2)
        # ручка вернулась, пока файл ещё качается - в этом весь смысл
        p = tsmirror.progress()
        assert p["running"] is True and p["done"] == 10
        # второй клик не начинает вторую загрузку поверх первой
        assert await tsmirror.start_download(str(tmp_path), VERSION) is False
    finally:
        release.set()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not tsmirror.progress()["running"]:
            break
    assert tsmirror.progress()["running"] is False


def test_total_is_unknown_only_while_a_file_has_not_said_its_size():
    tsmirror._state["version"] = VERSION
    tsmirror._mark("a", state="load", done=5, total=10)
    tsmirror._mark("b", state="wait", done=0, total=0)
    # пока второй файл не начался, общий размер назвать нечем
    assert tsmirror.progress()["total"] == 0
    tsmirror._mark("b", state="load", done=2, total=20)
    p = tsmirror.progress()
    assert (p["done"], p["total"]) == (7, 30)
    # сорвавшийся файл больше ничего не добавит: полоса не должна сбрасываться
    # в неизвестность на середине
    tsmirror._mark("b", state="fail", note="HTTP 404", total=0, done=2)
    p = tsmirror.progress()
    assert (p["done"], p["total"]) == (7, 12)


def test_listing_shows_the_file_that_is_being_downloaded(tmp_path):
    tsmirror._state["version"] = VERSION
    name = tsmirror.files_for(VERSION)[0]
    tsmirror._mark(name, state="load", done=1024, total=4096)
    item = next(f for f in tsmirror.list_mirror(str(tmp_path), VERSION) if f["name"] == name)
    assert item["loading"] is True
    assert (item["done"], item["total"]) == (1024, 4096)
    assert item["ok"] is False  # файла на диске ещё нет
    # загрузка другой версии не подмешивается в выдачу
    tsmirror._state["version"] = "1.70.0"
    assert all(not f["loading"] for f in tsmirror.list_mirror(str(tmp_path), VERSION))
