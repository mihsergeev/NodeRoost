from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.models import NodeMetricSample
from app.schemas import HistoryPoint, MetricsHistory

router = APIRouter(prefix="/metrics", tags=["metrics"])

# «Красивая» лесенка шагов бакета (сек): 1м…7д — чтобы на любой диапазон выходило
# ~несколько сотен точек, а не десятки тысяч в одном polyline.
_NICE_STEPS = (
    60, 300, 600, 900, 1800,
    3600, 7200, 10800, 21600, 43200,
    86400, 172800, 604800,
)


def _bucket_seconds(range_seconds: float, base: int, max_points: int = 350) -> int:
    base = max(base, 1)
    min_step = max(base, range_seconds / max_points if max_points else base)
    for step in _NICE_STEPS:
        if step >= min_step:
            return step
    return _NICE_STEPS[-1]


def _bin(samples: list[NodeMetricSample], step: int) -> list[HistoryPoint]:
    # bucket -> [sum_online, count, max_total]
    buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])
    for s in samples:
        b = int(s.ts.timestamp() // step) * step
        buckets[b][0] += s.online
        buckets[b][1] += 1
        buckets[b][2] = max(buckets[b][2], s.total)
    points: list[HistoryPoint] = []
    for b in sorted(buckets):
        so, cnt, mt = buckets[b]
        points.append(
            HistoryPoint(
                ts=datetime.fromtimestamp(b, timezone.utc).isoformat(),
                online=round(so / cnt) if cnt else 0,
                total=mt,
            )
        )
    return points


@router.get("/history", response_model=MetricsHistory)
async def history(session: SessionDep, _: CurrentUser, hours: int = 24) -> MetricsHistory:
    hours = max(1, min(hours, 24 * 90))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = list(
        await session.scalars(
            select(NodeMetricSample)
            .where(NodeMetricSample.ts >= since)
            .order_by(NodeMetricSample.ts)
        )
    )
    step = _bucket_seconds(hours * 3600, get_settings().metrics_interval)
    return MetricsHistory(interval_seconds=step, points=_bin(rows, step))
