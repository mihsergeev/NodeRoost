"""Страна по IP: офлайн-таблица не должна врать и не должна падать без файла."""

from app import geoip


def test_known_addresses():
    # Hetzner Helsinki: RIR-файлы отвечают DE (владелец диапазона — немецкая
    # компания), геолокация — FI. Именно из-за этого случая таблица собирается
    # из DB-IP, а не из delegated-extended.
    assert geoip.country_of("95.216.29.183") == "FI"
    assert geoip.country_of("8.8.8.8") == "US"
    assert geoip.country_of("217.198.91.18") == "RU"


def test_ipv6():
    assert geoip.country_of("2a01:4f9:c010:1::1") == "FI"


def test_no_country_for_private_and_garbage():
    for ip in ("10.0.0.1", "192.168.1.1", "100.64.0.7", "127.0.0.1",
               "не адрес", "", "   "):
        assert geoip.country_of(ip) == ""


def test_cache_returns_same_answer():
    first = geoip.country_of("1.1.1.1")
    assert geoip.country_of("1.1.1.1") == first
