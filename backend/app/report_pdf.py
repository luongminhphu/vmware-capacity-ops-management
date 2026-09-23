"""
A4 PDF report generation (Executive + Technical Detail views) for VMware
Capacity Ops Management.

Renders an HTML+CSS document with WeasyPrint. Reuses the same capacity /
compliance calculations as the dashboard and CSV export (see capacity.py) so
numbers in the PDF always match the live UI. No external chart images —
utilization bars and the VM power-state donut are drawn as inline SVG so the
report has zero network dependencies and renders identically every time.
"""
import datetime as dt
import html
import itertools
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from weasyprint import HTML

from . import capacity
from .timeutil import now_vn_str, parse_iso_to_vn_str

_SVG_UID = itertools.count()

# ---------------------------------------------------------------------------
# Cool-tone print palette (unified with the in-app dashboard's slate-blue
# theme, but flattened to light-background print colors for legibility on
# paper / PDF viewers).
# ---------------------------------------------------------------------------
COLORS = {
    "ink": "#101828",
    "muted": "#475467",
    "faint": "#98a2b3",
    "border": "#d0d5e0",
    "divider": "#e4e7ec",
    "surface": "#f8fafc",
    "surface2": "#eef2f7",
    # Synced to HSC's brand blue (design-system tokens: --brand / --logo-blue)
    # so headline text in the report matches the company logo's blue exactly.
    "primary": "#2681FF",
    "primary_dark": "#074B9B",
    "accent": "#0891b2",
    "ok": "#15803d",
    "ok_bg": "#e7f5ec",
    "warn": "#b45309",
    "warn_bg": "#fdf1e0",
    "bad": "#c2410c",
    "bad_bg": "#fdece1",
    "crit": "#b91c1c",
    "crit_bg": "#fbe4e2",
    "na": "#667085",
    "na_bg": "#eef1f5",
    # Darker than "surface2" on purpose: this is the unfilled 100% track
    # behind every gauge and bar-chart fill (svg_gauge / bar_cell /
    # svg_ranked_bar_chart), and it needs enough contrast against the white
    # card background for a reader to register the full 100% extent at a
    # glance, not just the filled/colored portion.
    "track_bg": "#c7cfdb",
}

STATUS_COLOR = {"ok": ("ok", "ok_bg"), "warn": ("warn", "warn_bg"), "bad": ("bad", "bad_bg"),
                "crit": ("crit", "crit_bg"), "na": ("na", "na_bg")}

SEVERITY_COLOR = {"critical": ("crit", "crit_bg"), "warning": ("warn", "warn_bg"), "info": ("primary", "surface2")}
SEVERITY_LABEL_VI = {"critical": "Nghiêm trọng", "warning": "Cảnh báo", "info": "Thông tin"}

# Canonical list of compliance-finding types, mirrored from capacity.build_compliance_findings().
# Used to validate/label the optional report-export type filter (?types=... on /api/reports/pdf).
ALL_FINDING_TYPES = ["host_cpu", "host_ram", "overcommit_cpu", "overcommit_ram", "vm_idle", "vm_off", "vm_overprovision"]
FINDING_TYPE_LABEL_VI = {
    "host_cpu": "Host CPU vượt ngưỡng",
    "host_ram": "Host RAM vượt ngưỡng",
    "overcommit_cpu": "Overcommit CPU vượt ngưỡng",
    "overcommit_ram": "Overcommit RAM vượt ngưỡng",
    "vm_idle": "VM idle lâu",
    "vm_off": "VM tắt lâu",
    "vm_overprovision": "VM over-provision",
}


def _finite(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v))


def fmt_num(v, decimals: int = 1, suffix: str = "") -> str:
    if not _finite(v):
        return "—"
    try:
        return f"{v:,.{decimals}f}{suffix}"
    except Exception:
        return "—"


def fmt_int(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(round(v)):,}"
    except Exception:
        return "—"


def fmt_pct(v, decimals: int = 1) -> str:
    if not _finite(v):
        return "—"
    return f"{v:.{decimals}f}%"


def status_pill(status: Optional[Dict[str, str]]) -> str:
    if not status:
        status = {"text": "NA", "cls": "na"}
    fg_key, bg_key = STATUS_COLOR.get(status.get("cls", "na"), STATUS_COLOR["na"])
    return (f'<span class="pill" style="color:{COLORS[fg_key]};background:{COLORS[bg_key]};'
            f'border-color:{COLORS[fg_key]}33">{esc(status.get("text", "NA"))}</span>')


def bar_cell(pct: Optional[float], cls: str = "ok", width_px: int = 108, height_px: int = 15, max_scale: float = 100.0) -> str:
    """Progress-bar cell whose fill width is always a FAITHFUL proportion of
    `pct / max_scale` -- two different percentages never render as the same
    bar length. `max_scale` defaults to 100 because every current call site
    passes an already-computed 0-100 percentage (of an overcommit threshold
    or of allocated storage); pass a different value only for a metric that
    is genuinely NOT a plain percentage.

    The label is drawn INSIDE the colored fill (solid white, centered) only
    when the true fill is wide enough to hold it without inflating the bar;
    otherwise it is drawn just to the right of the fill in muted ink, on the
    plain track -- the same fallback `svg_ranked_bar_chart` uses -- so a
    small percentage still reads as a visually short bar instead of being
    artificially widened to fit its own text.
    """
    fg_key, _ = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    color = COLORS[fg_key]
    clamped = 0.0 if not _finite(pct) else max(0.0, min(max_scale, pct))
    fill_w = max(0.0, min(1.0, clamped / max_scale)) * width_px
    r = height_px / 2.0
    font_size = round(height_px * 0.6, 1)
    label = fmt_pct(pct)
    text_y = round(height_px / 2.0 + font_size * 0.35, 1)
    label_w_est = len(label) * font_size * 0.62 + 10
    fill_w_draw = max(height_px if fill_w > 0.5 else 0.0, fill_w)
    parts = [
        f'<svg width="{width_px}" height="{height_px}" viewBox="0 0 {width_px} {height_px}" '
        f'style="display:block;vertical-align:middle;">',
        f'<rect x="0" y="0" width="{width_px}" height="{height_px}" rx="{r:.1f}" fill="{COLORS["track_bg"]}"/>',
        f'<rect x="0" y="0" width="{fill_w_draw:.1f}" height="{height_px}" rx="{r:.1f}" fill="{color}"/>',
    ]
    if fill_w_draw >= label_w_est:
        cx = fill_w_draw / 2.0
        parts.append(f'<text x="{cx:.1f}" y="{text_y}" text-anchor="middle" font-family="Roboto, Noto Sans, sans-serif" '
                     f'font-size="{font_size}" font-weight="700" fill="#ffffff">{esc(label)}</text>')
    else:
        lx = min(width_px - 2, fill_w_draw + 4)
        parts.append(f'<text x="{lx:.1f}" y="{text_y}" text-anchor="start" font-family="Roboto, Noto Sans, sans-serif" '
                     f'font-size="{font_size}" font-weight="700" fill="{COLORS["muted"]}">{esc(label)}</text>')
    if _finite(pct) and pct > max_scale:
        # Fill is clamped to the cell's full width above -- mark genuine
        # over-threshold values with a small notch near the right edge so
        # "at the cap" and "over the cap" don't look identical.
        nx = width_px - 7
        parts.append(f'<path d="M {nx:.1f} {height_px*0.28:.1f} L {nx+4.5:.1f} {height_px/2:.1f} L {nx:.1f} {height_px*0.72:.1f}" '
                     f'stroke="#ffffff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round">'
                     f'<title>V\u01b0\u1ee3t ng\u01b0\u1ee1ng c\u1ea5u h\u00ecnh</title></path>')
    parts.append('</svg>')
    return "".join(parts)


def svg_donut(segments: List[Dict[str, Any]], size: int = 120, hole: float = 0.62,
              center_label: Optional[str] = None, center_sub: Optional[str] = None) -> str:
    """segments: [{label, value, color}] — color is a hex string.

    When `center_label` is given (e.g. a total count or headline %), it is
    drawn centered in the donut hole so the chart carries its own headline
    number instead of relying purely on an external legend."""
    total = sum(max(0.0, s.get("value", 0) or 0) for s in segments) or 1.0
    r = size / 2
    cx = cy = r
    stroke_w = r * (1 - hole)
    radius = r - stroke_w / 2
    circumference = 2 * math.pi * radius
    offset = 0.0
    # Faint full-circle track behind the segments so an all-zero / single
    # segment donut still reads as a complete ring rather than a lone arc.
    arcs = [f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{COLORS["surface2"]}" stroke-width="{stroke_w}"/>']
    for s in segments:
        val = max(0.0, s.get("value", 0) or 0)
        frac = val / total
        dash = frac * circumference
        gap = circumference - dash
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{s.get("color", "#999")}" '
            f'stroke-width="{stroke_w}" stroke-dasharray="{dash:.2f} {gap:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>'
        )
        offset += dash
    center = ""
    if center_label is not None:
        label_y = cy + (2 if not center_sub else -2)
        center += (f'<text x="{cx}" y="{label_y:.1f}" text-anchor="middle" font-family="Roboto, Noto Sans, sans-serif" '
                   f'font-size="{size*0.16:.1f}" font-weight="800" fill="{COLORS["ink"]}">{esc(center_label)}</text>')
        if center_sub:
            center += (f'<text x="{cx}" y="{cy+size*0.14:.1f}" text-anchor="middle" font-family="Roboto, Noto Sans, sans-serif" '
                       f'font-size="{size*0.075:.1f}" fill="{COLORS["muted"]}">{esc(center_sub)}</text>')
    return f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" font-family="Roboto, Noto Sans, sans-serif">{"".join(arcs)}{center}</svg>'


GAUGE_ZONES_CPU_RAM = ((0.0, 80.0, "ok"), (80.0, 100.0, "warn"))
GAUGE_ZONES_STORAGE = ((0.0, 80.0, "ok"), (80.0, 90.0, "warn"), (90.0, 100.0, "crit"))


