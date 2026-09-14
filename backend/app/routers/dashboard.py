import datetime as dt
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ClusterAggregate, User
from ..security import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/trend")
def trend_history(
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Org-wide daily used-vs-total CPU/RAM series from persisted
    ClusterAggregate history, plus a simple linear-regression forecast of days
    until capacity saturation. This is the one thing the frontend cannot
    reconstruct from a single 'latest snapshot' fetch — everything else in the
    Executive view is computed client-side from /api/data/latest, reusing the
    same calculation functions as the Technical view."""
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    history_rows = (
        db.query(ClusterAggregate)
        .filter(ClusterAggregate.captured_at >= since)
        .order_by(ClusterAggregate.captured_at.asc())
        .all()
    )
    daily = {}
    for row in history_rows:
        day_key = row.captured_at.date().isoformat()
        d = daily.setdefault(day_key, {"date": day_key, "usedVcpu": 0.0, "usedRamGB": 0.0, "totalLogicalCores": 0.0, "totalRamGB": 0.0})
        d["usedVcpu"] += row.used_vcpu or 0
        d["usedRamGB"] += row.used_ram_gb or 0
        d["totalLogicalCores"] += row.total_logical_cores or 0
        d["totalRamGB"] += row.total_ram_gb or 0
    trend = sorted(daily.values(), key=lambda d: d["date"])

    forecast = _forecast_capacity(trend)
    return {"trend": trend, "forecast": forecast}


def _linreg(xs: List[float], ys: List[float]):
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / denom
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _forecast_capacity(trend: List[dict]) -> dict:
    """Directional, transparent linear-regression forecast — NOT a precision
    capacity model. Projects days-to-100% physical capacity from the recent
    used/total trend so Executive view can flag "needs investment soon"."""
    if len(trend) < 3:
        return {"available": False, "reason": "Chưa đủ dữ liệu lịch sử (cần tối thiểu 3 điểm dữ liệu) để dự báo xu hướng."}

    xs = list(range(len(trend)))
    cpu_pct_series = [
        (t["usedVcpu"] / t["totalLogicalCores"] * 100) if t["totalLogicalCores"] else None
        for t in trend
    ]
    ram_pct_series = [
        (t["usedRamGB"] / t["totalRamGB"] * 100) if t["totalRamGB"] else None
        for t in trend
    ]

    result = {"available": True, "cpu": None, "ram": None}
    for label, series in (("cpu", cpu_pct_series), ("ram", ram_pct_series)):
        pts = [(x, y) for x, y in zip(xs, series) if y is not None]
        if len(pts) < 3:
            continue
        reg = _linreg([p[0] for p in pts], [p[1] for p in pts])
        if not reg:
            continue
        slope, _intercept = reg
        current_pct = pts[-1][1]
        if slope <= 0.0001:
            result[label] = {
                "slopePerSample": round(slope, 4), "currentPct": round(current_pct, 1), "daysTo100Pct": None,
                "insight": "Xu hướng sử dụng ổn định hoặc giảm — chưa cần bổ sung năng lực trong ngắn hạn.",
            }
            continue
        days_per_sample = _days_span(trend)
        samples_to_100 = (100 - current_pct) / slope
        days_to_100 = samples_to_100 * days_per_sample if samples_to_100 > 0 else None
        result[label] = {
            "slopePerSample": round(slope, 4), "currentPct": round(current_pct, 1),
            "daysTo100Pct": round(days_to_100, 0) if days_to_100 else None,
            "insight": (
                f"Với tốc độ tăng trưởng hiện tại, {label.upper()} dự kiến chạm ngưỡng 100% công suất vật lý sau khoảng {int(days_to_100)} ngày — nên đưa vào kế hoạch đầu tư/mở rộng."
                if days_to_100 and days_to_100 > 0 else
                "Tốc độ tăng trưởng chưa đủ rõ ràng để đưa ra mốc thời gian dự báo cụ thể."
            ),
        }
    return result


def _days_span(trend: List[dict]) -> float:
    if len(trend) < 2:
        return 1.0
    d0 = dt.date.fromisoformat(trend[0]["date"])
    d1 = dt.date.fromisoformat(trend[-1]["date"])
    span_days = max(1, (d1 - d0).days)
    return span_days / max(1, len(trend) - 1)
