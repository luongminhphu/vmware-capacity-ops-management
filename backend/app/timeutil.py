"""Timezone helpers.

Root cause of the "sync time looks wrong / off by a few hours" bug:
every timestamp in the DB (`SnapshotRun.finished_at`, `AppSetting`,
`ReportLogo.uploaded_at`, ...) is stored as a *naive* UTC datetime
(`datetime.utcnow()`, see models.py). Several API endpoints then called
bare `.isoformat()` on those naive values, which produces a string with
NO timezone marker, e.g. "2026-09-17T06:06:28.123456".

The browser's `new Date("2026-09-17T06:06:28")` treats an offset-less
ISO string as LOCAL time, not UTC. So a sync that actually finished at
06:06 UTC (== 13:06 in Vietnam, UTC+7) was parsed by the frontend as if
06:06 were already Vietnam time, then re-displayed via
`toLocaleString('vi-VN')` unchanged -- appearing ~7h in the past.

Fix: attach the UTC offset before serializing (`to_iso_utc`), so
`new Date(...)` parses it correctly and the browser's own locale
formatting converts it to the viewer's real local time. No frontend
change is needed once the API sends a properly-marked UTC string.

Server-rendered text (PDF reports) never goes through a browser, so it
must convert to Vietnam time itself -- `to_vn_str()` / `now_vn_str()` do
that. Vietnam does not observe DST, so a fixed UTC+7 offset is used
instead of a zoneinfo/tzdata dependency (python:3.12-slim does not ship
the IANA tz database by default).
"""
import datetime as dt
from typing import Optional

VN_TZ = dt.timezone(dt.timedelta(hours=7), name="Asia/Ho_Chi_Minh")


def _as_utc(value: dt.datetime) -> dt.datetime:
    """Attach the UTC offset to a naive datetime, assumed to already be
    UTC (matches how every DateTime column in models.py is populated).
    A datetime that already carries tzinfo is returned unchanged."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def to_iso_utc(value: Optional[dt.datetime]) -> Optional[str]:
    """ISO 8601 string with an explicit UTC offset -- safe for the
    frontend's `new Date(...)`. Use this instead of bare `.isoformat()`
    for any naive-UTC datetime sent to the frontend as JSON."""
    if value is None:
        return None
    return _as_utc(value).isoformat()


def to_vn_str(value: Optional[dt.datetime], fmt: str = "%H:%M %d/%m/%Y") -> str:
    """Format a naive-UTC (or tz-aware) datetime in Vietnam local time,
    for server-rendered text such as PDF reports."""
    if value is None:
        return "—"
    return _as_utc(value).astimezone(VN_TZ).strftime(fmt)


def parse_iso_to_vn_str(iso_value: Optional[str], fmt: str = "%H:%M %d/%m/%Y") -> str:
    """Parse an ISO timestamp string (as produced by `to_iso_utc`, or a
    legacy naive one) and format it in Vietnam local time."""
    if not iso_value:
        return "—"
    try:
        parsed = dt.datetime.fromisoformat(iso_value)
    except ValueError:
        return "—"
    return to_vn_str(parsed, fmt)


def now_vn_str(fmt: str = "%H:%M %d/%m/%Y") -> str:
    """Current time formatted in Vietnam local time, for PDF report
    'generated at' stamps -- server containers commonly run in UTC."""
    return dt.datetime.now(dt.timezone.utc).astimezone(VN_TZ).strftime(fmt)


def to_vn_date(value: dt.datetime) -> dt.date:
    """Calendar date of `value` in Vietnam local time -- use this instead
    of `value.date()` when bucketing snapshots by day, so a poll shortly
    after UTC midnight (still 'yesterday evening' in Vietnam... actually
    still the same Vietnam day since VN is ahead of UTC) buckets onto the
    correct Vietnam-local day rather than the UTC day."""
    return _as_utc(value).astimezone(VN_TZ).date()