def svg_gauge(pct: Optional[float], color: str, size: int = 132, max_scale: float = 100.0,
              label_top: Optional[str] = None, caption: Optional[str] = None,
              zones=GAUGE_ZONES_CPU_RAM) -> str:
    """Semicircular gauge whose track is split into tinted *zones* (safe /
    watch / over) so a reader sees not only the current value but also how
    far it sits from the warning band -- the arc sweep is proportional to
    `pct` (capped at `max_scale`) and drawn in the status `color`.

    The drawn arc always sweeps at most 180 degrees (from the 9-o'clock point
    to some point along the top half of the circle), so it is always the
    *minor* arc between its two endpoints -- the SVG large-arc-flag must
    therefore always be 0 (a previous version flipped it above 50% and the
    arc rendered as a broken fragment outside the viewBox)."""
    w = float(size)
    stroke_w = w * 0.105
    r = w / 2 - stroke_w / 2 - 4
    cx, cy = w / 2, w / 2 + 2
    h = w / 2 + stroke_w / 2 + (30 if (label_top or caption) else 14)

    def polar(deg: float):
        rad = math.radians(deg)
        return cx + r * math.cos(rad), cy - r * math.sin(rad)

    def arc(from_frac: float, to_frac: float, stroke: str, width: float, opacity: float = 1.0, cap: str = "butt") -> str:
        a0 = 180 - 180 * max(0.0, min(1.0, from_frac))
        a1 = 180 - 180 * max(0.0, min(1.0, to_frac))
        xa, ya = polar(a0)
        xb, yb = polar(a1)
        return (f'<path d="M {xa:.1f} {ya:.1f} A {r:.1f} {r:.1f} 0 0 1 {xb:.1f} {yb:.1f}" fill="none" stroke="{stroke}" '
                f'stroke-width="{width:.1f}" stroke-opacity="{opacity}" stroke-linecap="{cap}"/>')

    parts = [f'<svg width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}" font-family="Roboto, Noto Sans, sans-serif">']
    # Zone bands (light tints) underneath, so they read as "background".
    for lo, hi, cls in zones:
        fg_key, _ = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
        parts.append(arc(lo / max_scale, hi / max_scale, COLORS[fg_key], stroke_w, opacity=0.22))
    # Zone boundary ticks.
    for lo, hi, cls in zones[1:]:
        deg = 180 - 180 * (lo / max_scale)
        xi, yi = cx + (r - stroke_w / 2 - 1) * math.cos(math.radians(deg)), cy - (r - stroke_w / 2 - 1) * math.sin(math.radians(deg))
        xo, yo = cx + (r + stroke_w / 2 + 1) * math.cos(math.radians(deg)), cy - (r + stroke_w / 2 + 1) * math.sin(math.radians(deg))
        parts.append(f'<line x1="{xi:.1f}" y1="{yi:.1f}" x2="{xo:.1f}" y2="{yo:.1f}" stroke="{COLORS["muted"]}" stroke-width="1"/>')
    clamped = 0.0 if not _finite(pct) else max(0.0, min(max_scale, pct))
    frac = clamped / max_scale
    if frac > 0.008:
        parts.append(arc(0.0, frac, color, stroke_w * 0.72, cap="round"))
        xe, ye = polar(180 - 180 * frac)
        parts.append(f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="{stroke_w*0.42:.1f}" fill="#ffffff" stroke="{color}" stroke-width="2"/>')
    label = fmt_pct(pct, 0)
    parts.append(f'<text x="{cx:.1f}" y="{cy-2:.1f}" text-anchor="middle" font-size="{w*0.175:.1f}" font-weight="800" fill="{COLORS["ink"]}">{label}</text>')
    if caption:
        parts.append(f'<text x="{cx:.1f}" y="{cy+9:.1f}" text-anchor="middle" font-size="7.2" fill="{COLORS["muted"]}">{esc(caption)}</text>')
    # Scale end labels.
    x0, y0 = polar(180)
    x1, y1 = polar(0)
    parts.append(f'<text x="{x0:.1f}" y="{y0+stroke_w/2+9:.1f}" text-anchor="middle" font-size="6.8" fill="{COLORS["faint"]}">0%</text>')
    parts.append(f'<text x="{x1:.1f}" y="{y1+stroke_w/2+9:.1f}" text-anchor="middle" font-size="6.8" fill="{COLORS["faint"]}">{max_scale:.0f}%</text>')
    if label_top:
        parts.append(f'<text x="{cx:.1f}" y="{h-4:.1f}" text-anchor="middle" font-size="8.5" font-weight="700" fill="{COLORS["muted"]}">{esc(label_top)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def _nice_ceiling(v: float) -> float:
    """Round `v` up to a 'nice' axis maximum (…, 50, 60, 80, 100, 120, 150, 200, …)."""
    if not _finite(v) or v <= 0:
        return 100.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * mag >= v:
            return m * mag
    return 10 * mag


def _wrap_label(label: str, max_chars: int, max_lines: int = 2) -> List[str]:
    """Split a cluster/host name into up to `max_lines` lines on natural
    separators ('-', '_', ' ', '.') so chart category labels stay legible
    instead of being truncated to 'PROD-CLUSTE…'."""
    label = str(label)
    if len(label) <= max_chars:
        return [label]
    tokens = re.split(r"(?<=[-_ .])", label)
    lines, cur = [], ""
    for tok in tokens:
        if cur and len(cur) + len(tok) > max_chars:
            lines.append(cur)
            cur = tok
        else:
            cur += tok
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = (lines[-1][:max_chars - 1] + "…") if len(lines[-1]) > max_chars - 1 else lines[-1] + "…"
    return [ln.strip() for ln in lines if ln.strip()] or [label[:max_chars - 1] + "…"]


def svg_bar_chart(labels: List[str], series: List[Dict[str, Any]], width: int = 720, height: int = 220,
                   max_value: Optional[float] = None, threshold: Optional[float] = None,
                   threshold_label: str = "Ngưỡng", unit: str = "", show_values: bool = True) -> str:
    """series: [{name, color, values}] grouped vertical bars, one group per label.

    Axis scale is derived from the data (a 'nice' ceiling just above the
    tallest bar / the threshold) so bars fill the plot instead of huddling
    at the bottom of a fixed 0-140 range; an optional dashed `threshold`
    line (e.g. 100% of the overcommit policy) gives the bars a reference,
    and each bar carries its own value label so no legend lookup is needed."""
    if not labels:
        return '<div style="color:#98a2b3;font-size:11px;padding:8px 0;">Không có dữ liệu.</div>'
    n_groups = len(labels)
    label_chars = max(6, int((width - 60) / n_groups / 5.2))
    wrapped = [_wrap_label(lb, label_chars) for lb in labels]
    label_lines = max(len(w) for w in wrapped)
    pad_l, pad_r, pad_t, pad_b = 36, 10, 14, 10 + 10 * label_lines
    if threshold is not None:
        pad_r = max(pad_r, 58)
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_vals = [v for s in series for v in s["values"] if _finite(v)]
    data_max = max(all_vals) if all_vals else 0.0
    if max_value is not None:
        mx = max_value
    else:
        target = max(data_max * 1.15, (threshold or 0) * 1.1, 10.0)
        mx = _nice_ceiling(target)
    mx = mx if mx > 0 else 1.0
    n_series = max(1, len(series))
    group_w = plot_w / n_groups
    bar_gap = group_w * 0.28
    bar_w = (group_w - bar_gap) / n_series
    inner_gap = min(3.0, bar_w * 0.12)

    def y_of(v: float) -> float:
        return pad_t + plot_h * (1 - max(0.0, min(1.0, v / mx)))

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Roboto, Noto Sans, sans-serif">']
    # Soft plot background so the chart reads as one visual block.
    parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w:.1f}" height="{plot_h:.1f}" rx="4" fill="{COLORS["surface2"]}"/>')
    steps = 4
    for i in range(steps + 1):
        frac = i / steps
        y = pad_t + plot_h * (1 - frac)
        val = mx * frac
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" stroke="#ffffff" stroke-width="1.2"/>')
        parts.append(f'<text x="{pad_l-5}" y="{y+3:.1f}" text-anchor="end" font-size="7.8" fill="{COLORS["muted"]}">{val:.0f}{esc(unit)}</text>')
    if threshold is not None and 0 < threshold <= mx:
        ty = y_of(threshold)
        parts.append(f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{width-pad_r}" y2="{ty:.1f}" stroke="{COLORS["crit"]}" stroke-width="1.2" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{width-pad_r+4}" y="{ty+3:.1f}" text-anchor="start" font-size="7" font-weight="700" fill="{COLORS["crit"]}">{esc(threshold_label)} {threshold:.0f}{esc(unit)}</text>')
    for gi, label in enumerate(labels):
        gx = pad_l + gi * group_w + bar_gap / 2
        for si, srs in enumerate(series):
            v = srs["values"][gi] if gi < len(srs["values"]) else 0
            v = v if _finite(v) else 0
            bh = max(0.0, min(1.0, v / mx)) * plot_h
            bx = gx + si * bar_w + inner_gap / 2
            bw = max(2.0, bar_w - inner_gap)
            by = pad_t + plot_h - bh
            over = threshold is not None and v > threshold
            fill = COLORS["crit"] if over else srs["color"]
            if bh > 0:
                parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2.5" fill="{fill}"/>')
            if show_values:
                vy = by - 3 if bh > 0 else pad_t + plot_h - 3
                parts.append(f'<text x="{bx+bw/2:.1f}" y="{vy:.1f}" text-anchor="middle" font-size="7.4" font-weight="700" fill="{COLORS["ink"]}">{v:.0f}{esc(unit)}</text>')
        lx = pad_l + gi * group_w + group_w / 2
        for li, line in enumerate(wrapped[gi]):
            parts.append(f'<text x="{lx:.1f}" y="{pad_t+plot_h+10+li*9.5:.1f}" text-anchor="middle" font-size="7.6" font-weight="600" fill="{COLORS["muted"]}">{esc(line)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_ranked_bar_chart(rows: List[Dict[str, Any]], width: int = 330, height: Optional[int] = None,
                          max_scale: float = 100.0, name_max_len: int = 13, top_n: int = 10) -> str:
    """Generic horizontal ranked bar chart: one row per entity (cluster/host/
    datastore), sorted descending by `pct`. The bar is colored by risk status
    (`cls`) and the % label is embedded INSIDE the colored fill (right-aligned)
    when the fill is wide enough, matching the same "label inside the bar"
    treatment as `bar_cell()`; falls back to just outside the fill for very
    short bars so the number never gets clipped or overlaps a badge.

    `rows`: [{"name": str, "pct": float, "cls": "ok"|"warn"|"bad"|"crit"|"na",
              "badge": Optional[int], "badge_crit": bool, "badge_title": str}]
    """
    rows = [r for r in rows if _finite(r.get("pct"))]
    if not rows:
        return '<div style="color:#98a2b3;font-size:11px;padding:8px 0;">Không có dữ liệu.</div>'
    rows = sorted(rows, key=lambda r: r["pct"], reverse=True)[:top_n]

    row_h = 20
    # Reserve enough left padding for the row-name column so long labels
    # (e.g. datastore names truncated at a longer `name_max_len`) never draw
    # past x=0, where inline SVG gets clipped by its own viewBox in
    # WeasyPrint -- that clipping previously ate the first few characters
    # off long names instead of showing the truncation ellipsis.
    pad_l, pad_r, pad_t = max(78, round(name_max_len * 4.8) + 14), 34, 4
    has_badge = any(r.get("badge") for r in rows)
    if has_badge:
        pad_r = 58
    height = height if height is not None else pad_t + len(rows) * row_h + 6
    plot_w = width - pad_l - pad_r

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Roboto, Noto Sans, sans-serif">']
    for i, r in enumerate(rows):
        pct = r["pct"]
        cls = r.get("cls", "na")
        fg_key, _ = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
        y = pad_t + i * row_h
        bar_y = y + 3
        bar_h = row_h - 8
        clamped = max(0.0, min(max_scale, pct))
        bar_w = clamped / max_scale * plot_w
        name = str(r.get("name", ""))
        if len(name) > name_max_len:
            # Middle-ellipsis so the distinguishing suffix (e.g. "-esx07",
            # "-ds02") survives truncation instead of the shared prefix.
            tail = max(5, min(8, name_max_len // 3))
            name = name[:name_max_len - tail - 1] + "…" + name[-tail:]
        label = fmt_pct(pct, 0)
        parts.append(f'<text x="{pad_l-6}" y="{bar_y+bar_h/2+3:.1f}" text-anchor="end" font-size="8.3" fill="{COLORS["ink"]}">{esc(name)}</text>')
        parts.append(f'<rect x="{pad_l}" y="{bar_y:.1f}" width="{plot_w:.1f}" height="{bar_h}" rx="{bar_h/2:.1f}" fill="{COLORS["track_bg"]}"/>')
        fill_w = max(bar_h, bar_w)
        parts.append(f'<rect x="{pad_l}" y="{bar_y:.1f}" width="{fill_w:.1f}" height="{bar_h}" rx="{bar_h/2:.1f}" fill="{COLORS[fg_key]}"/>')
        if bar_w >= 20:
            lx = pad_l + fill_w - 5
            parts.append(f'<text x="{lx:.1f}" y="{bar_y+bar_h/2+2.9:.1f}" text-anchor="end" font-size="7.6" font-weight="700" fill="#ffffff">{label}</text>')
        else:
            lx = pad_l + fill_w + 5
            parts.append(f'<text x="{lx:.1f}" y="{bar_y+bar_h/2+2.9:.1f}" text-anchor="start" font-size="7.6" font-weight="700" fill="{COLORS["muted"]}">{label}</text>')
        if pct > max_scale:
            # Genuine over-threshold value (e.g. "vượt ngưỡng" CPU/RAM) --
            # the fill itself is clamped to the bar's full width above, so
            # add a small chevron just past the bar end as a distinct
            # visual cue that this one is off the top of the scale.
            ov_x = pad_l + plot_w + 4
            ov_y = bar_y + bar_h / 2
            parts.append(f'<path d="M {ov_x:.1f} {ov_y-4:.1f} L {ov_x+5:.1f} {ov_y:.1f} L {ov_x:.1f} {ov_y+4:.1f} Z" fill="{COLORS[fg_key]}"><title>Vượt ngưỡng cấu hình</title></path>')
        badge_n = r.get("badge")
        if badge_n:
            badge_title = r.get("badge_title", "")
            badge_color = COLORS["crit"] if r.get("badge_crit") else COLORS["warn"]
            bx = pad_l + plot_w + (30 if pct > max_scale else 22)
            parts.append(f'<circle cx="{bx}" cy="{bar_y+bar_h/2:.1f}" r="7" fill="{badge_color}"><title>{esc(badge_title)}</title></circle>')
            parts.append(f'<text x="{bx}" y="{bar_y+bar_h/2+2.8:.1f}" text-anchor="middle" font-size="7.5" font-weight="700" fill="#ffffff">{badge_n}<title>{esc(badge_title)}</title></text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_cluster_risk_chart(cluster_rows: List[Dict[str, Any]], metric: str, finding_counts: Dict[str, Dict[str, int]],
                            width: int = 330, height: Optional[int] = None, name_max_len: int = 13) -> str:
    """Cluster-specific wrapper around svg_ranked_bar_chart for a single
    metric ('cpu' or 'ram'): colors each bar by that cluster's own risk
    status and attaches a critical/warning finding-count badge at row end."""
    pct_key = f"{metric}Pct"
    status_key = f"{metric}Status"
    rows = []
    for c in cluster_rows:
        if not _finite(c["model"].get(pct_key)):
            continue
        fc = finding_counts.get(c["name"]) or {}
        crit_n, warn_n = fc.get("critical", 0), fc.get("warning", 0)
        badge_title = "·".join(([f"{crit_n} nghiêm trọng"] if crit_n else []) + ([f"{warn_n} cảnh báo"] if warn_n else []))
        rows.append({
            "name": c["name"], "pct": c["model"][pct_key], "cls": c[status_key]["cls"],
            "badge": (crit_n + warn_n) or None, "badge_crit": bool(crit_n), "badge_title": badge_title,
        })
    return svg_ranked_bar_chart(rows, width=width, height=height, name_max_len=name_max_len)


def svg_radar_chart(categories: List[str], series: List[Dict[str, Any]], size: int = 232, max_value: float = 100.0, rings: int = 4) -> str:
    """Radar/spider chart comparing several entities (clusters/hosts) across
    the SAME set of 0-`max_value` metrics on one glance -- a deliberately
    different chart family from the bars/gauges/donuts used elsewhere in this
    report, so multi-metric comparisons don't all look like the same widget.

    `categories`: axis labels in order, e.g. ["CPU", "RAM", "Storage"].
    `series`: [{"name": str, "color": "#hex", "values": [float, ...]}] --
      `values` aligned to `categories`; a value above `max_value` (e.g. an
      over-threshold CPU/RAM %) is clamped to the outer ring, the same
      "cap the fill, keep the real number in the legend" treatment used by
      `bar_cell`/`svg_ranked_bar_chart`.
    """
    n = len(categories)
    if n < 3 or not series:
        return '<div style="color:#98a2b3;font-size:11px;padding:8px 0;">Kh\u00f4ng \u0111\u1ee7 d\u1eef li\u1ec7u \u0111\u1ec3 v\u1ebd bi\u1ec3u \u0111\u1ed3.</div>'
    cx = cy = size / 2
    r = size / 2 - 30

    def axis_point(i: int, frac: float):
        angle = -math.pi / 2 + i * 2 * math.pi / n
        return cx + r * frac * math.cos(angle), cy + r * frac * math.sin(angle)

    # The vertex layout is asymmetric for odd `n` (e.g. n=3 has one axis
    # pointing straight up and two pointing down-ish), so a square size x size
    # box centered on (cx, cy) leaves a lot of dead space on whichever side is
    # shorter. Compute the actual bounding box from the outer label ring and
    # crop the viewBox tightly to it (plus small text padding) instead.
    label_pts = [axis_point(i, 1.2) for i in range(n)]
    pad_x, pad_y = 20, 11
    min_x = min(p[0] for p in label_pts) - pad_x
    max_x = max(p[0] for p in label_pts) + pad_x
    min_y = min(p[1] for p in label_pts) - pad_y
    max_y = max(p[1] for p in label_pts) + pad_y
    box_w = max_x - min_x
    box_h = max_y - min_y

    parts = [f'<svg width="{box_w:.0f}" height="{box_h:.0f}" viewBox="{min_x:.1f} {min_y:.1f} {box_w:.1f} {box_h:.1f}" font-family="Roboto, Noto Sans, sans-serif">']
    outer_pts = " ".join(f"{axis_point(i, 1.0)[0]:.1f},{axis_point(i, 1.0)[1]:.1f}" for i in range(n))
    parts.append(f'<polygon points="{outer_pts}" fill="{COLORS["surface2"]}" stroke="none"/>')
    for ring in range(1, rings + 1):
        frac = ring / rings
        pts = " ".join(f"{axis_point(i, frac)[0]:.1f},{axis_point(i, frac)[1]:.1f}" for i in range(n))
        parts.append(f'<polygon points="{pts}" fill="none" stroke="#ffffff" stroke-width="1.2"/>')
        # Ring value label along the first (top) axis so the scale is readable.
        rx, ry = axis_point(0, frac)
        parts.append(f'<text x="{rx+4:.1f}" y="{ry+3:.1f}" font-size="6.8" fill="{COLORS["faint"]}">{max_value*frac:.0f}%</text>')
    for i, cat in enumerate(categories):
        x, y = axis_point(i, 1.0)
        parts.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{COLORS["divider"]}" stroke-width="1"/>')
        lx, ly = axis_point(i, 1.2)
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="8.5" font-weight="700" fill="{COLORS["muted"]}">{esc(cat)}</text>')
    for s in series:
        vals = s.get("values", [])
        color = s.get("color", COLORS["primary"])
        pts = []
        for i in range(n):
            v = vals[i] if i < len(vals) and _finite(vals[i]) else 0
            frac = max(0.0, min(1.0, v / max_value))
            pts.append(axis_point(i, frac))
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polygon points="{pts_str}" fill="{color}26" stroke="{color}" stroke-width="1.7"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.3" fill="{color}"/>')
    parts.append('</svg>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Shared page chrome
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Snapshot-history section (CPU/RAM change over the last 3/6/12 months) and
# the VM added/removed appendix. Both consume the dicts produced by
# app.history so the PDF always agrees with the web panel.
# ---------------------------------------------------------------------------
HISTORY_SERIES_COLORS = ["#2563eb", "#0891b2", "#7c3aed", "#db2777", "#ea580c", "#16a34a", "#ca8a04", "#64748b"]


def _fmt_short_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        return dt.date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _fmt_delta(v: Optional[float], decimals: int = 1, unit: str = "") -> str:
    if not _finite(v):
        return "—"
    sign = "+" if v > 0 else ("−" if v < 0 else "±")
    return f"{sign}{abs(v):.{decimals}f}{unit}"


def _delta_color(v: Optional[float]) -> str:
    if not _finite(v) or abs(v) < 0.05:
        return COLORS["muted"]
    return COLORS["warn"] if v > 0 else COLORS["ok"]


def svg_line_chart(dates: List[str], series: List[Dict[str, Any]], width: int = 320, height: int = 170,
                   threshold: Optional[float] = 100.0, threshold_label: str = "Ngưỡng", unit: str = "%") -> str:
    """series: [{name, color, values (aligned to dates, None = gap), dashed}].

    Same visual language as svg_bar_chart: soft plot background, white grid
    lines, 'nice' auto scale that always shows the threshold, dashed red
    threshold line, sparse dd/mm date labels and a value tag on the latest
    point of every solid line so the reader sees "now" without a legend."""
    if not dates or len(dates) < 2:
        return '<div style="color:#98a2b3;font-size:9px;padding:8px 0;">Chưa đủ dữ liệu lịch sử.</div>'
    pad_l, pad_r, pad_t, pad_b = 32, 8, 14, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_vals = [v for srs in series for v in srs["values"] if _finite(v)]
    data_max = max(all_vals) if all_vals else 0.0
    target = max(data_max * 1.15, (threshold or 0) * 1.1, 10.0)
    mx = _nice_ceiling(target) or 1.0
    n = len(dates)

    def x_of(i: int) -> float:
        return pad_l + plot_w * (i / (n - 1))

    def y_of(v: float) -> float:
        return pad_t + plot_h * (1 - max(0.0, min(1.0, v / mx)))

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Roboto, Noto Sans, sans-serif">']
    parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w:.1f}" height="{plot_h:.1f}" rx="4" fill="{COLORS["surface2"]}"/>')
    steps = 4
    for i in range(steps + 1):
        frac = i / steps
        y = pad_t + plot_h * (1 - frac)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" stroke="#ffffff" stroke-width="1.2"/>')
        parts.append(f'<text x="{pad_l-4}" y="{y+3:.1f}" text-anchor="end" font-size="7.4" fill="{COLORS["muted"]}">{mx*frac:.0f}{esc(unit)}</text>')
    if threshold is not None and 0 < threshold <= mx:
        ty = y_of(threshold)
        parts.append(f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{width-pad_r}" y2="{ty:.1f}" stroke="{COLORS["crit"]}" stroke-width="1.1" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{width-pad_r-2}" y="{ty-3:.1f}" text-anchor="end" font-size="6.8" font-weight="700" fill="{COLORS["crit"]}">{esc(threshold_label)} {threshold:.0f}{esc(unit)}</text>')
    n_labels = min(6, n)
    label_idx = sorted({int(round(i * (n - 1) / (n_labels - 1))) for i in range(n_labels)}) if n_labels > 1 else [0]
    for i in label_idx:
        x = x_of(i)
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t+plot_h:.1f}" x2="{x:.1f}" y2="{pad_t+plot_h+3:.1f}" stroke="{COLORS["border"]}" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{pad_t+plot_h+12:.1f}" text-anchor="{anchor}" font-size="7.2" fill="{COLORS["muted"]}">{esc(_fmt_short_date(dates[i])[:5])}</text>')
    end_labels: List[Tuple[float, float, str, str]] = []
    for srs in series:
        pts = [(x_of(i), y_of(v)) for i, v in enumerate(srs["values"]) if _finite(v)]
        if len(pts) < 2:
            continue
        d = " ".join(f'{"M" if k == 0 else "L"} {x:.1f} {y:.1f}' for k, (x, y) in enumerate(pts))
        dash = ' stroke-dasharray="5 3"' if srs.get("dashed") else ""
        parts.append(f'<path d="{d}" fill="none" stroke="{srs["color"]}" stroke-width="{1.4 if srs.get("dashed") else 1.9}" stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        if not srs.get("dashed"):
            xe, ye = pts[-1]
            last_val = [v for v in srs["values"] if _finite(v)][-1]
            parts.append(f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="2.4" fill="#ffffff" stroke="{srs["color"]}" stroke-width="1.6"/>')
            end_labels.append((ye, xe, f"{last_val:.0f}{unit}", srs["color"]))
    # End-value tags: place from the top down, skipping any that would
    # collide with an already-placed tag (lines that end close together are
    # still readable from the table below).
    last_y = -100.0
    for ye, xe, text, color in sorted(end_labels):
        if ye - last_y < 8.5:
            continue
        parts.append(f'<text x="{xe-4:.1f}" y="{ye-4:.1f}" text-anchor="end" font-size="6.8" font-weight="700" fill="{color}">{esc(text)}</text>')
        last_y = ye
    parts.append("</svg>")
    return "".join(parts)


def _history_legend_html(history: Dict[str, Any]) -> str:
    items = []
    groups = history.get("groups", [])
    for i, g in enumerate(groups):
        color = HISTORY_SERIES_COLORS[i % len(HISTORY_SERIES_COLORS)]
        items.append(f'<span><span class="dot" style="background:{color};"></span>{esc(g["label"])}</span>')
    if len(groups) != 1:
        items.append(f'<span><span class="dot" style="background:{COLORS["ink"]};height:2px;width:12px;border-radius:0;"></span>{esc(history["overall"].get("label") or "Toàn bộ")} (đường đứt)</span>')
    return f'<div class="legend" style="margin-top:4px;">{"".join(items)}</div>'


def _history_series(history: Dict[str, Any], field: str, max_points: int = 60) -> Tuple[List[str], List[Dict[str, Any]]]:
    from . import history as history_mod
    overall = history_mod.downsample(history["overall"]["series"], max_points)
    dates = [p["date"] for p in overall]
    groups = history.get("groups", [])
    series = []
    for i, g in enumerate(groups):
        lookup = {p["date"]: p.get(field) for p in g["series"]}
        series.append({"name": g["label"], "color": HISTORY_SERIES_COLORS[i % len(HISTORY_SERIES_COLORS)],
                       "values": [lookup.get(d) for d in dates]})
    if len(groups) != 1:
        series.append({"name": history["overall"].get("label") or "Toàn bộ", "color": COLORS["ink"], "dashed": True, "values": [p.get(field) for p in overall]})
    return dates, series


def _history_insights(history: Dict[str, Any]) -> List[str]:
    """Two or three plain-language sentences a manager can act on."""
    s = history["overall"]["summary"]
    d = s.get("delta", {})
    months = history["months"]
    out: List[str] = []
    cpu_d, ram_d = d.get("cpuPct"), d.get("ramPct")
    vm_d, host_d = d.get("vmCount"), d.get("hostCount")
    out.append(
        f"Trong {months} tháng, mức dùng so với ngưỡng overcommit thay đổi <b>{_fmt_delta(cpu_d, 1, ' điểm')}</b> ở CPU "
        f"({fmt_num(s['first'].get('cpuPct'))}% → {fmt_num(s['last'].get('cpuPct'))}%) và <b>{_fmt_delta(ram_d, 1, ' điểm')}</b> ở RAM "
        f"({fmt_num(s['first'].get('ramPct'))}% → {fmt_num(s['last'].get('ramPct'))}%); số VM {_fmt_delta(vm_d, 0)} "
        f"({fmt_int(s['first'].get('vmCount'))} → {fmt_int(s['last'].get('vmCount'))}), số host {_fmt_delta(host_d, 0)}.")
    if (ram_d or 0) >= (cpu_d or 0):
        binding_label, binding_delta, binding_last = "RAM", ram_d, s["last"].get("ramPct")
    else:
        binding_label, binding_delta, binding_last = "CPU", cpu_d, s["last"].get("cpuPct")
    if _finite(binding_delta) and binding_delta > 0.3 and _finite(binding_last) and binding_last < 100:
        pace = binding_delta / months
        eta = (100 - binding_last) / pace
        eta_txt = f"khoảng {eta:.0f} tháng" if eta < 60 else "trên 5 năm"
        out.append(f"Nếu giữ đúng tốc độ {months} tháng vừa qua ({pace:.1f} điểm/tháng), <b>{binding_label}</b> sẽ chạm 100% ngưỡng sau <b>{eta_txt}</b> — mốc nên đối chiếu với kế hoạch ngân sách và thời gian mua sắm thiết bị.")
    elif _finite(binding_delta) and binding_delta <= 0.3:
        out.append("Mức sử dụng gần như đi ngang hoặc giảm trong kỳ — xu hướng lịch sử chưa tạo áp lực đầu tư thêm năng lực; ưu tiên tối ưu và thu hồi tài nguyên nhàn rỗi.")
    groups = [g["summary"] for g in history.get("groups", []) if g["summary"].get("points")]
    if len(groups) > 1:
        fastest = max(groups, key=lambda g: max(g["delta"].get("cpuPct") or 0, g["delta"].get("ramPct") or 0))
        fd = fastest["delta"]
        top_metric = "RAM" if (fd.get("ramPct") or 0) >= (fd.get("cpuPct") or 0) else "CPU"
        top_val = fd.get("ramPct") if top_metric == "RAM" else fd.get("cpuPct")
        if _finite(top_val) and top_val > 0.3:
            gword = "Cluster" if history.get("groupBy") == "cluster" else "vCenter"
            out.append(f"Tăng nhanh nhất là {gword.lower()} <b>{esc(fastest['label'])}</b> ({top_metric} {_fmt_delta(top_val, 1, ' điểm')}, VM {_fmt_delta(fd.get('vmCount'), 0)}) — nơi nên ưu tiên đề xuất bổ sung tài nguyên hoặc cân bằng tải trước.")
    return out


def _history_table_html(history: Dict[str, Any], group_label: str) -> str:
    rows = [g["summary"] for g in history.get("groups", []) if g["summary"].get("points")]
    total = None
    if len(history.get("groups", [])) != 1:
        total = dict(history["overall"]["summary"])
        total["label"] = history["overall"].get("label") or "Toàn bộ"

    def _row(sm: Dict[str, Any], bold: bool = False) -> str:
        d = sm.get("delta", {})
        style = f' style="background:{COLORS["surface2"]};font-weight:700;"' if bold else ""
        return (f'<tr{style}><td><b>{esc(sm["label"])}</b></td>'
                f'<td>{fmt_num(sm["first"].get("cpuPct"))}% → <b>{fmt_num(sm["last"].get("cpuPct"))}%</b></td>'
                f'<td style="color:{_delta_color(d.get("cpuPct"))};font-weight:700;">{_fmt_delta(d.get("cpuPct"), 1, " đ")}</td>'
                f'<td>{fmt_num(sm["first"].get("ramPct"))}% → <b>{fmt_num(sm["last"].get("ramPct"))}%</b></td>'
                f'<td style="color:{_delta_color(d.get("ramPct"))};font-weight:700;">{_fmt_delta(d.get("ramPct"), 1, " đ")}</td>'
                f'<td>{fmt_num(sm.get("peakCpuPct"), 0)}% / {fmt_num(sm.get("peakRamPct"), 0)}%</td>'
                f'<td>{fmt_int(sm["first"].get("hostCount"))} → {fmt_int(sm["last"].get("hostCount"))} ({_fmt_delta(d.get("hostCount"), 0)})</td>'
                f'<td>{fmt_int(sm["first"].get("vmCount"))} → {fmt_int(sm["last"].get("vmCount"))} ({_fmt_delta(d.get("vmCount"), 0)})</td></tr>')

    body = "".join(_row(r) for r in rows) + (_row(total, True) if total else "")
    return (f'<table style="margin-top:8px;"><thead><tr><th>{esc(group_label)}</th><th>CPU đầu kỳ → nay</th><th>Δ CPU</th>'
            f'<th>RAM đầu kỳ → nay</th><th>Δ RAM</th><th>Đỉnh CPU / RAM</th><th>Hosts</th><th>VMs</th></tr></thead><tbody>{body}</tbody></table>')


def _history_section_html(history: Optional[Dict[str, Any]], vm_changes: Optional[Dict[str, Any]] = None,
                          audience: str = "executive") -> str:
    """Section 'Biến động tài nguyên N tháng gần nhất'. Returns an explanatory
    empty-state when fewer than two snapshot days exist in the window."""
    months = (history or {}).get("months", 12)
    title = f"Biến động tài nguyên {months} tháng gần nhất"
    if not history or not history.get("available"):
        count = (history or {}).get("pointCount", 0)
        msg = ("Hệ thống mới có 1 ngày snapshot trong khoảng thời gian này — biểu đồ biến động sẽ xuất hiện từ lần đồng bộ tiếp theo."
               if count == 1 else "Chưa có snapshot nào trong khoảng thời gian này. Lịch sử được tích lũy tự động sau mỗi lần đồng bộ vCenter.")
        return ('<div class="section"><div class="section-title">' + esc(title) + '</div>'
                '<div class="assumption-note">' + esc(msg) + '</div></div>')
    group_label = "Cluster" if history.get("groupBy") == "cluster" else "vCenter"
    s = history["overall"]["summary"]
    cpu_dates, cpu_series = _history_series(history, "cpuPct")
    ram_dates, ram_series = _history_series(history, "ramPct")
    insight_html = "".join(f"<div style=\"margin-bottom:3px;\">{t}</div>" for t in _history_insights(history))
    vm_line = ""
    if vm_changes and vm_changes.get("available"):
        a, r = vm_changes["addedTotals"], vm_changes["removedTotals"]
        tail = " — danh sách chi tiết ở Phụ lục A." if audience == "technical" else " — danh sách chi tiết nằm trong phụ lục Báo cáo Kỹ thuật."
        vm_line = (f'<div class="small-note" style="margin-top:6px;">Biến động VM trong kỳ: <b style="color:{COLORS["ok"]};">+{a["count"]} thêm mới</b> '
                   f'({fmt_int(a["vcpu"])} vCPU · {fmt_int(a["ramGB"])} GB RAM) · <b style="color:{COLORS["warn"]};">−{r["count"]} loại bỏ</b> '
                   f'({fmt_int(r["vcpu"])} vCPU · {fmt_int(r["ramGB"])} GB RAM){esc(_vm_changes_excluded_note(vm_changes))}{tail}</div>')
    scope_clusters = history.get("clusters")
    scope_txt = ""
    if scope_clusters is not None:
        shown = ", ".join(scope_clusters[:4]) + (f" và {len(scope_clusters) - 4} cluster khác" if len(scope_clusters) > 4 else "")
        scope_txt = f" Phạm vi: cluster {shown}." if scope_clusters else " Phạm vi: không có cluster nào được chọn."
    sub = (f"Mỗi {group_label.lower()} được theo dõi như một nhóm dự án độc lập. "
           f"So sánh {_fmt_short_date(s.get('firstDate'))} → {_fmt_short_date(s.get('lastDate'))} qua {fmt_int(history.get('pointCount'))} ngày có snapshot; "
           f"% tính theo ngưỡng overcommit đang áp dụng, nên 100% = chạm chính sách, không phải hết tài nguyên vật lý.{scope_txt}")
    cpu_chart = svg_line_chart(cpu_dates, cpu_series, width=318, height=168)
    ram_chart = svg_line_chart(ram_dates, ram_series, width=318, height=168)
    return (
        '<div class="section">'
        f'<div class="section-title">{esc(title)}</div>'
        f'<div class="section-sub">{esc(sub)}</div>'
        '<div class="chart-row">'
        f'<div class="chart-card soft"><div class="chart-card-title">CPU — % ngưỡng overcommit theo {esc(group_label)}</div>{cpu_chart}</div>'
        f'<div class="chart-card soft"><div class="chart-card-title">RAM — % ngưỡng overcommit theo {esc(group_label)}</div>{ram_chart}</div>'
        '</div>'
        f'{_history_legend_html(history)}'
        f'{_history_table_html(history, group_label)}'
        f'<div class="insight-box" style="margin-top:8px;">{insight_html}{vm_line}</div>'
        '</div>')


EMPTY_CLUSTER_ROW = '<tr><td colspan="12" style="color:#98a2b3;">Không có cluster nào trong phạm vi.</td></tr>'


def _vm_changes_excluded_note(vm_changes: Dict[str, Any], compact: bool = False) -> str:
    """One short clause for the history summary lines: how many rows were
    recognised as moves / renames (not counted) and how many genuine resizes.
    ``compact`` keeps the Executive report inside its two-page budget."""
    bits = []
    ex = int(vm_changes.get("excludedFalsePositives") or 0)
    mt, rt, rs = vm_changes.get("movedTotals") or {}, vm_changes.get("renamedTotals") or {}, vm_changes.get("resizedTotals") or {}
    if compact:
        if ex:
            bits.append(f"đã loại {fmt_int(ex)} trường hợp di chuyển/đổi tên không đổi tài nguyên")
        if rs.get("count"):
            bits.append(f'{fmt_int(rs["count"])} VM đổi cấu hình ({fmt_signed(rs.get("deltaVcpu"))} vCPU · {fmt_signed(rs.get("deltaRamGB"))} GB)')
        return ("; " + "; ".join(bits)) if bits else ""
    if ex:
        parts = []
        if mt.get("count"):
            parts.append(f'{fmt_int(mt["count"])} VM di chuyển')
        if rt.get("vms"):
            parts.append(f'{fmt_int(rt["vms"])} VM đổi tên')
        if rt.get("clusters"):
            parts.append(f'{fmt_int(rt["clusters"])} cluster đổi tên')
        if rt.get("hosts"):
            parts.append(f'{fmt_int(rt["hosts"])} host đổi tên')
        bits.append("đã loại " + ", ".join(parts) + " khỏi thêm mới/loại bỏ vì không thay đổi tài nguyên")
    if rs.get("count"):
        bits.append(f'{fmt_int(rs["count"])} VM đổi cấu hình ({fmt_signed(rs.get("deltaVcpu"))} vCPU · {fmt_signed(rs.get("deltaRamGB"))} GB RAM)')
    return ("; " + "; ".join(bits)) if bits else ""


def fmt_signed(v: Any) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{'+' if n > 0 else ''}{fmt_int(n)}"


def _vm_changes_appendix_html(vm_changes: Optional[Dict[str, Any]]) -> str:
    """Appendix A — VM inventory changes per cluster (and per host when the
    report scope is narrowed by host): genuine add / remove, plus the moves,
    renames and resizes that were recognised and kept out of those counts."""
    months = (vm_changes or {}).get("months", 12)
    title = f"Phụ lục A — VM thêm mới / loại bỏ trong {months} tháng gần nhất"
    if not vm_changes or not vm_changes.get("available"):
        return ('<div class="section"><div class="section-title">' + esc(title) + '</div>'
                '<div class="assumption-note">Chưa đủ snapshot trong khoảng thời gian này để so sánh danh sách VM (cần ít nhất 2 lần đồng bộ thành công).</div></div>')

    def _date(v: Optional[str]) -> str:
        return parse_iso_to_vn_str(v, "%d/%m/%Y") if v else "—"

    def _pill(r: Dict[str, Any]) -> str:
        if r.get("isPoweredOn"):
            return f'<span class="pill" style="color:{COLORS["ok"]};border-color:{COLORS["ok"]};background:{COLORS["ok_bg"]};">Powered On</span>'
        return f'<span class="pill" style="color:{COLORS["muted"]};border-color:{COLORS["border"]};background:{COLORS["surface2"]};">{esc(r.get("powerState") or "Powered Off")}</span>'

    def _grouped(items: List[Dict[str, Any]], cols: int, render_row) -> str:
        if not items:
            return f'<tr><td colspan="{cols}" style="color:#98a2b3;">Không có VM nào.</td></tr>'
        out = []
        current_cluster = object()
        for r in items:
            # Group header per cluster so each project team's changes read as one block.
            if r.get("cluster") != current_cluster:
                current_cluster = r.get("cluster")
                n = sum(1 for x in items if x.get("cluster") == current_cluster)
                out.append(f'<tr class="group-row"><td colspan="{cols}"><b>Cluster {esc(current_cluster or "—")}</b> · {n} VM</td></tr>')
            out.append(render_row(r))
        return "".join(out)

    def _row_add_rm(date_field: str):
        def _r(r: Dict[str, Any]) -> str:
            return (f'<tr><td><b>{esc(r.get("vm"))}</b></td><td>{esc(r.get("vcenterLabel"))}</td><td>{esc(r.get("cluster") or "—")}</td>'
                    f'<td>{esc(r.get("host") or "—")}</td><td>{_pill(r)}</td><td>{fmt_int(r.get("vcpu"))}</td><td>{fmt_num(r.get("ramGB"), 0)}</td>'
                    f'<td>{esc(_date(r.get(date_field)))}</td></tr>')
        return _r

    def _row_moved(r: Dict[str, Any]) -> str:
        kind = "Giữa cluster" if r.get("kind") == "cluster" else "Giữa host (vMotion)"
        return (f'<tr><td><b>{esc(r.get("vm"))}</b></td><td>{esc(r.get("vcenterLabel"))}</td><td>{esc(kind)}</td>'
                f'<td>{esc(r.get("fromCluster") or "—")} → {esc(r.get("toCluster") or "—")}</td>'
                f'<td>{esc(r.get("fromHost") or "—")} → {esc(r.get("toHost") or "—")}</td>'
                f'<td>{_pill(r)}</td><td>{fmt_int(r.get("vcpu"))}</td><td>{fmt_num(r.get("ramGB"), 0)}</td></tr>')

    def _row_renamed(r: Dict[str, Any]) -> str:
        how = "cùng instanceUuid" if r.get("matchedBy") == "uuid" else "suy luận: cùng cluster, IP, vCPU, RAM"
        return (f'<tr><td>{esc(r.get("fromName"))}</td><td><b>{esc(r.get("toName"))}</b></td><td>{esc(r.get("vcenterLabel"))}</td>'
                f'<td>{esc(r.get("cluster") or "—")}</td><td>{esc(r.get("host") or "—")}</td>'
                f'<td>{fmt_int(r.get("vcpu"))}</td><td>{fmt_num(r.get("ramGB"), 0)}</td><td style="color:{COLORS["muted"]};">{esc(how)}</td></tr>')

    def _delta(v: Any) -> str:
        n = float(v or 0)
        color = COLORS["warn"] if n > 0 else (COLORS["ok"] if n < 0 else COLORS["muted"])
        return f'<span style="color:{color};font-weight:700;">{fmt_signed(n)}</span>'

    def _row_resized(r: Dict[str, Any]) -> str:
        return (f'<tr><td><b>{esc(r.get("vm"))}</b></td><td>{esc(r.get("vcenterLabel"))}</td><td>{esc(r.get("cluster") or "—")}</td>'
                f'<td>{esc(r.get("host") or "—")}</td><td>{_pill(r)}</td>'
                f'<td>{fmt_int(r.get("vcpuBefore"))} → {fmt_int(r.get("vcpuAfter"))} ({_delta(r.get("deltaVcpu"))})</td>'
                f'<td>{fmt_num(r.get("ramBefore"), 0)} → {fmt_num(r.get("ramAfter"), 0)} ({_delta(r.get("deltaRamGB"))})</td></tr>')

    a, rm = vm_changes["addedTotals"], vm_changes["removedTotals"]
    mv = vm_changes.get("movedTotals") or {"count": 0, "cluster": 0, "host": 0, "vcpu": 0, "ramGB": 0}
    rs = vm_changes.get("resizedTotals") or {"count": 0, "deltaVcpu": 0, "deltaRamGB": 0, "up": 0, "down": 0}
    rn = vm_changes.get("renamedTotals") or {"vms": 0, "clusters": 0, "hosts": 0}
    moved_rows = vm_changes.get("moved", [])
    renamed_rows = vm_changes.get("renamedVms", [])
    resized_rows = vm_changes.get("resized", [])
    sc_clusters, sc_hosts = vm_changes.get("clusters"), vm_changes.get("hosts")
    scope_bits = []
    if sc_clusters is not None:
        scope_bits.append(("cluster " + ", ".join(sc_clusters)) if sc_clusters else "không có cluster nào được chọn")
    if sc_hosts is not None:
        shown = ", ".join(sc_hosts[:4]) + (f" và {len(sc_hosts) - 4} host khác" if len(sc_hosts) > 4 else "")
        scope_bits.append(("host " + shown) if sc_hosts else "không có host nào được chọn")
    scope_note = (" Phạm vi: " + "; ".join(scope_bits) + ".") if scope_bits else ""
    runs_note = "; ".join(
        f'{sc["vcenterLabel"]}: {_date(sc.get("baselineAt"))} → {_date(sc.get("currentAt"))}'
        for sc in vm_changes.get("scopes", []) if sc.get("available"))
    identity = vm_changes.get("identity") or "name"
    identity_txt = {
        "uuid": "theo instanceUuid của vCenter (định danh ổn định, không đổi khi rename/vMotion/migrate)",
        "name": "theo tên VM (snapshot chưa có instanceUuid — sẽ tự chuyển sang UUID khi các snapshot mới đủ dữ liệu)",
        "mixed": "theo instanceUuid khi có, tên VM cho các snapshot cũ chưa có UUID",
    }.get(identity, identity)

    def _net(v: Any) -> str:
        v = int(v or 0)
        color = COLORS["ok"] if v > 0 else (COLORS["warn"] if v < 0 else COLORS["muted"])
        return f'<span style="color:{color};font-weight:700;">{"+" if v > 0 else ""}{v}</span>'

    def _cluster_cell(b: Dict[str, Any]) -> str:
        name = f'<b>{esc(b["cluster"])}</b>'
        if b.get("renamedFrom"):
            name += f'<br/><span style="color:{COLORS["muted"]};font-size:7.5px;">trước: {esc(b["renamedFrom"])}</span>'
        return name

    cluster_rows = "".join(
        f'<tr><td>{_cluster_cell(b)}</td><td>{esc(b["vcenterLabel"])}</td>'
        f'<td>{fmt_int(b["baselineVmCount"])}</td><td>{fmt_int(b["currentVmCount"])}</td><td>{_net(b["netVm"])}</td>'
        f'<td style="color:{COLORS["ok"]};font-weight:700;">+{fmt_int(b["added"])}</td><td>{fmt_int(b["addedVcpu"])} / {fmt_int(b["addedRamGB"])}</td>'
        f'<td style="color:{COLORS["warn"]};font-weight:700;">−{fmt_int(b["removed"])}</td><td>{fmt_int(b["removedVcpu"])} / {fmt_int(b["removedRamGB"])}</td>'
        f'<td>{fmt_int(b.get("movedIn"))} / {fmt_int(b.get("movedOut"))}</td><td>{fmt_int(b.get("renamed"))}</td>'
        f'<td>{fmt_int(b.get("resized"))}: {fmt_signed(b.get("resizedVcpuDelta"))} / {fmt_signed(b.get("resizedRamDelta"))}</td></tr>'
        for b in vm_changes.get("clusterSummary", []))
    cluster_table = (
        '<table class="appendix-table summary"><thead><tr><th>Cluster</th><th>vCenter</th><th>VM đầu kỳ</th><th>VM hiện tại</th><th>Chênh lệch</th>'
        '<th>Thêm mới</th><th>vCPU / GB RAM thêm</th><th>Loại bỏ</th><th>vCPU / GB RAM bỏ</th><th>Chuyển vào / ra</th><th>Đổi tên VM</th><th>Đổi cấu hình: Δ vCPU / Δ GB</th></tr></thead>'
        f'<tbody>{cluster_rows or EMPTY_CLUSTER_ROW}</tbody></table>')

    host_table = ""
    if vm_changes.get("hostSummary"):
        host_rows = "".join(
            f'<tr><td><b>{esc(b["host"])}</b></td><td>{esc(b["cluster"])}</td><td>{esc(b["vcenterLabel"])}</td>'
            f'<td>{fmt_int(b["baselineVmCount"])}</td><td>{fmt_int(b["currentVmCount"])}</td><td>{_net(b["netVm"])}</td>'
            f'<td style="color:{COLORS["ok"]};font-weight:700;">+{fmt_int(b["added"])}</td><td>{fmt_int(b["addedVcpu"])} / {fmt_int(b["addedRamGB"])}</td>'
            f'<td style="color:{COLORS["warn"]};font-weight:700;">−{fmt_int(b["removed"])}</td><td>{fmt_int(b["removedVcpu"])} / {fmt_int(b["removedRamGB"])}</td>'
            f'<td>{fmt_int(b.get("movedIn"))} / {fmt_int(b.get("movedOut"))}</td></tr>'
            for b in vm_changes["hostSummary"])
        host_table = (
            '<div class="section-title" style="font-size:10.5px;border-left:none;padding-left:0;margin-top:12px;">Theo host (phạm vi host đã chọn)</div>'
            '<table class="appendix-table summary"><thead><tr><th>Host</th><th>Cluster</th><th>vCenter</th><th>VM đầu kỳ</th><th>VM hiện tại</th><th>Chênh lệch</th>'
            '<th>Thêm mới</th><th>vCPU / GB RAM thêm</th><th>Loại bỏ</th><th>vCPU / GB RAM bỏ</th><th>Chuyển vào / ra (vMotion, migrate)</th></tr></thead>'
            f'<tbody>{host_rows}</tbody></table>')

    # Renamed clusters / hosts: one compact note so the reader knows why the
    # old name is absent from the tables.
    rename_note = ""
    rc, rh = vm_changes.get("renamedClusters", []), vm_changes.get("renamedHosts", [])
    if rc or rh:
        items = [f'Cluster <b>{esc(r["from"])}</b> → <b>{esc(r["to"])}</b> ({esc(r["vcenterLabel"])}; trùng {int(round((r.get("overlap") or 0) * 100))}% {"host" if r.get("matchedBy") == "host" else "VM"})' for r in rc]
        items += [f'Host <b>{esc(r["from"])}</b> → <b>{esc(r["to"])}</b> (cluster {esc(r["cluster"])}, {esc(r["vcenterLabel"])}; trùng {int(round((r.get("overlap") or 0) * 100))}% VM)' for r in rh]
        rename_note = ('<div class="assumption-note" style="margin-top:6px;"><b>Đổi tên cluster/host được nhận diện trong kỳ</b> — số liệu đầu kỳ đã được gộp về tên hiện tại, '
                       'không tính là loại bỏ/thêm mới: ' + "; ".join(items) + '.</div>')

    excluded = int(vm_changes.get("excludedFalsePositives") or 0)
    excluded_txt = (f' Kỳ này đã loại <b>{fmt_int(excluded)}</b> trường hợp như vậy khỏi số thêm mới/loại bỏ.' if excluded else
                    ' Kỳ này không phát hiện trường hợp nào như vậy.')

    header_add_rm = ("<tr><th>VM</th><th>vCenter</th><th>Cluster</th><th>Host</th><th>Trạng thái</th><th>vCPU</th><th>RAM (GB)</th>"
                     "<th style=\"white-space:nowrap;\">{}</th></tr>")
    sub_title_style = 'style="font-size:10.5px;border-left:none;padding-left:0;margin-top:12px;"'
    return (
        '<div class="section breakable" style="break-before:page;">'
        f'<div class="section-title">{esc(title)}</div>'
        '<div class="section-sub">So sánh danh sách VM của từng cluster giữa snapshot đầu kỳ và snapshot mới nhất, đối chiếu '
        f'{esc(identity_txt)}.'
        f'{esc(scope_note)}<br/>Snapshot đối chiếu — {esc(runs_note)}.</div>'
        '<div class="assumption-note"><b>Điều kiện nhận diện.</b> '
        '<b>Thêm mới</b> = định danh VM chưa có ở bất kỳ cluster/host nào của vCenter tại đầu kỳ. '
        '<b>Loại bỏ</b> = định danh VM không còn ở bất kỳ cluster/host nào của vCenter hiện tại. '
        'Các trường hợp sau <b>không</b> tính là thêm mới/loại bỏ vì không phát sinh hay giảm tài nguyên: '
        '(1) vMotion giữa host trong cùng cluster; (2) migrate VM từ cluster này sang cluster khác — ghi nhận ở A.3; '
        '(3) đổi tên cluster/host — nhận diện khi cluster/host biến mất và cluster/host mới xuất hiện có ≥ 50% host (hoặc VM) trùng nhau, số liệu đầu kỳ được gộp về tên hiện tại; '
        '(4) đổi tên VM — cùng instanceUuid, hoặc khi chưa có UUID: cùng cluster, IP, vCPU và RAM — ghi nhận ở A.4. '
        'VM đổi cấu hình vCPU/RAM (A.5) được tách riêng vì có làm thay đổi tài nguyên đã cấp.'
        f'{excluded_txt}</div>'
        f'{rename_note}'
        '<div class="section-title" ' + sub_title_style + '>Tổng hợp theo cluster</div>'
        f'{cluster_table}{host_table}'
        f'<div class="section-title" {sub_title_style}>A.1 VM thêm mới ({fmt_int(a["count"])} VM · {fmt_int(a["vcpu"])} vCPU · {fmt_int(a["ramGB"])} GB RAM · {fmt_int(a["poweredOn"])} đang bật)</div>'
        f'<table class="appendix-table"><thead>{header_add_rm.format("Xuất hiện từ")}</thead><tbody>{_grouped(vm_changes.get("added", []), 8, _row_add_rm("firstSeen"))}</tbody></table>'
        f'<div class="section-title" {sub_title_style}>A.2 VM đã loại bỏ ({fmt_int(rm["count"])} VM · {fmt_int(rm["vcpu"])} vCPU · {fmt_int(rm["ramGB"])} GB RAM)</div>'
        f'<table class="appendix-table"><thead>{header_add_rm.format("Thấy lần cuối")}</thead><tbody>{_grouped(vm_changes.get("removed", []), 8, _row_add_rm("lastSeen"))}</tbody></table>'
        f'<div class="section-title" {sub_title_style}>A.3 VM di chuyển — không tính thêm mới/loại bỏ ({fmt_int(mv["count"])} VM: {fmt_int(mv.get("cluster"))} giữa cluster · {fmt_int(mv.get("host"))} giữa host · {fmt_int(mv["vcpu"])} vCPU · {fmt_int(mv["ramGB"])} GB RAM)</div>'
        '<table class="appendix-table moved"><thead><tr><th>VM</th><th>vCenter</th><th>Loại</th><th>Cluster (từ → đến)</th><th>Host (từ → đến)</th><th>Trạng thái</th><th>vCPU</th><th>RAM (GB)</th></tr></thead>'
        f'<tbody>{_grouped(moved_rows, 8, _row_moved)}</tbody></table>'
        f'<div class="section-title" {sub_title_style}>A.4 VM đổi tên — không tính thêm mới/loại bỏ ({fmt_int(rn.get("vms"))} VM)</div>'
        '<table class="appendix-table renamed"><thead><tr><th>Tên cũ</th><th>Tên mới</th><th>vCenter</th><th>Cluster</th><th>Host</th><th>vCPU</th><th>RAM (GB)</th><th>Căn cứ</th></tr></thead>'
        f'<tbody>{_grouped(renamed_rows, 8, _row_renamed)}</tbody></table>'
        f'<div class="section-title" {sub_title_style}>A.5 VM đổi cấu hình vCPU/RAM ({fmt_int(rs["count"])} VM · {fmt_int(rs.get("up"))} tăng · {fmt_int(rs.get("down"))} giảm · tổng {fmt_signed(rs.get("deltaVcpu"))} vCPU · {fmt_signed(rs.get("deltaRamGB"))} GB RAM)</div>'
        '<table class="appendix-table resized"><thead><tr><th>VM</th><th>vCenter</th><th>Cluster</th><th>Host</th><th>Trạng thái</th><th>vCPU (đầu kỳ → nay)</th><th>RAM GB (đầu kỳ → nay)</th></tr></thead>'
        f'<tbody>{_grouped(resized_rows, 7, _row_resized)}</tbody></table>'
        '</div>')


def _base_css(report_title: str, generated_at: str) -> str:
    return f"""
@page {{
  size: A4;
  margin: 16mm 14mm 18mm 14mm;
  @top-left {{ content: "VMware Capacity Ops Management"; font-family:'Roboto','Noto Sans',sans-serif; font-size:8.5px; color:{COLORS['faint']}; letter-spacing:.04em; text-transform:uppercase; }}
  @top-right {{ content: "{report_title}"; font-family:'Roboto','Noto Sans',sans-serif; font-size:8.5px; color:{COLORS['faint']}; letter-spacing:.04em; text-transform:uppercase; }}
  @bottom-left {{ content: "Tạo lúc {generated_at}"; font-family:'Roboto','Noto Sans',sans-serif; font-size:8px; color:{COLORS['faint']}; }}
  @bottom-right {{ content: "Trang " counter(page) " / " counter(pages); font-family:'Roboto','Noto Sans',sans-serif; font-size:8px; color:{COLORS['faint']}; }}
}}
* {{ box-sizing:border-box; }}
body {{ font-family:'Roboto','Noto Sans',sans-serif; color:{COLORS['ink']}; font-size:10.3px; line-height:1.5; }}
h1,h2,h3,h4 {{ font-family:'Roboto','Noto Sans',sans-serif; font-weight:700; color:{COLORS['ink']}; margin:0; }}
.cover-title {{ font-size:22px; font-weight:800; color:{COLORS['primary_dark']}; margin-bottom:2px; }}
.cover-sub {{ font-size:12px; color:{COLORS['muted']}; margin-bottom:14px; }}
.eyebrow {{ font-size:8.5px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{COLORS['accent']}; margin-bottom:4px; }}
.meta-row {{ display:flex; gap:18px; font-size:9px; color:{COLORS['muted']}; margin-bottom:16px; flex-wrap:wrap; }}
.meta-row b {{ color:{COLORS['ink']}; }}
.section {{ margin-top:16px; break-inside:avoid; }}
.section-title {{ font-size:13px; font-weight:700; color:{COLORS['ink']}; border-left:3px solid {COLORS['primary']}; padding-left:8px; margin-bottom:8px; break-after:avoid; page-break-after:avoid; }}
.section-sub {{ font-size:9px; color:{COLORS['muted']}; margin:-4px 0 8px 11px; }}
.kpi-grid {{ display:flex; gap:8px; margin-bottom:4px; }}
.kpi-card {{ flex:1; background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-radius:6px; padding:9px 11px; }}
.kpi-label {{ font-size:8px; text-transform:uppercase; letter-spacing:.06em; color:{COLORS['muted']}; font-weight:700; margin-bottom:4px; }}
.kpi-value {{ font-size:16px; font-weight:800; color:{COLORS['ink']}; }}
.kpi-sub {{ font-size:8.5px; color:{COLORS['muted']}; margin-top:3px; }}
table {{ width:100%; border-collapse:collapse; font-size:9px; }}
thead {{ display:table-header-group; }}
tr {{ break-inside:avoid; }}
th {{ text-align:left; background:{COLORS['surface2']}; color:{COLORS['muted']}; font-size:7.8px; text-transform:uppercase; letter-spacing:.05em; font-weight:700; padding:5px 6px; border-bottom:1px solid {COLORS['border']}; }}
td {{ padding:4.5px 6px; border-bottom:1px solid {COLORS['divider']}; vertical-align:middle; }}
tbody tr:nth-child(even) {{ background:#fbfcfe; }}
.pill {{ display:inline-block; padding:1.5px 7px; border-radius:3px; font-size:8px; font-weight:700; border:1px solid; white-space:nowrap; }}
.insight-box {{ background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-left:3px solid {COLORS['accent']}; border-radius:0 6px 6px 0; padding:9px 12px; margin-bottom:8px; font-size:9.5px; }}
.insight-box b {{ color:{COLORS['primary_dark']}; }}
.insight-box.risk {{ border-left-color:{COLORS['crit']}; }}
.two-col {{ display:flex; gap:14px; }}
.two-col > div {{ flex:1; }}
.legend {{ display:flex; gap:12px; flex-wrap:wrap; font-size:8.5px; color:{COLORS['muted']}; margin-top:6px; }}
.legend .dot {{ width:7px; height:7px; border-radius:2px; display:inline-block; margin-right:4px; vertical-align:middle; }}
.small-note {{ font-size:8px; color:{COLORS['faint']}; margin-top:6px; }}
.chip {{ display:inline-block; padding:1px 6px; border-radius:99px; background:{COLORS['surface2']}; color:{COLORS['muted']}; font-size:8px; font-weight:600; margin-right:4px; }}
.cover-header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:14px; }}
.cover-logo {{ max-height:42px; max-width:150px; object-fit:contain; }}
.gauge-row {{ display:flex; gap:6px; margin-bottom:10px; }}
.gauge-card {{ flex:1; background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-radius:6px; padding:8px 4px 4px; text-align:center; }}
.chart-row {{ display:flex; gap:14px; align-items:flex-start; }}
.chart-card {{ flex:1; background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-radius:6px; padding:10px 12px; }}
.chart-card-title {{ font-size:9.5px; font-weight:700; color:{COLORS['ink']}; margin-bottom:4px; }}
.headroom-card {{ flex:1; background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-radius:6px; padding:9px 11px; }}
.headroom-metric {{ font-size:8px; text-transform:uppercase; letter-spacing:.06em; color:{COLORS['muted']}; font-weight:700; margin-bottom:5px; }}
.headroom-value {{ font-size:15px; font-weight:800; color:{COLORS['primary_dark']}; }}
.headroom-value span {{ font-size:9px; font-weight:600; color:{COLORS['muted']}; }}
.headroom-sub {{ font-size:8.3px; color:{COLORS['muted']}; margin-top:3px; }}
.priority-pill {{ display:inline-block; padding:1.5px 7px; border-radius:3px; font-size:7.8px; font-weight:700; white-space:nowrap; }}
.priority-cao {{ background:{COLORS['crit_bg']}; color:{COLORS['crit']}; }}
.priority-tb {{ background:{COLORS['warn_bg']}; color:{COLORS['warn']}; }}
.priority-thap {{ background:{COLORS['ok_bg']}; color:{COLORS['ok']}; }}
.priority-info {{ background:{COLORS['surface2']}; color:{COLORS['primary_dark']}; }}
.section.breakable {{ break-inside:auto; }}
.assumption-note {{ background:{COLORS['surface2']}; border:1px dashed {COLORS['border']}; border-radius:5px; padding:6px 10px; font-size:8px; color:{COLORS['muted']}; margin-top:8px; }}
.radar-legend-item {{ display:flex; align-items:center; gap:5px; font-size:8.3px; color:{COLORS['ink']}; margin-bottom:3px; }}
.radar-swatch {{ width:9px; height:9px; border-radius:2px; display:inline-block; flex:none; }}
.exec-brief {{ border:1px solid {COLORS['border']}; border-radius:8px; padding:11px 14px 10px; margin:2px 0 4px; background:{COLORS['surface']}; border-left:5px solid {COLORS['primary']}; }}
.exec-brief.ok {{ background:#f2faf5; border-left-color:{COLORS['ok']}; }}
.exec-brief.warn {{ background:#fff8ec; border-left-color:{COLORS['warn']}; }}
.exec-brief.bad {{ background:#fff3ea; border-left-color:{COLORS['bad']}; }}
.exec-brief.crit {{ background:#fdf0ef; border-left-color:{COLORS['crit']}; }}
.exec-brief-head {{ display:flex; align-items:center; gap:8px; margin-bottom:5px; }}
.exec-brief-title {{ font-size:8.5px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{COLORS['muted']}; }}
.exec-brief-headline {{ font-size:12.5px; font-weight:700; color:{COLORS['ink']}; line-height:1.4; margin-bottom:6px; }}
.brief-list {{ margin:0 0 6px 0; padding-left:16px; font-size:9.6px; line-height:1.5; }}
.brief-list li {{ margin-bottom:2px; }}
.exec-brief-decision {{ font-size:9.6px; padding-top:6px; border-top:1px dashed {COLORS['border']}; color:{COLORS['ink']}; }}
.exec-brief-decision b {{ color:{COLORS['primary_dark']}; }}
.rec-card {{ border:1px solid {COLORS['border']}; border-left:4px solid {COLORS['primary']}; border-radius:0 7px 7px 0; padding:8px 11px 7px; margin-bottom:7px; background:{COLORS['surface']}; break-inside:avoid; }}
.rec-card.prio-cao {{ border-left-color:{COLORS['crit']}; background:#fdf5f4; }}
.rec-card.prio-tb {{ border-left-color:{COLORS['warn']}; background:#fffaf1; }}
.rec-card.tone-opportunity {{ border-left-color:{COLORS['accent']}; background:#f0f9fb; }}
.rec-card.tone-ok {{ border-left-color:{COLORS['ok']}; background:#f2faf5; }}
.rec-head {{ display:flex; align-items:center; gap:7px; margin-bottom:5px; }}
.rec-num {{ flex:none; width:16px; height:16px; border-radius:50%; background:{COLORS['ink']}; color:#fff; font-size:8.5px; font-weight:800; text-align:center; line-height:16px; }}
.rec-title {{ flex:1; font-size:10.6px; font-weight:700; color:{COLORS['ink']}; }}
.rec-grid {{ display:flex; gap:10px; font-size:8.9px; line-height:1.45; color:{COLORS['ink']}; }}
.rec-cell {{ flex:1; background:rgba(255,255,255,.7); border:1px solid {COLORS['divider']}; border-radius:5px; padding:5px 7px; }}
.rec-label {{ font-size:7.4px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:{COLORS['muted']}; margin-bottom:2px; }}
.rec-decision {{ margin-top:5px; font-size:9px; color:{COLORS['ink']}; }}
.rec-decision b {{ color:{COLORS['primary_dark']}; }}
.rec-refs {{ margin-top:4px; padding-top:4px; border-top:1px dashed {COLORS['divider']}; font-size:8px; color:{COLORS['muted']}; line-height:1.55; }}
.rec-refs b {{ color:{COLORS['primary_dark']}; margin-right:3px; }}
.ref-item {{ display:inline-block; margin:0 8px 1px 0; color:{COLORS['ink']}; }}
.ref-item.muted {{ color:{COLORS['muted']}; font-style:italic; }}
.ref-code {{ font-family:'DejaVu Sans Mono', monospace; font-weight:700; font-size:7.5px; padding:0 4px; border-radius:3px; background:{COLORS['surface2']}; border:1px solid {COLORS['divider']}; color:{COLORS['primary_dark']}; }}
.ref-note {{ margin-top:2px; color:{COLORS['muted']}; }}
.ref-codes-inline {{ margin-top:2px; font-size:7.5px; color:{COLORS['muted']}; font-family:'DejaVu Sans Mono', monospace; }}
td.code-cell {{ font-family:'DejaVu Sans Mono', monospace; font-size:7.5px; font-weight:700; color:{COLORS['primary_dark']}; white-space:nowrap; }}
.sign-box {{ display:inline-block; width:64px; height:16px; border:1px dashed {COLORS['border']}; border-radius:3px; }}
.gauge-card.ok {{ background:#f2faf5; }}
.gauge-card.warn {{ background:#fff8ec; }}
.gauge-card.bad {{ background:#fff3ea; }}
.gauge-card.crit {{ background:#fdf0ef; }}
.gauge-card.na {{ background:{COLORS['surface2']}; }}
.gauge-caption {{ font-size:8.3px; color:{COLORS['muted']}; margin-top:1px; }}
.chart-card.soft {{ background:{COLORS['surface']}; }}
.appendix-table td:nth-child(-n+3), .appendix-table td:nth-child(n+5) {{ white-space:nowrap; }}
.appendix-table td:nth-child(4) {{ word-break:break-all; }}
.appendix-table.moved td:nth-child(4), .appendix-table.moved td:nth-child(5),
.appendix-table.renamed td:nth-child(5), .appendix-table.renamed td:nth-child(8),
.appendix-table.summary td:nth-child(1) {{ white-space:normal; word-break:break-all; }}
.appendix-table.summary th, .appendix-table.summary td {{ font-size:7.6px; }}
.appendix-table tr.group-row td {{ background:{COLORS['surface2']}; white-space:normal; padding:3px 6px; font-size:8.6px; }}
.appendix-table td:last-child {{ white-space:normal; }}
"""


def _now_str() -> str:
    # Container clocks commonly run in UTC; PDF reports are read by staff in
    # Vietnam, so the "generated at" stamp is rendered in Vietnam local time.
    return now_vn_str()


def _overall_status(model: Dict[str, Any]) -> Dict[str, str]:
    cpu_status = capacity.get_status_for_pct(model.get("cpuPct"), 80, 100)
    ram_status = capacity.get_status_for_pct(model.get("ramPct"), 80, 100)
    return capacity.combine_status(cpu_status, ram_status)


def _severity_counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        out[f.get("severity", "info")] = out.get(f.get("severity", "info"), 0) + 1
    return out


PRIORITY_LABEL_VI = {"cao": "Ưu tiên cao", "tb": "Ưu tiên trung bình", "thap": "Ưu tiên thấp", "info": "Thông tin"}
PRIORITY_ORDER = {"cao": 0, "tb": 1, "thap": 2, "info": 3}


def _quarter_label(months_ahead: Optional[float]) -> str:
    """'Q3/2027'-style label for a point `months_ahead` months from now."""
    if months_ahead is None or not _finite(months_ahead):
        return ""
    today = dt.date.today()
    total = today.year * 12 + (today.month - 1) + int(round(months_ahead))
    y, m = divmod(total, 12)
    return f"Q{m // 3 + 1}/{y}"


def _runway_phrase(months: Optional[float]) -> str:
    if months is None:
        return "chưa xác định được (mức sử dụng hiện rất thấp hoặc không tăng)"
    if months <= 0.05:
        return "đã chạm ngưỡng ngay tại thời điểm báo cáo"
    if months < 1:
        return "dưới 1 tháng"
    if months > 36:
        return "trên 3 năm"
    return f"khoảng {months:.0f} tháng (≈ {_quarter_label(months)})"


def _scenario_months(growth: Dict[str, Any], key: str, growth_pct: float = 10.0) -> Optional[float]:
    for sc in growth.get(key, {}).get("scenarios", []):
        if abs(sc.get("growthPct", -1) - growth_pct) < 1e-6:
            return sc.get("months")
    return None


def _reclaimable_from_findings(findings: List[Dict[str, Any]], vms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum the vCPU / RAM footprint of VMs flagged idle or powered-off long
    term, so the reclaim opportunity can be expressed in concrete units."""
    names = {f.get("entity") for f in findings if f.get("type") in ("vm_idle", "vm_off")}
    vcpu = ram = 0.0
    n = 0
    for v in vms:
        if v.get("vm") in names:
            n += 1
            vcpu += v.get("vcpu", 0) or 0
            ram += v.get("ramGB", 0) or 0
    return {"count": n, "vcpu": vcpu, "ramGB": ram}


def _management_brief(model: Dict[str, Any], cluster_rows: List[Dict[str, Any]], findings: List[Dict[str, Any]],
                      policy: capacity.Policy, vms: List[Dict[str, Any]], hosts: List[Dict[str, Any]],
                      headroom: Dict[str, Any], growth: Dict[str, Any], ha_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Plain-language management brief: a 30-second summary, a ranked list of
    recommendations (what it means → what we propose → what happens if we
    wait → the decision requested), and the derived decision register.
    Written for leadership review, not for operators: business impact,
    risk and timing first; technical detail only in parentheses."""
    include_storage = bool(getattr(policy, "include_storage", False))
    cpu_pct, ram_pct, storage_pct = model.get("cpuPct"), model.get("ramPct"), model.get("storagePct")
    sev = _severity_counts(findings)
    recs: List[Dict[str, Any]] = []

    # --- binding resource & runway (10 %/quarter = "moderate" scenario) ---
    cpu_m, ram_m = _scenario_months(growth, "cpu"), _scenario_months(growth, "ram")
    binding = "RAM" if (_finite(ram_pct) and (not _finite(cpu_pct) or ram_pct >= cpu_pct)) else "CPU"
    binding_pct = ram_pct if binding == "RAM" else cpu_pct
    binding_months = ram_m if binding == "RAM" else cpu_m
    binding_fit = headroom["ram"]["vmsFit"] if binding == "RAM" else headroom["cpu"]["vmsFit"]

    # --- overall health headline ---
    worst = max(cpu_pct if _finite(cpu_pct) else 0, ram_pct if _finite(ram_pct) else 0)
    # Same status vocabulary/thresholds as the "Trạng thái tổng thể" pill
    # (_overall_status) so the brief never contradicts the header badge.
    overall = _overall_status(model)
    health_cls, health_word = overall["cls"], overall["text"]
    if health_cls == "crit":
        headline = f"Hạ tầng đang chạy vượt ngưỡng an toàn ở {binding} — cần quyết định bổ sung năng lực ngay trong quý này."
    elif health_cls == "bad":
        headline = f"Hạ tầng đã chạm ngưỡng cho phép ở {binding} ({binding_pct:.0f}%) — cần phê duyệt kế hoạch mở rộng ngay trong quý này."
    elif health_cls == "warn":
        headline = f"Hạ tầng vẫn an toàn nhưng {binding} đã dùng {binding_pct:.0f}% ngưỡng cho phép — nên đưa mở rộng vào kế hoạch ngân sách kỳ tới."
    else:
        headline = "Hạ tầng đang vận hành ổn định, còn dư địa tốt — chưa cần đầu tư phần cứng mới tại thời điểm này."

    # --- HA ---
    ha_eligible = [r for r in ha_rows if r.get("eligible")]
    ha_fail = [r for r in ha_eligible if not r.get("survives")]

    # --- reclaim opportunity ---
    reclaim = _reclaimable_from_findings(findings, vms)
    avg_host_ram = (model["totalRamGB"] / model["hostCount"]) if model.get("hostCount") else 0
    host_equiv = (reclaim["ramGB"] / avg_host_ram) if avg_host_ram > 0 else 0
    reclaim_pct_ram = (reclaim["ramGB"] / (model["totalRamGB"] * policy.ram_overcommit_max) * 100) if model.get("totalRamGB") else 0

    # --- concrete extra capacity needed for clusters over policy ---
    actions = _technical_headroom_actions(cluster_rows, policy)
    extra_cores = sum(a["extraPhysCores"] for a in actions)
    extra_ram = sum(a["extraRamGB"] for a in actions)

    def refs_from(items: List[Dict[str, Any]], limit: int = 8) -> Dict[str, Any]:
        """Reconciliation references for a recommendation: finding codes with
        the object and the measured value, so a reader can trace each headline
        number to a concrete row in the findings tables (Executive + Technical)."""
        items = sorted(items, key=lambda f: ({"critical": 0, "warning": 1, "info": 2}.get(f.get("severity"), 3),
                                             str(f.get("cluster") or ""), str(f.get("entity") or "")))
        return {"total": len(items),
                "items": [{"code": f.get("code"), "entity": f.get("entity"), "cluster": f.get("cluster"),
                           "vcenterKey": f.get("vcenterKey") or "", "typeLabel": f.get("typeLabel"),
                           "metric": f.get("metric"), "severity": f.get("severity")} for f in items[:limit]]}

    def add(priority, title, meaning, action, impact, decision, timeframe, tone="risk", refs=None, ref_note=None):
        if refs and refs.get("total", 0) > 6 and priority in ("thap", "info"):
            refs = {**refs, "items": refs["items"][:6], "compact": True}
        recs.append({"priority": priority, "title": title, "meaning": meaning, "action": action,
                     "impact": impact, "decision": decision, "timeframe": timeframe, "tone": tone,
                     "refs": refs or {"total": 0, "items": []}, "refNote": ref_note or ""})

    by_type = lambda *types: [f for f in findings if f.get("type") in types]

    # 1) Over policy right now
    for label, pct, oc, oc_max, key in (("CPU", cpu_pct, model.get("cpuOvercommit"), policy.cpu_overcommit_max, "cpu"),
                                        ("RAM", ram_pct, model.get("ramOvercommit"), policy.ram_overcommit_max, "ram")):
        if not _finite(pct):
            continue
        if pct >= 100:
            need = (f"khoảng +{extra_cores:.0f} core vật lý" if key == "cpu" and extra_cores > 0.5 else
                    f"khoảng +{extra_ram:,.0f} GB RAM" if key == "ram" and extra_ram > 0.5 else "bổ sung host")
            add("cao", f"Bổ sung năng lực {label} ngay — hệ thống đang chạy vượt ngưỡng an toàn",
                f"{label} đang được cấp phát gấp {oc:.2f} lần năng lực vật lý, vượt mức tối đa {oc_max:.1f}x mà chúng ta đã thống nhất. "
                f"Nói đơn giản: các máy chủ ảo đang 'chia sẻ' {label} nhiều hơn mức an toàn, hiệu năng ứng dụng có thể chậm bất kỳ lúc nào khi tải tăng.",
                f"Bổ sung {need} cho các cluster đang vượt ngưỡng, đồng thời IT cân bằng lại tải và thu hồi VM không dùng để giảm áp lực tức thời.",
                "Ứng dụng nghiệp vụ chậm hoặc gián đoạn vào giờ cao điểm; không còn chỗ để khôi phục khi một host gặp sự cố.",
                f"Phê duyệt ngân sách mở rộng {label} khẩn (mức ưu tiên cao nhất).", "Trong quý này",
                refs=refs_from(by_type(f"overcommit_{key}", f"host_{key}")),
                ref_note=f"Cluster/host đang vượt ngưỡng {label}")
        elif pct >= 80:
            months = cpu_m if key == "cpu" else ram_m
            add("tb", f"Lên kế hoạch mở rộng {label} trong 1–2 quý tới",
                f"{label} đã dùng {pct:.0f}% ngưỡng cho phép. Với tốc độ tăng trưởng vừa phải (10%/quý), dự kiến chạm ngưỡng sau {_runway_phrase(months)}.",
                f"Đưa hạng mục bổ sung host / {label} vào kế hoạch ngân sách kỳ tới; IT chuẩn bị cấu hình và báo giá để sẵn sàng triển khai khi được duyệt.",
                "Nếu chờ đến khi chạm ngưỡng mới mua sắm, thời gian đặt hàng – lắp đặt (thường 8–12 tuần) sẽ khiến hệ thống phải chạy quá tải trong lúc chờ.",
                f"Đồng ý chủ trương mở rộng {label}; giao IT lập dự toán chi tiết.", "1–2 quý tới",
                refs=refs_from(by_type(f"overcommit_{key}", f"host_{key}")),
                ref_note=f"Cluster/host đang tiến gần ngưỡng {label}")

    # 2) Storage (only when assessed)
    if include_storage and _finite(storage_pct) and storage_pct >= 90:
        add("cao", "Dung lượng lưu trữ đã cấp phát gần đầy",
            f"Đã sử dụng {storage_pct:.0f}% dung lượng cấp phát cho các datastore. Khi datastore đầy, VM có thể bị tạm dừng đột ngột.",
            "Dọn snapshot cũ, rà soát VM cấp phát thừa (thin-provisioning) trước; nếu vẫn thiếu thì bổ sung dung lượng.",
            "Rủi ro dừng dịch vụ ngoài kế hoạch do datastore đầy.",
            "Phê duyệt bổ sung dung lượng lưu trữ nếu sau rà soát vẫn thiếu.", "Trong quý này")

    # 3) Risky clusters
    risky = [c for c in cluster_rows if c["overall"]["cls"] in ("bad", "crit")]
    if risky:
        names = ", ".join(c["name"] for c in risky[:5])
        add("cao" if any(c["overall"]["cls"] == "crit" for c in risky) else "tb",
            f"{len(risky)} cluster cần được ưu tiên bổ sung host",
            f"Các cluster {names} đang ở mức cảnh báo/vượt ngưỡng trong khi các cluster còn lại vẫn thoải mái. Đây là điểm nghẽn cục bộ, không phải toàn hệ thống.",
            "Ưu tiên phân bổ host mới (hoặc điều chuyển VM sang cluster còn dư) cho các cluster này trước khi mở rộng chung.",
            "Ứng dụng đặt trên các cluster này chịu rủi ro hiệu năng cao hơn phần còn lại của hệ thống.",
            "Đồng ý ưu tiên ngân sách/host cho các cluster nêu trên.", "Quý này",
            refs=refs_from([f for f in by_type("overcommit_cpu", "overcommit_ram", "host_cpu", "host_ram")
                            if f.get("cluster") in {c["name"] for c in risky}]),
            ref_note="Cluster: " + names)

    # 4) HA / N+1
    if ha_eligible and ha_fail:
        names = ", ".join(r["cluster"] for r in ha_fail[:6])
        add("cao", f"{len(ha_fail)}/{len(ha_eligible)} cluster không còn dự phòng khi mất 1 host",
            f"Nếu một host trong cluster {names} hỏng hoặc phải bảo trì, số host còn lại sẽ không đủ chỗ cho toàn bộ máy chủ ảo — tức là không còn 'ghế dự phòng'.",
            "Bổ sung 1 host dự phòng cho từng cluster nêu trên, hoặc giảm tải bằng cách thu hồi/điều chuyển VM.",
            "Một sự cố phần cứng đơn lẻ có thể gây gián đoạn dịch vụ thay vì được hệ thống tự động hấp thụ.",
            "Phê duyệt bổ sung host dự phòng (N+1) cho các cluster nêu trên.", "Trong 1 quý",
            ref_note="Cluster: " + names + " — chi tiết tại bảng 'Dự phòng sự cố (N+1)' của Báo cáo Kỹ thuật")

    # 5) Critical operational findings
    if sev["critical"] > 0:
        crit = [f for f in findings if f.get("severity") == "critical"]
        kinds = sorted({f.get("typeLabel") for f in crit if f.get("typeLabel")})
        add("tb", f"{sev['critical']} điểm cần IT xử lý ngay ở cấp vận hành",
            f"Đây là các cảnh báo mức nghiêm trọng ({'; '.join(kinds[:4])}). Phần lớn xử lý được bằng thao tác vận hành, không cần mua sắm.",
            "Giao IT xử lý theo từng mã đối soát bên dưới (chi tiết tại bảng 'Rủi ro compliance nổi bật' cuối báo cáo và mục 'Compliance Findings' của Báo cáo Kỹ thuật) và báo cáo kết quả trong kỳ tới.",
            "Nếu để lâu, các điểm nhỏ tích tụ thành rủi ro lớn về hiệu năng và chi phí.",
            "Không cần phê duyệt ngân sách — ghi nhận và theo dõi tiến độ xử lý.", "2–4 tuần", tone="neutral",
            refs=refs_from(crit, limit=10))

    # 6) Reclaim opportunity
    if reclaim["count"] >= 3:
        equiv = f" — tương đương ~{host_equiv:.1f} host" if host_equiv >= 0.3 else ""
        add("thap", f"Cơ hội tiết kiệm: {reclaim['count']} máy chủ ảo không sử dụng có thể thu hồi",
            f"Có {reclaim['count']} VM đã bỏ không hoặc tắt lâu ngày nhưng vẫn giữ {fmt_int(reclaim['vcpu'])} vCPU và {fmt_int(reclaim['ramGB'])} GB RAM"
            f"{equiv}, chiếm khoảng {reclaim_pct_ram:.0f}% dư địa RAM cho phép.",
            "Xác nhận với chủ sở hữu ứng dụng rồi thu hồi/dừng hẳn. Việc này giải phóng năng lực miễn phí trước khi cân nhắc mua thêm.",
            "Tiếp tục trả chi phí (license, backup, vận hành) cho tài nguyên không tạo giá trị và phải mua sắm sớm hơn cần thiết.",
            "Đồng ý chủ trương rà soát và thu hồi VM không sử dụng.", "Trong 1 tháng", tone="opportunity",
            refs=refs_from(by_type("vm_idle", "vm_off")))

    # 7) Over-provisioned VMs
    overprov = [f for f in findings if f.get("type") == "vm_overprovision"]
    if len(overprov) >= 3:
        add("thap", f"{len(overprov)} máy chủ ảo được cấp phát dư so với mức dùng thực",
            "Các VM này được cấp nhiều vCPU nhưng thực tế dùng rất ít. Cấp phát dư làm 'đầy sổ' năng lực trên giấy dù phần cứng còn rảnh.",
            "IT phối hợp chủ ứng dụng để giảm cấu hình (right-size) theo lộ trình an toàn.",
            "Chỉ số overcommit tăng ảo, dẫn tới quyết định mua sắm sớm hơn nhu cầu thật.",
            "Không cần phê duyệt ngân sách — ủng hộ chủ trương right-size.", "1–2 tháng", tone="opportunity",
            refs=refs_from(overprov))

    # 8) Healthy → say so, with a forward date
    if worst < 80:
        add("info", "Chưa cần đầu tư phần cứng mới — tiếp tục theo dõi định kỳ",
            f"Cả CPU ({fmt_pct(cpu_pct, 0)}) và RAM ({fmt_pct(ram_pct, 0)}) đều còn xa ngưỡng cho phép. Hệ thống có thể tiếp nhận thêm khoảng {fmt_int(binding_fit) if binding_fit is not None else '—'} VM cỡ trung bình hiện tại.",
            f"Duy trì báo cáo định kỳ hàng quý. Mốc cần xem xét đầu tư tiếp theo (nếu tăng trưởng 10%/quý): {_runway_phrase(binding_months)} ở {binding}.",
            "Không có rủi ro trọng yếu tại thời điểm báo cáo.",
            "Không cần quyết định đầu tư tại thời điểm này.", "Rà soát lại quý sau", tone="ok")

    recs.sort(key=lambda r: PRIORITY_ORDER.get(r["priority"], 9))
    recs = recs[:6]

    # --- 30-second summary bullets ---
    points = []
    if include_storage and _finite(storage_pct):
        points.append(f"Sức khỏe tổng thể: {health_word}. CPU đang dùng {fmt_pct(cpu_pct, 0)}, RAM {fmt_pct(ram_pct, 0)} và Storage {fmt_pct(storage_pct, 0)} ngưỡng cho phép.")
    else:
        points.append(f"Sức khỏe tổng thể: {health_word}. CPU đang dùng {fmt_pct(cpu_pct, 0)} và RAM {fmt_pct(ram_pct, 0)} ngưỡng cho phép (Storage quản lý riêng ở tầng SAN).")
    if binding_fit is not None:
        points.append(f"Dư địa: còn tiếp nhận thêm khoảng {fmt_int(binding_fit)} VM cỡ trung bình trước khi {binding} chạm ngưỡng; nguồn lực sẽ hết trước là {binding}.")
    points.append(f"Xu hướng: nếu tăng trưởng 10%/quý, {binding} dự kiến chạm ngưỡng sau {_runway_phrase(binding_months)} — đây là mốc nên đưa vào kế hoạch ngân sách.")
    if ha_eligible:
        points.append(f"Dự phòng sự cố: {len(ha_eligible) - len(ha_fail)}/{len(ha_eligible)} cluster vẫn vận hành bình thường nếu mất 1 host"
                      + (f"; {len(ha_fail)} cluster chưa đạt ({', '.join(r['cluster'] for r in ha_fail[:4])})." if ha_fail else "."))
    if reclaim["count"] >= 3:
        points.append(f"Cơ hội tiết kiệm: thu hồi {reclaim['count']} VM không sử dụng giải phóng ~{fmt_int(reclaim['vcpu'])} vCPU / {fmt_int(reclaim['ramGB'])} GB RAM mà không tốn chi phí.")

    decisions = [r for r in recs if r["priority"] in ("cao", "tb")]
    if decisions:
        decision_line = f"Cần Ban lãnh đạo xem xét {len(decisions)} nội dung: " + "; ".join(d["decision"].rstrip(".") for d in decisions[:3]) + "."
    else:
        decision_line = "Không có quyết định đầu tư nào cần phê duyệt tại kỳ báo cáo này; các đề xuất còn lại thuộc phạm vi vận hành của IT."

    return {"health_cls": health_cls, "health_word": health_word, "headline": headline, "points": points,
            "decision_line": decision_line, "recommendations": recs, "binding": binding}


def _brief_html(brief: Dict[str, Any]) -> str:
    fg_key, bg_key = STATUS_COLOR.get(brief["health_cls"], STATUS_COLOR["na"])
    items = "".join(f'<li>{esc(pt)}</li>' for pt in brief["points"])
    return f"""
    <div class="exec-brief {brief['health_cls']}">
      <div class="exec-brief-head">
        <span class="pill" style="color:{COLORS[fg_key]};background:{COLORS[bg_key]};border-color:{COLORS[fg_key]}55;font-size:9px;">{esc(brief['health_word'])}</span>
        <span class="exec-brief-title">Tóm tắt 30 giây cho Ban lãnh đạo</span>
      </div>
      <div class="exec-brief-headline">{esc(brief['headline'])}</div>
      <ul class="brief-list">{items}</ul>
      <div class="exec-brief-decision"><b>Quyết định cần từ Ban lãnh đạo:</b> {esc(brief['decision_line'])}</div>
    </div>"""


def _refs_html(r: Dict[str, Any], compact: bool = False) -> str:
    """'Đối soát' line under a recommendation: ticket-style finding codes with
    object and measured value, plus a pointer to the full tables."""
    refs = r.get("refs") or {}
    items = refs.get("items") or []
    note = r.get("refNote") or ""
    if not items and not note:
        return ""
    compact = compact or bool(refs.get("compact"))
    parts = []
    for it in items:
        detail = "" if compact else f" — {esc(it.get('metric') or it.get('typeLabel') or '')}"
        vc = f" ({esc(it.get('vcenterKey'))})" if it.get("vcenterKey") else ""
        parts.append(f'<span class="ref-item"><span class="ref-code">{esc(it.get("code"))}</span> {esc(it.get("entity"))}{vc}{detail}</span>')
    more = refs.get("total", 0) - len(items)
    if more > 0:
        parts.append(f'<span class="ref-item muted">+{more} mã khác — xem bảng Compliance Findings (Báo cáo Kỹ thuật)</span>')
    body = ("".join(parts)) if parts else ""
    note_html = f'<div class="ref-note">{esc(note)}</div>' if note else ""
    return f'<div class="rec-refs"><b>Đối soát:</b> {body}{note_html}</div>'


def _register_codes_html(r: Dict[str, Any], limit: int = 5) -> str:
    items = (r.get("refs") or {}).get("items") or []
    if not items or limit <= 0:
        return ""
    codes = ", ".join(str(it.get("code")) for it in items[:limit])
    more = (r.get("refs") or {}).get("total", 0) - min(len(items), limit)
    return f'<div class="ref-codes-inline">Mã đối soát: {esc(codes)}{f" (+{more})" if more > 0 else ""}</div>'


def _decision_register_html(recs: List[Dict[str, Any]], code_limit: int = 5, show_title: bool = True) -> str:
    rows = []
    for i, r in enumerate(recs, 1):
        title_html = f'<div class="small-note" style="margin-top:1px;">{esc(r["title"])}</div>' if show_title else ""
        rows.append(f"""
      <tr>
        <td style="text-align:center;">{i}</td>
        <td><b>{esc(r['decision'])}</b>{title_html}{_register_codes_html(r, code_limit)}</td>
        <td><span class="priority-pill priority-{r['priority']}">{PRIORITY_LABEL_VI.get(r['priority'], '')}</span></td>
        <td>{esc(r['timeframe'])}</td>
        <td>{esc(r['impact'])}</td>
        <td style="width:74px;"><span class="sign-box"></span></td>
      </tr>""")
    if not rows:
        rows.append('<tr><td colspan="6" class="small-note">Không có nội dung cần phê duyệt.</td></tr>')
    return "".join(rows)


def build_management_brief(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]],
                           policy: capacity.Policy) -> Dict[str, Any]:
    """Compute the management brief (headline, key points, recommendations,
    decision register) as plain JSON for the web Executive View, using the
    exact same logic as the Executive PDF so both surfaces agree."""
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    findings = capacity.build_compliance_findings(hosts, vms, policy)
    headroom = capacity.estimate_capacity_headroom(model, vms, policy)
    growth = capacity.estimate_growth_scenarios(model)
    ha_rows = capacity.evaluate_ha_headroom(hosts, vms, policy)
    brief = _management_brief(model, cluster_rows, findings, policy, vms, hosts, headroom, growth, ha_rows)
    brief["overall"] = _overall_status(model)
    brief["include_storage"] = bool(getattr(policy, "include_storage", False))
    brief["generated_at"] = _now_str()
    brief["counts"] = {"hosts": len(hosts), "vms": len(vms), **_severity_counts(findings)}
    for i, rec in enumerate(brief["recommendations"], start=1):
        rec["index"] = i
        rec["priority_label"] = PRIORITY_LABEL_VI.get(rec["priority"], rec["priority"])
    return brief


def _executive_insights(model: Dict[str, Any], cluster_rows: List[Dict[str, Any]],
                        findings: List[Dict[str, Any]], policy: capacity.Policy) -> List[Dict[str, str]]:
    """Backward-compatible flat view of the management brief (title/body/risk)."""
    headroom = capacity.estimate_capacity_headroom(model, [], policy)
    growth = capacity.estimate_growth_scenarios(model)
    brief = _management_brief(model, cluster_rows, findings, policy, [], [], headroom, growth, [])
    return [{"risk": r["priority"] == "cao", "title": r["title"], "body": f"{r['meaning']} {r['action']}"} for r in brief["recommendations"]]


# ---------------------------------------------------------------------------
# Forward-looking capacity planning content (forecast, headroom, HA/DR check)
# shared by both the Executive and Technical reports.
# ---------------------------------------------------------------------------
METRIC_LABEL_VI = {"cpu": "CPU", "ram": "RAM", "storage": "Storage"}

STORAGE_EXCLUDED_NOTE = (
    "Tiêu chí Storage không thuộc phạm vi đánh giá của báo cáo này: hạ tầng lưu trữ dùng SAN dùng chung, "
    "nhiều VM cấp phát thick provisioning nên tỷ lệ \"đã dùng / cấp phát\" luôn tiệm cận 100% và không phản ánh "
    "dung lượng SAN thực còn trống. Năng lực lưu trữ được quản lý và báo cáo riêng ở tầng SAN."
)


def _storage_excluded_html() -> str:
    return f'<div class="insight-box" style="margin-top:6px;"><b>Storage: không đánh giá trong báo cáo này</b><br/>{esc(STORAGE_EXCLUDED_NOTE)}</div>'


def _fmt_months(months: Optional[float]) -> str:
    if months is None:
        return "Ch\u01b0a x\u00e1c \u0111\u1ecbnh (t\u1ed1c \u0111\u1ed9 t\u0103ng tr\u01b0\u1edfng gi\u1ea3 \u0111\u1ecbnh b\u1eb1ng 0 ho\u1eb7c \u00e2m)"
    if months <= 0.05:
        return "\u0110\u00e3 \u0111\u1ea1t/v\u01b0\u1ee3t ng\u01b0\u1ee1ng ngay t\u1ea1i th\u1eddi \u0111i\u1ec3m b\u00e1o c\u00e1o"
    if months > 36:
        return "> 36 th\u00e1ng"
    if months < 1:
        return "< 1 th\u00e1ng"
    return f"~{months:.0f} th\u00e1ng"


def _growth_scenario_rows_html(growth: Dict[str, Any], include_storage: bool = True) -> str:
    rows = []
    for key in (("cpu", "ram", "storage") if include_storage else ("cpu", "ram")):
        g = growth.get(key, {})
        cur = g.get("currentPct")
        cells = "".join(
            f'<td>{_fmt_months(sc["months"])}</td>' for sc in g.get("scenarios", [])
        )
        rows.append(f"""
      <tr>
        <td><b>{METRIC_LABEL_VI[key]}</b></td>
        <td>{fmt_pct(cur)}</td>
        {cells}
      </tr>""")
    return "".join(rows)


def _headroom_cards_html(headroom: Dict[str, Any], include_storage: bool = True) -> str:
    cpu, ram, storage = headroom["cpu"], headroom["ram"], headroom["storage"]

    def vms_fit_text(v: Optional[int]) -> str:
        if v is None:
            return "\u2014"
        return f"~{v} VM" if v < 9999 else "> 9,999 VM"

    storage_card = f"""
      <div class="headroom-card">
        <div class="headroom-metric">D\u01b0 \u0111\u1ecba Storage t\u1edbi ng\u01b0\u1ee1ng 90%</div>
        <div class="headroom-value">{fmt_int(storage['freeStorageGB'])} <span>GB tr\u1ed1ng</span></div>
        <div class="headroom-sub">\u01af\u1edbc t\u00ednh th\u00eam \u0111\u01b0\u1ee3c {vms_fit_text(storage['vmsFit'])} c\u1ee1 trung b\u00ecnh hi\u1ec7n t\u1ea1i tr\u01b0\u1edbc khi datastore g\u1ea7n \u0111\u1ea7y.</div>
      </div>""" if include_storage else ""
    storage_avg = f" \u00b7 {fmt_num(headroom['avg']['avgStorageGB'],1)} GB storage" if include_storage else ""

    return f"""
    <div class="chart-row">
      <div class="headroom-card">
        <div class="headroom-metric">D\u01b0 \u0111\u1ecba CPU t\u1edbi ng\u01b0\u1ee1ng c\u1ea5u h\u00ecnh</div>
        <div class="headroom-value">{fmt_int(cpu['freeVcpu'])} <span>vCPU tr\u1ed1ng / {fmt_int(cpu['maxVcpu'])} vCPU t\u1ed1i \u0111a</span></div>
        <div class="headroom-sub">\u01af\u1edbc t\u00ednh th\u00eam \u0111\u01b0\u1ee3c {vms_fit_text(cpu['vmsFit'])} c\u1ee1 trung b\u00ecnh hi\u1ec7n t\u1ea1i tr\u01b0\u1edbc khi ch\u1ea1m ng\u01b0\u1ee1ng overcommit CPU.</div>
      </div>
      <div class="headroom-card">
        <div class="headroom-metric">D\u01b0 \u0111\u1ecba RAM t\u1edbi ng\u01b0\u1ee1ng c\u1ea5u h\u00ecnh</div>
        <div class="headroom-value">{fmt_int(ram['freeRamGB'])} <span>GB tr\u1ed1ng / {fmt_int(ram['maxRamGB'])} GB t\u1ed1i \u0111a</span></div>
        <div class="headroom-sub">\u01af\u1edbc t\u00ednh th\u00eam \u0111\u01b0\u1ee3c {vms_fit_text(ram['vmsFit'])} c\u1ee1 trung b\u00ecnh hi\u1ec7n t\u1ea1i tr\u01b0\u1edbc khi ch\u1ea1m ng\u01b0\u1ee1ng overcommit RAM.</div>
      </div>
      {storage_card}
    </div>
    <div class="small-note" style="margin-top:6px;">VM \"c\u1ee1 trung b\u00ecnh\" t\u00ednh theo footprint b\u00ecnh qu\u00e2n c\u1ee7a c\u00e1c VM \u0111ang b\u1eadt trong ph\u1ea1m vi b\u00e1o c\u00e1o: {fmt_num(headroom['avg']['avgVcpu'],1)} vCPU \u00b7 {fmt_num(headroom['avg']['avgRamGB'],1)} GB RAM{storage_avg}.</div>
    """


def _ha_table_html(ha_rows: List[Dict[str, Any]]) -> str:
    rows = []
    for r in ha_rows:
        if not r.get("eligible"):
            rows.append(f"""
      <tr><td><b>{esc(r['cluster'])}</b></td><td colspan="3" class="small-note">Ch\u1ec9 c\u00f3 {fmt_int(r['hosts'])} host \u2014 kh\u00f4ng \u0111\u1ee7 \u0111\u1ec3 ki\u1ec3m tra d\u1ef1 ph\u00f2ng N+1.</td></tr>""")
            continue
        survives = r.get("survives")
        pill = ('<span class="pill" style="color:%s;background:%s;border-color:%s33">%s</span>' % (
            COLORS["ok"], COLORS["ok_bg"], COLORS["ok"], "\u0110\u00e1p \u1ee9ng") if survives else
            '<span class="pill" style="color:%s;background:%s;border-color:%s33">%s</span>' % (
            COLORS["crit"], COLORS["crit_bg"], COLORS["crit"], "Kh\u00f4ng \u0111\u00e1p \u1ee9ng"))
        rows.append(f"""
      <tr>
        <td><b>{esc(r['cluster'])}</b></td>
        <td>{esc(r.get('largestHost') or '\u2014')}</td>
        <td>{fmt_pct(r.get('cpuPctAfter'))} / {fmt_pct(r.get('ramPctAfter'))}</td>
        <td>{pill}</td>
      </tr>""")
    return "".join(rows)


def _technical_headroom_actions(cluster_rows: List[Dict[str, Any]], policy: capacity.Policy) -> List[Dict[str, Any]]:
    """Per-cluster extra-core / extra-RAM-GB required to bring a cluster
    currently OVER its policy overcommit ratio back to exactly the policy
    max, plus a priority tag. Only clusters at/above the warn threshold
    (80%) are returned, ranked worst first."""
    ht_factor = max(1.0, policy.ht_factor or 2.0)
    out = []
    for c in cluster_rows:
        m = c["model"]
        cpu_pct, ram_pct = m.get("cpuPct"), m.get("ramPct")
        worst = max(cpu_pct if _finite(cpu_pct) else -1, ram_pct if _finite(ram_pct) else -1)
        if worst < 80:
            continue
        extra_logical_cores = 0.0
        if _finite(cpu_pct) and cpu_pct > 100 and policy.cpu_overcommit_max > 0:
            extra_logical_cores = max(0.0, m["usedVcpu"] / policy.cpu_overcommit_max - m["totalLogicalCores"])
        extra_ram_gb = 0.0
        if _finite(ram_pct) and ram_pct > 100 and policy.ram_overcommit_max > 0:
            extra_ram_gb = max(0.0, m["usedRamGB"] / policy.ram_overcommit_max - m["totalRamGB"])
        priority = "cao" if worst >= 100 else ("tb" if worst >= 90 else "thap")
        out.append({
            "cluster": c["name"], "cpuPct": cpu_pct, "ramPct": ram_pct,
            "extraPhysCores": extra_logical_cores / ht_factor, "extraRamGB": extra_ram_gb,
            "priority": priority,
        })
    return sorted(out, key=lambda r: max(r["cpuPct"] if _finite(r["cpuPct"]) else -1, r["ramPct"] if _finite(r["ramPct"]) else -1), reverse=True)


def _technical_actions_html(actions: List[Dict[str, Any]]) -> str:
    if not actions:
        return '<tr><td colspan="6" class="small-note">Kh\u00f4ng c\u00f3 cluster n\u00e0o \u1edf m\u1ee9c c\u1ea3nh b\u00e1o tr\u1edf l\u00ean.</td></tr>'
    prio_label = {"cao": "Cao", "tb": "Trung b\u00ecnh", "thap": "Th\u1ea5p"}
    rows = []
    for a in actions:
        need = []
        if a["extraPhysCores"] > 0.05:
            need.append(f"+{a['extraPhysCores']:.1f} core v\u1eadt l\u00fd (CPU)")
        if a["extraRamGB"] > 0.5:
            need.append(f"+{a['extraRamGB']:.0f} GB RAM")
        need_text = "; ".join(need) if need else "Ch\u01b0a v\u01b0\u1ee3t ng\u01b0\u1ee1ng \u2014 theo d\u00f5i th\u00eam"
        rows.append(f"""
      <tr>
        <td><b>{esc(a['cluster'])}</b></td>
        <td>{fmt_pct(a['cpuPct'])}</td>
        <td>{fmt_pct(a['ramPct'])}</td>
        <td><span class="priority-pill priority-{a['priority']}">{prio_label[a['priority']]}</span></td>
        <td>{esc(need_text)}</td>
      </tr>""")
    return "".join(rows)


# ---------------------------------------------------------------------------
# Executive report
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Executive report — compact two-page layout helpers
# ---------------------------------------------------------------------------
def _exec_compact_css() -> str:
    """Overrides that keep the Executive report on two A4 pages: tighter
    spacing, a 2-column recommendation grid, smaller gauges and tables."""
    return f"""
body.exec-compact {{ font-size:9.4px; line-height:1.42; }}
body.exec-compact .cover-title {{ font-size:19px; }}
body.exec-compact .cover-sub {{ margin-bottom:6px; font-size:10.5px; }}
body.exec-compact .meta-row {{ margin-bottom:8px; }}
body.exec-compact .section {{ margin-top:7px; }}
body.exec-compact .section-title {{ font-size:11.5px; margin-bottom:4px; }}
body.exec-compact .section-sub {{ font-size:8.2px; margin:-2px 0 5px 11px; }}
body.exec-compact .exec-brief {{ padding:8px 12px 7px; }}
body.exec-compact .exec-brief-headline {{ font-size:11.5px; margin-bottom:4px; }}
body.exec-compact .brief-list {{ font-size:8.8px; margin-bottom:4px; }}
body.exec-compact .exec-brief-decision {{ font-size:8.8px; }}
body.exec-compact .gauge-row {{ margin-bottom:6px; }}
body.exec-compact .gauge-card {{ padding:4px 4px 3px; }}
body.exec-compact .gauge-caption {{ font-size:7.6px; }}
body.exec-compact .kpi-card {{ padding:5px 9px; }}
body.exec-compact .kpi-value {{ font-size:13px; }}
body.exec-compact .kpi-sub {{ font-size:7.8px; }}
body.exec-compact .overview-row {{ display:flex; gap:8px; align-items:stretch; }}
body.exec-compact .overview-row .gauge-row {{ flex:0 0 46%; margin-bottom:0; }}
body.exec-compact .overview-row .kpi-col {{ flex:1; display:flex; flex-direction:column; gap:6px; }}
body.exec-compact .overview-row .kpi-grid {{ flex:1; margin-bottom:0; gap:6px; }}
body.exec-compact .overview-row .kpi-card {{ display:flex; flex-direction:column; justify-content:center; }}
body.exec-compact .insight-box {{ padding:6px 10px; font-size:8.4px; margin-bottom:4px; }}
body.exec-compact table {{ font-size:8.3px; }}
body.exec-compact th {{ font-size:7.2px; padding:3.5px 5px; }}
body.exec-compact td {{ padding:3px 5px; }}
body.exec-compact .small-note {{ font-size:7.4px; margin-top:4px; }}
body.exec-compact .assumption-note {{ font-size:7.6px; padding:5px 8px; margin-top:5px; }}
body.exec-compact .legend {{ font-size:7.4px; margin-top:2px !important; gap:4px 10px; }}
body.exec-compact .legend span {{ margin-right:6px; }}
body.exec-compact .chart-card-title {{ font-size:8.4px; margin-bottom:2px; }}
.rec-row {{ display:flex; gap:6px; margin-bottom:6px; align-items:stretch; }}
.rec-card.compact {{ flex:1; margin:0; padding:6px 9px 6px; font-size:8.3px; line-height:1.42; }}
.rec-card.compact.spacer {{ border-color:transparent; background:transparent; }}
.rec-card.compact .rec-head {{ margin-bottom:3px; gap:6px; }}
.rec-card.compact .rec-title {{ font-size:9.4px; line-height:1.3; }}
.rec-card.compact .rec-num {{ width:14px; height:14px; line-height:14px; font-size:7.8px; }}
.rec-card.compact .rec-meta {{ display:flex; gap:4px; align-items:center; margin-bottom:4px; }}
.rec-card.compact .rec-line {{ margin-bottom:2px; color:{COLORS['ink']}; }}
.rec-card.compact .rec-line b {{ color:{COLORS['muted']}; font-size:7.2px; text-transform:uppercase; letter-spacing:.06em; margin-right:3px; }}
.rec-card.compact .rec-decision {{ margin-top:3px; font-size:8.3px; }}
.rec-card.compact .rec-refs {{ margin-top:3px; padding-top:3px; font-size:7.4px; line-height:1.5; }}
.rec-card.compact .ref-code {{ font-size:7px; }}
.crit-table td {{ vertical-align:top; }}
.ref-map-box {{ background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-left:3px solid {COLORS['primary']}; border-radius:0 6px 6px 0; padding:5px 8px 4px; }}
.ref-map-title {{ font-size:8.6px; font-weight:700; color:{COLORS['primary_dark']}; margin-bottom:2px; }}
.ref-map-title span {{ font-weight:400; color:{COLORS['muted']}; font-size:7.4px; }}
body.exec-compact table.ref-map {{ font-size:7.7px; }}
body.exec-compact table.ref-map td {{ width:50%; border-bottom:none; padding:1px 6px 1px 0; color:{COLORS['muted']}; vertical-align:top; }}
body.exec-compact table.ref-map td b {{ color:{COLORS['ink']}; }}
.page-2 {{ break-before:page; margin-top:0; }}
/* Dense page 2: used when the decision register has more than three rows or
   the storage row is included, so the reference box still fits on page 2. */
body.exec-compact.dense .page-2 .section {{ margin-top:5px; }}
body.exec-compact.dense .page-2 .section-title {{ margin-bottom:3px; }}
body.exec-compact.dense .page-2 .section-sub {{ margin-bottom:3px; }}
body.exec-compact.dense .page-2 td {{ padding:2.2px 5px; }}
body.exec-compact.dense .page-2 th {{ padding:2.8px 5px; }}
body.exec-compact.dense .page-2 .insight-box {{ padding:5px 9px; margin-bottom:3px; }}
body.exec-compact.dense .page-2 .assumption-note {{ padding:4px 8px; margin-top:4px; }}
body.exec-compact.dense .page-2 .small-note {{ margin-top:3px; }}
"""


def _recommendations_compact_html(recs: List[Dict[str, Any]], limit: int = 4) -> str:
    """Two-column card grid: meaning → proposal → decision, plus compact
    reconciliation codes. 'Nếu chưa hành động' moves to the decision register
    to save space; full narrative lives in the web Executive View. At most
    ``limit`` cards are drawn so page 1 of the two-page Executive report never
    overflows; the rest stay in the decision register on page 2."""
    cards = []
    hidden = max(0, len(recs) - limit)
    for i, r in enumerate(recs[:limit], 1):
        refs = r.get("refs") or {}
        items = (refs.get("items") or [])[:4]
        total = refs.get("total", 0)
        chips = ""
        for it in items:
            where = f" ({esc(it.get('vcenterKey'))})" if it.get("vcenterKey") else ""
            chips += f'<span class="ref-item"><span class="ref-code">{esc(it.get("code"))}</span> {esc(it.get("entity"))}{where}</span>'

        more = total - len(items)
        if more > 0:
            chips += f'<span class="ref-item muted">+{more} mã khác — Báo cáo Kỹ thuật, mục Compliance Findings</span>'
        note = r.get("refNote") or ""
        refs_html = ""
        if chips or note:
            note_html = f'<div class="ref-note">{esc(note)}</div>' if note else ""
            refs_html = f'<div class="rec-refs"><b>Đối soát:</b> {chips}{note_html}</div>'
        cards.append(f"""
      <div class="rec-card compact tone-{r['tone']} prio-{r['priority']}">
        <div class="rec-head"><span class="rec-num">{i}</span><span class="rec-title">{esc(r['title'])}</span></div>
        <div class="rec-meta"><span class="priority-pill priority-{r['priority']}">{PRIORITY_LABEL_VI.get(r['priority'], '')}</span><span class="chip">{esc(r['timeframe'])}</span></div>
        <div class="rec-line"><b>Ý nghĩa</b>{esc(r['meaning'])}</div>
        <div class="rec-line"><b>Đề xuất</b>{esc(r['action'])}</div>
        <div class="rec-decision"><b>Đề nghị Ban lãnh đạo:</b> {esc(r['decision'])}</div>
        {refs_html}
      </div>""")
    # WeasyPrint has limited flex-wrap support, so pair the cards into explicit
    # two-column rows instead of relying on wrapping.
    rows = []
    for i in range(0, len(cards), 2):
        pair = cards[i:i + 2]
        if len(pair) == 1:
            pair.append('<div class="rec-card compact spacer"></div>')
        rows.append(f'<div class="rec-row">{"".join(pair)}</div>')
    if hidden:
        rows.append(f'<div class="small-note" style="margin-top:4px;">+{hidden} đề xuất khác (ưu tiên thấp hơn) được liệt kê đầy đủ trong '
                    'Danh mục quyết định ở trang 2 và trong Báo cáo Kỹ thuật.</div>')
    return "".join(rows)


def _exec_outlook_table_html(model: Dict[str, Any], headroom: Dict[str, Any], growth: Dict[str, Any],
                             include_storage: bool) -> str:
    """One table: current % of policy, absolute headroom, VM-equivalents and
    months-to-threshold under three growth assumptions."""
    def fit(v):
        return "—" if v is None else (f"~{v} VM" if v < 9999 else "> 9,999 VM")
    rows = []
    spec = [("cpu", "CPU", model.get("cpuPct"), f"{fmt_int(headroom['cpu']['freeVcpu'])} vCPU", headroom["cpu"]["vmsFit"]),
            ("ram", "RAM", model.get("ramPct"), f"{fmt_int(headroom['ram']['freeRamGB'])} GB", headroom["ram"]["vmsFit"])]
    if include_storage:
        spec.append(("storage", "Storage", model.get("storagePct"), f"{fmt_int(headroom['storage']['freeStorageGB'])} GB", headroom["storage"]["vmsFit"]))
    for key, label, pct, free_txt, vms_fit in spec:
        g = growth.get(key, {})
        cells = "".join(f"<td>{_fmt_months(sc['months'])}</td>" for sc in g.get("scenarios", []))
        st = capacity.get_status_for_pct(pct, 80, 100) if key != "storage" else capacity.get_status_for_pct(pct, 80, 90)
        rows.append(f"<tr><td><b>{label}</b></td><td>{bar_cell(pct, st['cls'])}</td><td>{free_txt}</td><td>{fit(vms_fit)}</td>{cells}</tr>")
    avg = headroom["avg"]
    return (f'<table><thead><tr><th>Chỉ số</th><th>Hiện tại (% ngưỡng)</th><th>Dư địa còn lại</th><th>Còn nạp thêm</th>'
            f'<th>Chạm ngưỡng nếu +5%/quý</th><th>+10%/quý</th><th>+20%/quý</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="small-note">"Còn nạp thêm" tính theo VM cỡ trung bình đang bật ({fmt_num(avg["avgVcpu"],1)} vCPU · {fmt_num(avg["avgRamGB"],1)} GB RAM); '
            f'số tháng tới ngưỡng là giả định tăng đều theo quý để lập ngân sách, không phải cam kết.</div>')


def _exec_history_compact_html(history: Optional[Dict[str, Any]], vm_changes: Optional[Dict[str, Any]],
                               chart_height: int = 88) -> str:
    months = (history or {}).get("months", 12)
    title = f"Biến động tài nguyên {months} tháng gần nhất"
    if not history or not history.get("available"):
        count = (history or {}).get("pointCount", 0)
        msg = ("Hệ thống mới có 1 ngày snapshot trong khoảng thời gian này — biểu đồ biến động sẽ xuất hiện từ lần đồng bộ tiếp theo."
               if count == 1 else "Chưa có snapshot nào trong khoảng thời gian này. Lịch sử được tích lũy tự động sau mỗi lần đồng bộ vCenter.")
        return f'<div class="section"><div class="section-title">{esc(title)}</div><div class="assumption-note">{esc(msg)}</div></div>'
    s = history["overall"]["summary"]
    cpu_dates, cpu_series = _history_series(history, "cpuPct")
    ram_dates, ram_series = _history_series(history, "ramPct")
    insights = _history_insights(history)
    first_insight = insights[0] if insights else ""
    vm_line = ""
    if vm_changes and vm_changes.get("available"):
        a, r = vm_changes["addedTotals"], vm_changes["removedTotals"]
        vm_line = (f' Biến động VM: <b style="color:{COLORS["ok"]};">+{a["count"]} thêm mới</b> ({fmt_int(a["vcpu"])} vCPU · {fmt_int(a["ramGB"])} GB RAM) · '
                   f'<b style="color:{COLORS["warn"]};">−{r["count"]} loại bỏ</b> ({fmt_int(r["vcpu"])} vCPU · {fmt_int(r["ramGB"])} GB RAM){esc(_vm_changes_excluded_note(vm_changes, compact=True))} — chi tiết tại Phụ lục A của Báo cáo Kỹ thuật.')
    scope_clusters = history.get("clusters")
    scope_txt = ""
    if scope_clusters is not None:
        shown = ", ".join(scope_clusters[:4]) + (f" và {len(scope_clusters) - 4} cluster khác" if len(scope_clusters) > 4 else "")
        scope_txt = f" Phạm vi: cluster {shown}." if scope_clusters else " Phạm vi: không có cluster nào được chọn."
    sub = (f"Từng cluster là một nhóm dự án độc lập; so sánh {_fmt_short_date(s.get('firstDate'))} → {_fmt_short_date(s.get('lastDate'))} "
           f"qua {fmt_int(history.get('pointCount'))} ngày có snapshot. 100% = chạm ngưỡng overcommit đang áp dụng.{scope_txt}")
    cpu_chart = svg_line_chart(cpu_dates, cpu_series, width=330, height=chart_height)
    ram_chart = svg_line_chart(ram_dates, ram_series, width=330, height=chart_height)
    return (
        '<div class="section">'
        f'<div class="section-title">{esc(title)}</div>'
        f'<div class="section-sub">{esc(sub)}</div>'
        '<div class="chart-row" style="gap:8px;">'
        f'<div class="chart-card soft" style="padding:4px 6px;"><div class="chart-card-title">CPU — % ngưỡng theo cluster</div>{cpu_chart}</div>'
        f'<div class="chart-card soft" style="padding:4px 6px;"><div class="chart-card-title">RAM — % ngưỡng theo cluster</div>{ram_chart}</div>'
        '</div>'
        f'{_history_legend_html(history)}'
        f'<div class="insight-box" style="margin-top:4px;">{first_insight}{vm_line}</div>'
        '</div>')


def _exec_critical_table_html(findings: List[Dict[str, Any]], limit: int = 7) -> str:
    crit = sorted([f for f in findings if f.get("severity") == "critical"],
                  key=lambda f: (str(f.get("cluster") or ""), str(f.get("entity") or "")))
    sev = _severity_counts(findings)
    if not crit:
        return (f'<div class="insight-box"><b>Không có điểm nghiêm trọng cần xử lý ngay.</b><br/>'
                f'Còn {fmt_int(sev["warning"])} cảnh báo và {fmt_int(sev["info"])} thông tin (chủ yếu tối ưu cấp phát) — danh sách đầy đủ kèm mã đối soát tại Báo cáo Kỹ thuật, mục "Compliance Findings đầy đủ".</div>')
    rows = "".join(f"""
      <tr>
        <td class="code-cell">{esc(f.get('code'))}</td>
        <td style="white-space:nowrap;"><b>{esc(f.get('entity'))}</b></td>
        <td style="white-space:nowrap;">{esc(' · '.join(x for x in (f.get('cluster'), f.get('vcenterKey')) if x))}</td>
        <td>{esc(f.get('typeLabel'))}</td>
        <td>{esc(f.get('metric') or '')}</td>
      </tr>""" for f in crit[:limit])
    more = len(crit) - limit
    more_html = f'<div class="small-note">+{more} điểm nghiêm trọng khác — xem Báo cáo Kỹ thuật.</div>' if more > 0 else ""
    return (f'<table class="crit-table"><thead><tr><th style="width:56px;">Mã</th><th>Đối tượng</th><th>Cluster · vCenter</th><th>Vấn đề</th><th>Chỉ số</th></tr></thead><tbody>{rows}</tbody></table>{more_html}'
            f'<div class="small-note">Tổng finding: {fmt_int(sev["critical"])} nghiêm trọng · {fmt_int(sev["warning"])} cảnh báo · {fmt_int(sev["info"])} thông tin. '
            f'Mã đối soát dùng chung với Báo cáo Kỹ thuật và tab Compliance trên web.</div>')


def _exec_reference_html(include_storage: bool, months: int) -> str:
    """Compact map from executive topics to the Technical report sections."""
    rows = [
        ("Overcommit từng cluster/host", "Overcommit theo Cluster · Chi tiết Host · Top-10 Host"),
        ("Dư địa & số core/RAM cần bổ sung", "Phân tích Headroom & Khuyến nghị mở rộng"),
        ("Dự phòng N+1 theo cluster", "Kiểm tra dự phòng N+1 (mất 1 host)"),
        (f"Biến động CPU/RAM {months} tháng (bảng số)", f"Biến động tài nguyên {months} tháng gần nhất"),
        ("VM thêm mới / loại bỏ theo cluster, host", "Phụ lục A"),
        ("Toàn bộ finding kèm mã đối soát", "Compliance Findings đầy đủ (cột Mã)"),
    ]
    if include_storage:
        rows.insert(3, ("Dung lượng theo datastore", "Top-10 Datastore · Utilization theo Datastore"))
    cells = [f'<td><b>{esc(a)}</b> → {esc(b)}</td>' for a, b in rows]
    if len(cells) % 2:
        cells.append("<td></td>")
    body = "".join(f"<tr>{cells[i]}{cells[i + 1]}</tr>" for i in range(0, len(cells), 2))
    return (f'<div class="ref-map-box"><div class="ref-map-title">Tham chiếu chi tiết tại Báo cáo Kỹ thuật '
            f'<span>(xuất cùng phạm vi lọc vCenter / cluster / host / khoảng thời gian để mã đối soát và số liệu khớp nhau)</span></div>'
            f'<table class="ref-map"><tbody>{body}</tbody></table></div>')


def render_executive_html(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                           scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None,
                           include_types: Optional[List[str]] = None, logo_data_url: Optional[str] = None,
                           history: Optional[Dict[str, Any]] = None, vm_changes: Optional[Dict[str, Any]] = None) -> str:
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    all_findings = capacity.build_compliance_findings(hosts, vms, policy)
    type_filter = set(include_types) if include_types is not None else None
    findings = [f for f in all_findings if type_filter is None or f.get("type") in type_filter]
    overall = _overall_status(model)
    generated_at = _now_str()
    include_storage = bool(getattr(policy, "include_storage", False))

    # Forward-looking capacity planning content: headroom in absolute units,
    # assumption-based growth-to-threshold scenarios, and an N+1 host-loss
    # resiliency check -- gives leadership concrete runway/investment framing
    # instead of only point-in-time utilization numbers.
    headroom = capacity.estimate_capacity_headroom(model, vms, policy)
    growth = capacity.estimate_growth_scenarios(model)
    ha_rows = capacity.evaluate_ha_headroom(hosts, vms, policy)
    brief = _management_brief(model, cluster_rows, findings, policy, vms, hosts, headroom, growth, ha_rows)

    powered_on, powered_off = model["poweredOnCount"], model["poweredOffCount"]
    # Headline utilization gauges — give the cover-page numbers a chart, not
    # just text, for a livelier and more scannable first impression.
    cpu_status = capacity.get_status_for_pct(model.get("cpuPct"), 80, 100)
    ram_status = capacity.get_status_for_pct(model.get("ramPct"), 80, 100)
    gauge_cpu = svg_gauge(model.get("cpuPct"), COLORS[STATUS_COLOR.get(cpu_status["cls"], STATUS_COLOR["na"])[0]], size=104, caption="của ngưỡng cho phép")
    gauge_ram = svg_gauge(model.get("ramPct"), COLORS[STATUS_COLOR.get(ram_status["cls"], STATUS_COLOR["na"])[0]], size=104, caption="của ngưỡng cho phép")
    gauge_cpu_cls, gauge_ram_cls = cpu_status["cls"], ram_status["cls"]
    gauge_ds_html = ""
    storage_kpi_html = ""
    if include_storage:
        ds_status = capacity.get_status_for_pct(model.get("storagePct"), 80, 90)
        gauge_ds = svg_gauge(model.get("storagePct"), COLORS[STATUS_COLOR.get(ds_status["cls"], STATUS_COLOR["na"])[0]], size=104,
                             caption="đã dùng / cấp phát", zones=GAUGE_ZONES_STORAGE)
        gauge_ds_html = (f'<div class="gauge-card {ds_status["cls"]}">{gauge_ds}<div class="kpi-label" style="margin-top:2px;">Storage</div>'
                         f'<div class="gauge-caption">{esc(ds_status["text"])} · vùng vàng từ 80%, đỏ từ 90%</div></div>')
        storage_kpi_html = (f'<div class="kpi-card"><div class="kpi-label">Storage (GB)</div><div class="kpi-value">{fmt_int(model["usedStorageGB"])} / {fmt_int(model["provStorageGB"])}</div>'
                            f'<div class="kpi-sub">{fmt_pct(model["storagePct"])} đã dùng / cấp phát</div></div>')
    else:
        # Keep the 4-card KPI grid balanced with an explicit "out of scope" card
        # so readers see Storage was deliberately excluded, not forgotten.
        storage_kpi_html = ('<div class="kpi-card" style="opacity:.85;"><div class="kpi-label">Storage</div><div class="kpi-value" style="font-size:12px;line-height:1.35;padding-top:3px;">Không đánh giá</div>'
                            '<div class="kpi-sub">Quản lý ở tầng SAN dùng chung</div></div>')
    top_risk_clusters = sorted(cluster_rows, key=lambda c: max(
        c["model"]["cpuPct"] if _finite(c["model"]["cpuPct"]) else -1,
        c["model"]["ramPct"] if _finite(c["model"]["ramPct"]) else -1), reverse=True)[:8]

    brief_html = _brief_html(brief)
    # Page-2 budget: every extra decision-register row costs ~2 critical rows,
    # a crowded page switches to denser spacing (see CSS .dense), and with five
    # or more register rows the one-line finding title under each decision is
    # dropped (it is still on the page-1 cards / Technical report).
    extra_recs = max(0, len(brief["recommendations"]) - 3)
    dense = extra_recs > 0 or include_storage
    decision_rows_html = _decision_register_html([r for r in brief["recommendations"] if r["priority"] in ("cao", "tb", "thap")],
                                                 code_limit=0, show_title=extra_recs < 2)

    ha_eligible = [r for r in ha_rows if r.get("eligible")]
    ha_fail = [r for r in ha_eligible if not r.get("survives")]
    ha_summary_html = (
        f'<div class="insight-box risk"><b>{len(ha_fail)}/{len(ha_eligible)} cluster c\u00f3 \u2265 2 host s\u1ebd v\u01b0\u1ee3t ng\u01b0\u1ee1ng overcommit n\u1ebfu m\u1ea5t 1 host</b><br/>'
        f'Cluster r\u1ee7i ro: {esc(", ".join(r["cluster"] for r in ha_fail[:6]))}. C\u1ea7n b\u1ed5 sung d\u1ef1 ph\u00f2ng N+1 ho\u1eb7c chu\u1ea9n b\u1ecb k\u1ebf ho\u1ea1ch failover th\u1ee7 c\u00f4ng cho c\u00e1c cluster n\u00e0y.</div>'
        if ha_fail else
        '<div class="insight-box"><b>To\u00e0n b\u1ed9 cluster \u0111\u1ee7 \u0111i\u1ec1u ki\u1ec7n \u0111\u1ec1u \u0111\u00e1p \u1ee9ng d\u1ef1 ph\u00f2ng N+1</b><br/>H\u1ec7 th\u1ed1ng v\u1eabn gi\u1eef \u0111\u01b0\u1ee3c ng\u01b0\u1ee1ng overcommit c\u1ea5u h\u00ecnh n\u1ebfu m\u1ea5t 1 host b\u1ea5t k\u1ef3 trong m\u1ed7i cluster.</div>'
    ) if ha_eligible else '<div class="insight-box"><b>Ch\u01b0a \u0111\u1ee7 d\u1eef li\u1ec7u \u0111\u1ec3 \u0111\u00e1nh gi\u00e1 d\u1ef1 ph\u00f2ng N+1</b><br/>Kh\u00f4ng c\u00f3 cluster n\u00e0o c\u00f3 t\u1eeb 2 host tr\u1edf l\u00ean trong ph\u1ea1m vi b\u00e1o c\u00e1o.</div>'

    scope_note = ""
    if run_meta:
        parts = []
        for rm in run_meta:
            ts_fmt = parse_iso_to_vn_str(rm.get("finishedAt"))
            parts.append(f"{esc(rm.get('vcenterKey'))} ({ts_fmt})")
        scope_note = " · ".join(parts)

    filter_note = ""
    if type_filter is not None and len(type_filter) < len(ALL_FINDING_TYPES):
        excluded = [FINDING_TYPE_LABEL_VI.get(t, t) for t in ALL_FINDING_TYPES if t not in type_filter]
        filter_note = f"Đã loại {len(excluded)}/{len(ALL_FINDING_TYPES)} loại finding khỏi báo cáo: {', '.join(excluded)}."

    logo_html = f'<img class="cover-logo" src="{esc(logo_data_url)}"/>' if logo_data_url else ''

    history_compact_html = _exec_history_compact_html(history, vm_changes, chart_height=78 if dense else 88)
    months_win = (history or {}).get("months", 12)
    ha_by_cluster = {r["cluster"]: r for r in ha_rows}

    def ha_cell(name: str) -> str:
        r = ha_by_cluster.get(name)
        if not r or not r.get("eligible"):
            return '<span class="small-note" style="margin:0;">n/a</span>'
        return status_pill({"text": "Đạt", "cls": "ok"}) if r.get("survives") else status_pill({"text": "Không", "cls": "crit"})

    cluster_rows_compact_html = "".join(f"""
      <tr>
        <td style="white-space:nowrap;"><b>{esc(c['name'][:22])}</b></td>
        <td style="white-space:nowrap;">{fmt_int(c['hosts'])} / {fmt_int(c['vms'])}</td>
        <td>{bar_cell(c['model']['cpuPct'], c['cpuStatus']['cls'])}</td>
        <td>{bar_cell(c['model']['ramPct'], c['ramStatus']['cls'])}</td>
        <td>{ha_cell(c['name'])}</td>
        <td>{status_pill(c['overall'])}</td>
      </tr>""" for c in top_risk_clusters[:6])
    recommendations_compact_html = _recommendations_compact_html(brief["recommendations"])
    outlook_html = _exec_outlook_table_html(model, headroom, growth, include_storage)
    critical_html = _exec_critical_table_html(findings, limit=max(3, 7 - 2 * extra_recs - (1 if include_storage else 0)))
    reference_html = _exec_reference_html(include_storage, months_win)
    storage_line = "" if include_storage else f'<div class="small-note" style="margin-top:3px;"><b>Storage không đánh giá:</b> {esc(STORAGE_EXCLUDED_NOTE)}</div>'

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo điều hành', generated_at)}{_exec_compact_css()}</style></head>
<body class="exec-compact{' dense' if dense else ''}">
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Executive Summary</div>
      <div class="cover-title">Báo cáo Điều hành — Capacity Ops</div>
      <div class="cover-sub">Tổng quan năng lực hạ tầng ảo hóa VMware phục vụ ra quyết định đầu tư và mở rộng. Chi tiết kỹ thuật tham chiếu Báo cáo Kỹ thuật cùng kỳ.</div>
    </div>
    {logo_html}
  </div>
  <div class="meta-row">
    <span>Phạm vi: <b>{esc(scope_label)}</b></span>
    <span>Thời điểm tạo báo cáo: <b>{generated_at}</b></span>
    <span>Trạng thái tổng thể: {status_pill(overall)}</span>
    {f'<span>Đồng bộ vCenter: <b>{scope_note}</b></span>' if scope_note else ''}
    {f'<span>{esc(filter_note)}</span>' if filter_note else ''}
  </div>

  {brief_html}

  <div class="section">
    <div class="section-title">Tổng quan năng lực</div>
    <div class="section-sub">Đồng hồ đo thể hiện mức đã dùng so với ngưỡng overcommit cho phép: xanh là an toàn, vàng (từ 80%) cần lên kế hoạch, 100% là vượt ngưỡng.</div>
    <div class="overview-row">
      <div class="gauge-row" style="flex-basis:{'57%' if include_storage else '46%'};">
        <div class="gauge-card {gauge_cpu_cls}">{gauge_cpu}<div class="kpi-label" style="margin-top:1px;">CPU</div><div class="gauge-caption">{esc(cpu_status['text'])} · overcommit {fmt_num(model['cpuOvercommit'],2,'x')} / tối đa {fmt_num(policy.cpu_overcommit_max,1,'x')}</div></div>
        <div class="gauge-card {gauge_ram_cls}">{gauge_ram}<div class="kpi-label" style="margin-top:1px;">RAM</div><div class="gauge-caption">{esc(ram_status['text'])} · overcommit {fmt_num(model['ramOvercommit'],2,'x')} / tối đa {fmt_num(policy.ram_overcommit_max,1,'x')}</div></div>
        {gauge_ds_html}
      </div>
      <div class="kpi-col">
        <div class="kpi-grid">
          <div class="kpi-card"><div class="kpi-label">CPU (pCPU logic)</div><div class="kpi-value">{fmt_int(model['usedVcpu'])} / {fmt_int(model['totalLogicalCores'])}</div><div class="kpi-sub">Overcommit {fmt_num(model['cpuOvercommit'],2,'x')} · {fmt_pct(model['cpuPct'])} ngưỡng</div></div>
          <div class="kpi-card"><div class="kpi-label">RAM (GB)</div><div class="kpi-value">{fmt_int(model['usedRamGB'])} / {fmt_int(model['totalRamGB'])}</div><div class="kpi-sub">Overcommit {fmt_num(model['ramOvercommit'],2,'x')} · {fmt_pct(model['ramPct'])} ngưỡng</div></div>
        </div>
        <div class="kpi-grid">
          {storage_kpi_html}
          <div class="kpi-card"><div class="kpi-label">Hosts / VMs</div><div class="kpi-value">{fmt_int(model['hostCount'])} / {fmt_int(model['vmCount'])}</div><div class="kpi-sub">{fmt_int(powered_on)} bật · {fmt_int(powered_off)} tắt · {fmt_int(len(cluster_rows))} cluster</div></div>
        </div>
      </div>
    </div>
    {storage_line}
  </div>

  <div class="section breakable">
    <div class="section-title">Nhận định &amp; đề xuất cho Ban lãnh đạo</div>
    <div class="section-sub">Mỗi đề xuất nêu ý nghĩa với hoạt động kinh doanh, việc cần làm và nội dung cần Ban lãnh đạo quyết định; mã đối soát dẫn tới dòng cụ thể trong Báo cáo Kỹ thuật (mục Compliance Findings đầy đủ) và tab Compliance trên web.</div>
    {recommendations_compact_html}
  </div>

  <div class="section page-2">
    <div class="section-title">Danh mục quyết định trình Ban lãnh đạo</div>
    <div class="section-sub">Các nội dung cần phê duyệt hoặc ghi nhận chủ trương, xếp theo mức ưu tiên; ô cuối dành cho ý kiến/ký duyệt.</div>
    <table>
      <thead><tr><th style="width:20px;text-align:center;">#</th><th>Nội dung quyết định</th><th>Ưu tiên</th><th>Thời điểm</th><th>Rủi ro tránh được / lợi ích</th><th>Ý kiến</th></tr></thead>
      <tbody>{decision_rows_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Dư địa &amp; dự báo</div>
    {outlook_html}
    {ha_summary_html}
  </div>

  {history_compact_html}

  <div class="section">
    <div class="section-title">Cluster cần quan tâm nhất</div>
    <table>
      <thead><tr><th>Cluster</th><th style="white-space:nowrap;">Host / VM</th><th>CPU (% ngưỡng)</th><th>RAM (% ngưỡng)</th><th>N+1</th><th>Trạng thái</th></tr></thead>
      <tbody>{cluster_rows_compact_html}</tbody>
    </table>
    <div class="small-note">Sắp theo mức dùng cao nhất; N+1 = còn giữ được ngưỡng nếu mất 1 host. Đủ {fmt_int(len(cluster_rows))} cluster tại Báo cáo Kỹ thuật.</div>
  </div>

  <div class="section breakable">
    <div class="section-title">Điểm nghiêm trọng cần IT xử lý</div>
    {critical_html}
  </div>

  <div class="section" style="margin-top:7px;">
    {reference_html}
  </div>
</body></html>"""


# ---------------------------------------------------------------------------
# Technical detail report
# ---------------------------------------------------------------------------
def render_technical_html(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                           scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None,
                           include_types: Optional[List[str]] = None, logo_data_url: Optional[str] = None,
                           history: Optional[Dict[str, Any]] = None, vm_changes: Optional[Dict[str, Any]] = None) -> str:
    history_html = _history_section_html(history, vm_changes, audience="technical")
    appendix_html = _vm_changes_appendix_html(vm_changes)
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    host_rows = sorted(capacity.build_host_rows(hosts, vms, policy),
                        key=lambda h: max(h["cpuPct"] if _finite(h["cpuPct"]) else -1, h["ramPct"] if _finite(h["ramPct"]) else -1),
                        reverse=True)
    include_storage = bool(getattr(policy, "include_storage", False))
    ds_rows = capacity.build_datastore_rows(vms) if include_storage else []
    type_filter = set(include_types) if include_types is not None else None
    findings = sorted(
        [f for f in capacity.build_compliance_findings(hosts, vms, policy) if type_filter is None or f.get("type") in type_filter],
        key=lambda f: {"critical": 0, "warning": 1, "info": 2}.get(f.get("severity"), 3))
    generated_at = _now_str()
    sev = _severity_counts(findings)
    filter_note = ""
    if type_filter is not None and len(type_filter) < len(ALL_FINDING_TYPES):
        excluded = [FINDING_TYPE_LABEL_VI.get(t, t) for t in ALL_FINDING_TYPES if t not in type_filter]
        filter_note = f"Đã loại {len(excluded)}/{len(ALL_FINDING_TYPES)} loại finding khỏi báo cáo: {', '.join(excluded)}."

    policy_note = (f"Ngưỡng overcommit CPU {policy.cpu_overcommit_max:.1f}x · RAM {policy.ram_overcommit_max:.1f}x · "
                   f"Hệ số HT {policy.ht_factor:.1f}x · Ngưỡng idle {policy.idle_days} ngày · "
                   f"Storage: {'có đánh giá' if include_storage else 'không đánh giá (SAN)'}")

    headroom = capacity.estimate_capacity_headroom(model, vms, policy)
    growth = capacity.estimate_growth_scenarios(model)
    ha_rows = capacity.evaluate_ha_headroom(hosts, vms, policy)
    expansion_actions = _technical_headroom_actions(cluster_rows, policy)
    headroom_html = _headroom_cards_html(headroom, include_storage)
    growth_rows_html = _growth_scenario_rows_html(growth, include_storage)
    ha_full_html = _ha_table_html(ha_rows)
    actions_html = _technical_actions_html(expansion_actions)

    top_cluster_names = [c["name"] for c in sorted(
        cluster_rows, key=lambda c: max(
            c["model"]["cpuPct"] if _finite(c["model"]["cpuPct"]) else -1,
            c["model"]["ramPct"] if _finite(c["model"]["ramPct"]) else -1), reverse=True)[:8]]
    top_clusters_for_chart = [c for c in cluster_rows if c["name"] in top_cluster_names]
    cluster_comparison_chart = svg_bar_chart(
        [c["name"] for c in top_clusters_for_chart],
        [
            {"name": "CPU", "color": COLORS["primary"],
             "values": [c["model"]["cpuPct"] if _finite(c["model"]["cpuPct"]) else 0 for c in top_clusters_for_chart]},
            {"name": "RAM", "color": COLORS["accent"],
             "values": [c["model"]["ramPct"] if _finite(c["model"]["ramPct"]) else 0 for c in top_clusters_for_chart]},
        ] + ([
            {"name": "Storage", "color": COLORS["warn"],
             "values": [c["model"]["storagePct"] if _finite(c["model"]["storagePct"]) else 0 for c in top_clusters_for_chart]},
        ] if include_storage else []), width=650, height=230, threshold=100, threshold_label="Ngưỡng", unit="%")
    metrics_label = "CPU/RAM/Storage" if include_storage else "CPU/RAM"
    storage_legend_html = f'<span><span class="dot" style="background:{COLORS["warn"]}"></span>% Storage</span>' if include_storage else ""
    storage_th_html = "<th>% Storage</th>" if include_storage else ""
    storage_excluded_html = "" if include_storage else _storage_excluded_html()
    growth_threshold_note = "100% CPU/RAM, 90% Storage" if include_storage else "100% CPU/RAM"

    cluster_html = "".join(f"""
      <tr>
        <td><b>{esc(c['name'])}</b></td><td>{fmt_int(c['hosts'])}</td><td>{fmt_int(c['vms'])}</td>
        <td>{fmt_num(c['model']['cpuOvercommit'],2,'x')}</td>
        <td>{bar_cell(c['model']['cpuPct'], c['cpuStatus']['cls'])}</td>
        <td>{fmt_num(c['model']['ramOvercommit'],2,'x')}</td>
        <td>{bar_cell(c['model']['ramPct'], c['ramStatus']['cls'])}</td>
        {f"<td>{bar_cell(c['model']['storagePct'], capacity.get_status_for_pct(c['model']['storagePct'], 80, 90)['cls'], max_scale=100.0)}</td>" if include_storage else ""}
        <td>{status_pill(c['overall'])}</td>
      </tr>""" for c in cluster_rows)

    host_cpu_chart = svg_ranked_bar_chart(
        [{"name": h.get("host"), "pct": h.get("cpuPct"), "cls": h["cpuStatus"]["cls"]} for h in host_rows],
        width=320, top_n=10, name_max_len=28)
    host_ram_chart = svg_ranked_bar_chart(
        [{"name": h.get("host"), "pct": h.get("ramPct"), "cls": h["ramStatus"]["cls"]} for h in host_rows],
        width=320, top_n=10, name_max_len=28)
    ds_chart = svg_ranked_bar_chart(
        [{"name": d["name"], "pct": d["pct"], "cls": d["status"]["cls"]} for d in ds_rows],
        width=680, height=None, max_scale=100.0, name_max_len=20, top_n=10) if include_storage else ""

    host_html = "".join(f"""
      <tr>
        <td>{esc(h.get('cluster'))}</td><td><b>{esc(h.get('host'))}</b></td>
        <td>{fmt_int(h.get('cores'))}</td><td>{fmt_int(h.get('ramGB'))}</td>
        <td>{fmt_int(h.get('vcpuUsed'))}</td><td>{bar_cell(h.get('cpuPct'), h['cpuStatus']['cls'])}</td>
        <td>{fmt_num(h.get('ramUsedGB'),1)}</td><td>{bar_cell(h.get('ramPct'), h['ramStatus']['cls'])}</td>
        <td>{fmt_int(h.get('vmCount'))}</td><td>{status_pill(h['overall'])}</td>
      </tr>""" for h in host_rows)

    ds_html = "".join(f"""
      <tr>
        <td><b>{esc(d['name'])}</b></td><td>{fmt_num(d['provGB'],1)}</td><td>{fmt_num(d['usedGB'],1)}</td>
        <td>{bar_cell(d['pct'], d['status']['cls'])}</td><td>{fmt_int(d['vmCount'])}</td><td>{status_pill(d['status'])}</td>
      </tr>""" for d in ds_rows) if ds_rows else '<tr><td colspan="6" class="small-note">Không có dữ liệu datastore.</td></tr>'

    findings_sorted = sorted(findings, key=lambda f: ({"critical": 0, "warning": 1, "info": 2}.get(f.get("severity"), 3),
                                                       str(f.get("cluster") or ""), str(f.get("entity") or "")))
    findings_html = "".join(f"""
      <tr>
        <td class="code-cell">{esc(f.get('code'))}</td>
        <td>{status_pill({'text': SEVERITY_LABEL_VI.get(f.get('severity'), ''), 'cls': SEVERITY_COLOR.get(f.get('severity'), ('na','na_bg'))[0]})}</td>
        <td>{esc(f.get('typeLabel'))}</td>
        <td><b>{esc(f.get('entity'))}</b>{f'<div class="small-note" style="margin-top:1px;">{esc(f.get("vcenterKey"))}</div>' if f.get('vcenterKey') else ''}</td>
        <td>{esc(f.get('cluster') or '—')}</td>
        <td>{esc(f.get('metric'))}</td>
        <td style="max-width:180px;">{esc(f.get('recommendation'))}</td>
      </tr>""" for f in findings_sorted) if findings else '<tr><td colspan="7" class="small-note">Không có phát hiện nào.</td></tr>'

    logo_html = f'<img class="cover-logo" src="{esc(logo_data_url)}"/>' if logo_data_url else ''

    datastore_sections_html = f"""
  <div class="section">
    <div class="section-title">Top-10 Datastore theo % sử dụng</div>
    <div class="chart-card">{ds_chart}</div>
  </div>

  <div class="section">
    <div class="section-title">Utilization theo Datastore</div>
    <table>
      <thead><tr><th>Datastore</th><th>Provisioned GB</th><th>Used GB</th><th>% Storage</th><th>VM</th><th>Trạng thái</th></tr></thead>
      <tbody>{ds_html}</tbody>
    </table>
  </div>
""" if include_storage else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo kỹ thuật', generated_at)}</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Technical Detail</div>
      <div class="cover-title">Báo cáo Kỹ thuật — Capacity Ops</div>
      <div class="cover-sub">Chi tiết overcommit, host{', datastore' if include_storage else ''} và compliance findings phục vụ vận hành kỹ thuật.</div>
    </div>
    {logo_html}
  </div>
  <div class="meta-row">
    <span>Phạm vi: <b>{esc(scope_label)}</b></span>
    <span>Thời điểm tạo báo cáo: <b>{generated_at}</b></span>
    <span>{esc(policy_note)}</span>
  </div>
  <div class="meta-row" style="margin-top:-10px;">
    <span class="chip">{fmt_int(model['hostCount'])} hosts</span>
    <span class="chip">{fmt_int(model['vmCount'])} VMs</span>
    <span class="chip">{fmt_int(sev['critical'])} nghiêm trọng</span>
    <span class="chip">{fmt_int(sev['warning'])} cảnh báo</span>
    <span class="chip">{fmt_int(sev['info'])} thông tin</span>
  </div>
  {storage_excluded_html}

  <div class="section">
    <div class="section-title">Overcommit theo Cluster</div>
    <table>
      <thead><tr><th>Cluster</th><th>Hosts</th><th>VMs</th><th>CPU x</th><th>% CPU</th><th>RAM x</th><th>% RAM</th>{storage_th_html}<th>Trạng thái</th></tr></thead>
      <tbody>{cluster_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">So sánh {metrics_label} theo Cluster</div>
    <div class="section-sub">Biểu đồ cột nhóm — mỗi cluster có {3 if include_storage else 2} cột ({metrics_label}) tính theo % ngưỡng cấu hình, để đối chiếu nhanh giữa các chỉ số thay vì chỉ xem từng bảng riêng lẻ.</div>
    <div class="chart-card">{cluster_comparison_chart}</div>
    <div class="legend">
      <span><span class="dot" style="background:{COLORS['primary']}"></span>% CPU</span>
      <span><span class="dot" style="background:{COLORS['accent']}"></span>% RAM</span>
      {storage_legend_html}
    </div>
  </div>

{history_html}

  <div class="section">
    <div class="section-title">Phân tích Headroom &amp; Khuyến nghị mở rộng</div>
    <div class="section-sub">Dư địa tuyệt đối theo đơn vị thực tế (vCPU/GB), số VM cỡ trung bình còn nạp được, và số core/RAM vật lý cần bổ sung cho các cluster đã vượt ngưỡng cấu hình.</div>
    {headroom_html}
    <table style="margin-top:10px;">
      <thead><tr><th>Chỉ số</th><th>Hiện tại</th><th>Tăng 5%/quý</th><th>Tăng 10%/quý</th><th>Tăng 20%/quý</th></tr></thead>
      <tbody>{growth_rows_html}</tbody>
    </table>
    <div class="assumption-note">Kịch bản tăng trưởng là GIẢ ĐỊNH (không khớp xu hướng lịch sử — dữ liệu snapshot hiện có chưa đủ chuỗi thời gian để khớp mô hình) dùng cho lập kế hoạch sơ bộ; số tháng tính tới khi chạm ngưỡng cấu hình ({growth_threshold_note}).</div>
    <table style="margin-top:10px;">
      <thead><tr><th>Cluster</th><th>% CPU</th><th>% RAM</th><th>Ưu tiên</th><th>Đề xuất bổ sung</th></tr></thead>
      <tbody>{actions_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Kiểm tra dự phòng N+1 (mất 1 host)</div>
    <div class="section-sub">Giả lập mất host lớn nhất trong từng cluster (theo core × hệ số HT + RAM), giữ nguyên toàn bộ VM đang bật, tính lại % overcommit trên các host còn lại.</div>
    <table>
      <thead><tr><th>Cluster</th><th>Host lớn nhất (giả lập mất)</th><th>% CPU / RAM sau khi mất host</th><th>Đáp ứng ngưỡng</th></tr></thead>
      <tbody>{ha_full_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Top-10 Host sử dụng cao nhất</div>
    <div class="section-sub">Màu thanh thể hiện trạng thái rủi ro của từng host theo chỉ số.</div>
    <div class="chart-row">
      <div class="chart-card"><div class="chart-card-title">% CPU</div>{host_cpu_chart}</div>
      <div class="chart-card"><div class="chart-card-title">% RAM</div>{host_ram_chart}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Chi tiết Host ({fmt_int(len(host_rows))})</div>
    <table>
      <thead><tr><th>Cluster</th><th>Host</th><th>Cores</th><th>RAM GB</th><th>vCPU dùng</th><th>% CPU</th><th>RAM dùng GB</th><th>% RAM</th><th>VM</th><th>Trạng thái</th></tr></thead>
      <tbody>{host_html}</tbody>
    </table>
  </div>

{datastore_sections_html}
  <div class="section">
    <div class="section-title">Compliance Findings đầy đủ ({fmt_int(len(findings))})</div>
    <div class="small-note" style="margin-bottom:4px;">Cột "Mã" là mã đối soát ổn định theo đối tượng và loại vấn đề, trùng với mã trích dẫn trong Báo cáo Điều hành (mục Khuyến nghị / Đối soát) và tab Compliance trên web. Sắp xếp theo mức độ: nghiêm trọng trước.</div>
    <table>
      <thead><tr><th>Mã</th><th>Mức độ</th><th>Loại</th><th>Đối tượng</th><th>Cluster</th><th>Chỉ số</th><th>Khuyến nghị</th></tr></thead>
      <tbody>{findings_html}</tbody>
    </table>
  </div>
{appendix_html}
</body></html>"""


def html_to_pdf_bytes(html_str: str) -> bytes:
    return HTML(string=html_str).write_pdf()


def build_report_pdf(view: str, hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                      scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None,
                      include_types: Optional[List[str]] = None, logo_data_url: Optional[str] = None,
                      history: Optional[Dict[str, Any]] = None, vm_changes: Optional[Dict[str, Any]] = None) -> bytes:
    """`history` / `vm_changes` come from app.history; None renders an
    explanatory empty state so callers without a DB keep working."""
    if view == "technical":
        html_str = render_technical_html(hosts, vms, policy, scope_label, run_meta, include_types, logo_data_url, history, vm_changes)
    else:
        html_str = render_executive_html(hosts, vms, policy, scope_label, run_meta, include_types, logo_data_url, history, vm_changes)
    return html_to_pdf_bytes(html_str)
