"""Определение реального IP клиента за цепочкой caddy → nginx → backend."""

from app.clientip import client_ip

# ВНИМАНИЕ: обычные для этого репозитория «документационные» адреса RFC 5737
# (203.0.113.0/24 и соседи) здесь НЕ годятся — Python относит их к is_private,
# и код честно посчитает их внутренним хопом. Нужен адрес, который сам Python
# признаёт публичным; берём адрес example.com, он ровно для примеров и заведён.
PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "93.184.216.99"


class _Req:
    """Минимальная замена Request: нужны только заголовки и прямой пир."""

    def __init__(self, xff: str = "", peer: str = "172.20.0.5"):
        self.headers = {"x-forwarded-for": xff} if xff else {}

        class _C:
            host = peer

        self.client = _C()


def test_takes_real_client_not_proxy():
    # caddy дописал реальный адрес, nginx — свой контейнерный
    assert client_ip(_Req(f"{PUBLIC}, 172.18.0.2")) == PUBLIC


def test_spoofed_header_cannot_win():
    """Клиент прислал свой X-Forwarded-For — он оказывается ЛЕВЕЕ реального
    адреса, который дописал прокси, а мы идём справа налево."""
    assert client_ip(_Req(f"1.2.3.4, {PUBLIC}, 172.18.0.2")) == PUBLIC


def test_tailnet_client_is_kept():
    """Панель, открытая по мешу: 100.64/10 — законный адрес клиента, а не хоп.
    Иначе все клиенты из меша делили бы один ключ лимитера."""
    assert client_ip(_Req("100.100.0.2, 172.18.0.2")) == "100.100.0.2"


def test_tailnet_spoof_still_loses_to_real_address():
    assert client_ip(_Req(f"100.100.0.99, {PUBLIC}, 172.18.0.2")) == PUBLIC


def test_garbage_is_ignored():
    assert client_ip(_Req(f"не-ip, {PUBLIC}, 172.18.0.2")) == PUBLIC


def test_no_header_falls_back_to_peer():
    assert client_ip(_Req(peer=OTHER_PUBLIC)) == OTHER_PUBLIC
