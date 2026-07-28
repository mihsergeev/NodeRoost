from app import ratelimit


def test_public_limit_trips_after_max():
    ratelimit._hits.clear()
    t = 1000.0
    # ровно лимит — ещё пускаем
    for i in range(ratelimit.PUBLIC_MAX):
        assert ratelimit.too_many("1.2.3.4", now=t + i * 0.1) is False
    # следующий сверх лимита — отбой
    assert ratelimit.too_many("1.2.3.4", now=t + 1) is True


def test_public_limit_is_per_source():
    ratelimit._hits.clear()
    t = 1000.0
    for i in range(ratelimit.PUBLIC_MAX + 5):
        ratelimit.too_many("1.2.3.4", now=t + i * 0.1)
    # чужой адрес не должен пострадать от соседа
    assert ratelimit.too_many("5.6.7.8", now=t) is False


def test_window_slides():
    ratelimit._hits.clear()
    t = 1000.0
    for i in range(ratelimit.PUBLIC_MAX + 5):
        ratelimit.too_many("1.2.3.4", now=t + i * 0.1)
    assert ratelimit.too_many("1.2.3.4", now=t + 1) is True
    # окно прошло — счётчик снова чист
    assert ratelimit.too_many("1.2.3.4", now=t + ratelimit.PUBLIC_WINDOW + 1) is False


def test_keys_are_pruned_not_grown_forever():
    """Счётчик сам по себе не должен становиться способом съесть память панели
    запросами с рандомных адресов."""
    ratelimit._hits.clear()
    t = 1000.0
    for i in range(ratelimit._MAX_KEYS + 50):
        ratelimit.too_many(f"10.0.{i // 256}.{i % 256}", now=t)
    # все записи протухли → следующий вызов подчищает их
    ratelimit.too_many("1.2.3.4", now=t + ratelimit.PUBLIC_WINDOW + 1)
    assert len(ratelimit._hits) < ratelimit._MAX_KEYS
