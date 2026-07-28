from datetime import datetime, timezone

from app.api.metrics import _bin, _bucket_seconds
from app.models import NodeMetricSample
from tests.conftest import ADMIN_PASSWORD


def test_bucket_seconds():
    # маленький диапазон → базовый интервал (60с)
    assert _bucket_seconds(3600, 60) == 60
    # большой диапазон → укрупняется, чтобы точек было ≤ max
    assert _bucket_seconds(90 * 24 * 3600, 60) >= 21600


def test_bin_averages_online():
    base = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc).timestamp()
    samples = [
        NodeMetricSample(ts=datetime.fromtimestamp(base, timezone.utc), total=3, online=2),
        NodeMetricSample(ts=datetime.fromtimestamp(base + 10, timezone.utc), total=3, online=3),
    ]
    pts = _bin(samples, 300)  # оба в одном 5-мин бакете
    assert len(pts) == 1
    assert pts[0].online == 2  # round((2+3)/2)=round(2.5)=2
    assert pts[0].total == 3


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


async def test_history_requires_auth(client):
    assert (await client.get("/api/metrics/history")).status_code == 401


async def test_history_empty(client):
    token = await _login(client)
    r = await client.get(
        "/api/metrics/history?hours=24", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["interval_seconds"] > 0
