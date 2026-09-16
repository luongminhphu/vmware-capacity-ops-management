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
from typing import Any, Dict, List, Optional

from weasyprint import HTML

from . import capacity

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


def bar_cell(pct: Optional[float], cls: str = "ok", width_px: int = 80, height_px: int = 15, max_scale: float = 140.0) -> str:
    """Progress-bar cell with the percentage label NESTED INSIDE the colored
    fill, for a compact, professional look.

    Rendered as inline SVG (not HTML/CSS). Earlier versions centered the
    label over the *whole* track and used a stroke-halo double text-draw to
    keep it legible on top of both the colored fill and the plain track --
    on WeasyPrint that stroke halo rendered as a smudgy double image. A
    clip-path variant (white text clipped to the fill, muted text clipped to
    the track, both centered on the whole bar) avoided the halo but could
    still split a single glyph in half right at the fill boundary.

    This version instead widens the colored "chip" so it is never narrower
    than what the label itself needs -- the label is always drawn exactly
    once, centered, solid white, fully inside the colored fill. The chip
    still grows with pct (larger values render a visibly longer bar) but
    never shrinks below the label's own width, so the number is always
    legibly nested inside the bar instead of spilling onto the plain track.
    """
    fg_key, _ = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    color = COLORS[fg_key]
    clamped = 0.0 if not _finite(pct) else max(0.0, min(max_scale, pct))
    fill_w = max(0.0, min(1.0, clamped / max_scale)) * width_px
    r = height_px / 2.0
    cy = height_px / 2.0
    font_size = round(height_px * 0.6, 1)
    label = fmt_pct(pct)
    text_y = round(cy + font_size * 0.35, 1)
    label_w_est = len(label) * font_size * 0.62 + 10
    fill_w_draw = min(width_px, max(height_px, fill_w, label_w_est))
    cx = fill_w_draw / 2.0
    return (
        f'<svg width="{width_px}" height="{height_px}" viewBox="0 0 {width_px} {height_px}" '
        f'style="display:block;vertical-align:middle;">'
        f'<rect x="0" y="0" width="{width_px}" height="{height_px}" rx="{r:.1f}" fill="{COLORS["track_bg"]}"/>'
        f'<rect x="0" y="0" width="{fill_w_draw:.1f}" height="{height_px}" rx="{r:.1f}" fill="{color}"/>'
        f'<text x="{cx:.1f}" y="{text_y}" text-anchor="middle" font-family="Roboto, Noto Sans, sans-serif" '
        f'font-size="{font_size}" font-weight="700" fill="#ffffff">{esc(label)}</text>'
        f'</svg>'
    )


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
    arcs = []
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


def svg_gauge(pct: Optional[float], color: str, size: int = 132, max_scale: float = 100.0,
              label_top: Optional[str] = None) -> str:
    """Compact semicircular gauge: arc sweep proportional to `pct` (capped at
    `max_scale`), with the percentage rendered large in the center. Used for
    headline CPU/RAM/Storage utilization callouts so the Executive report
    reads at a glance instead of only through KPI numbers.

    The drawn arc always sweeps at most 180 degrees (from the 9-o'clock point
    to some point along the top half of the circle), so it is always the
    *minor* arc between its two endpoints -- the SVG large-arc-flag must
    therefore always be 0. Do not derive it from `frac`: a previous version
    flipped it to 1 once `frac > 0.5`, which forced the renderer to draw the
    reflex (major, >180 degree) arc instead -- it looped around through the
    hidden bottom half of the circle, mostly falling outside the viewBox and
    rendering as a broken-looking fragment (this is exactly what happened once
    a gauge value crossed 50% of `max_scale`, e.g. Storage above 50%)."""
    w = float(size)
    stroke_w = w * 0.10
    r = w / 2 - stroke_w / 2 - 3
    cx, cy = w / 2, w / 2 - 2
    h = w / 2 + stroke_w / 2 + 26

    def polar(deg: float):
        rad = math.radians(deg)
        return cx + r * math.cos(rad), cy - r * math.sin(rad)

    x0, y0 = polar(180)
    x1, y1 = polar(0)
    clamped = 0.0 if not _finite(pct) else max(0.0, min(max_scale, pct))
    frac = clamped / max_scale
    end_deg = 180 - 180 * frac
    xe, ye = polar(end_deg)
    large_arc = 0
    fg_path = f'<path d="M {x0:.1f} {y0:.1f} A {r:.1f} {r:.1f} 0 {large_arc} 1 {xe:.1f} {ye:.1f}" fill="none" stroke="{color}" stroke-width="{stroke_w:.1f}" stroke-linecap="round"/>' if frac > 0.008 else ""
    label = fmt_pct(pct, 0)
    top = f'<text x="{cx:.1f}" y="{h-6:.1f}" text-anchor="middle" font-family="Roboto, Noto Sans, sans-serif" font-size="8.5" font-weight="700" fill="{COLORS["muted"]}" text-transform="uppercase">{esc(label_top)}</text>' if label_top else ""
    return f'''<svg width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}" font-family="Roboto, Noto Sans, sans-serif">
      <path d="M {x0:.1f} {y0:.1f} A {r:.1f} {r:.1f} 0 0 1 {x1:.1f} {y1:.1f}" fill="none" stroke="{COLORS['track_bg']}" stroke-width="{stroke_w:.1f}" stroke-linecap="round"/>
      {fg_path}
      <text x="{cx:.1f}" y="{cy+2:.1f}" text-anchor="middle" font-size="{w*0.155:.1f}" font-weight="800" fill="{COLORS['ink']}">{label}</text>
      {top}
    </svg>'''


def svg_bar_chart(labels: List[str], series: List[Dict[str, Any]], width: int = 720, height: int = 220,
                   max_value: Optional[float] = None) -> str:
    """series: [{name, color, values}] grouped vertical bars, one group per label."""
    if not labels:
        return '<div style="color:#98a2b3;font-size:11px;padding:8px 0;">Không có dữ liệu.</div>'
    pad_l, pad_r, pad_t, pad_b = 34, 8, 10, 22
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_vals = [v for s in series for v in s["values"] if _finite(v)]
    mx = max_value if max_value is not None else (max(all_vals) if all_vals else 1.0)
    mx = mx if mx > 0 else 1.0
    n_groups = len(labels)
    n_series = max(1, len(series))
    group_w = plot_w / n_groups
    bar_gap = group_w * 0.18
    bar_w = (group_w - bar_gap) / n_series

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Roboto, Noto Sans, sans-serif">']
    # gridlines + y-axis labels (4 steps)
    for i in range(5):
        frac = i / 4
        y = pad_t + plot_h * (1 - frac)
        val = mx * frac
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" stroke="{COLORS["divider"]}" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-6}" y="{y+3:.1f}" text-anchor="end" font-size="8.5" fill="{COLORS["faint"]}">{val:.0f}</text>')
    for gi, label in enumerate(labels):
        gx = pad_l + gi * group_w + bar_gap / 2
        for si, s in enumerate(series):
            v = s["values"][gi] if gi < len(s["values"]) else 0
            v = v if _finite(v) else 0
            bh = max(0.0, min(1.0, v / mx)) * plot_h
            bx = gx + si * bar_w
            by = pad_t + plot_h - bh
            parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{max(1,bar_w-2):.1f}" height="{bh:.1f}" rx="1.5" fill="{s["color"]}"/>')
        lx = pad_l + gi * group_w + group_w / 2
        short = label if len(label) <= 12 else label[:11] + "…"
        parts.append(f'<text x="{lx:.1f}" y="{height-6}" text-anchor="middle" font-size="8" fill="{COLORS["muted"]}">{esc(short)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_ranked_bar_chart(rows: List[Dict[str, Any]], width: int = 330, height: Optional[int] = None,
                          max_scale: float = 140.0, name_max_len: int = 13, top_n: int = 10) -> str:
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
        name = name if len(name) <= name_max_len else name[:name_max_len - 1] + "…"
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
        badge_n = r.get("badge")
        if badge_n:
            badge_title = r.get("badge_title", "")
            badge_color = COLORS["crit"] if r.get("badge_crit") else COLORS["warn"]
            bx = pad_l + plot_w + 22
            parts.append(f'<circle cx="{bx}" cy="{bar_y+bar_h/2:.1f}" r="7" fill="{badge_color}"><title>{esc(badge_title)}</title></circle>')
            parts.append(f'<text x="{bx}" y="{bar_y+bar_h/2+2.8:.1f}" text-anchor="middle" font-size="7.5" font-weight="700" fill="#ffffff">{badge_n}<title>{esc(badge_title)}</title></text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_cluster_risk_chart(cluster_rows: List[Dict[str, Any]], metric: str, finding_counts: Dict[str, Dict[str, int]],
                            width: int = 330, height: Optional[int] = None) -> str:
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
    return svg_ranked_bar_chart(rows, width=width, height=height)


# ---------------------------------------------------------------------------
# Shared page chrome
# ---------------------------------------------------------------------------
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
.section-title {{ font-size:13px; font-weight:700; color:{COLORS['ink']}; border-left:3px solid {COLORS['primary']}; padding-left:8px; margin-bottom:8px; }}
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
"""


def _now_str() -> str:
    return dt.datetime.now().strftime("%H:%M %d/%m/%Y")


def _overall_status(model: Dict[str, Any]) -> Dict[str, str]:
    cpu_status = capacity.get_status_for_pct(model.get("cpuPct"), 80, 100)
    ram_status = capacity.get_status_for_pct(model.get("ramPct"), 80, 100)
    return capacity.combine_status(cpu_status, ram_status)


def _severity_counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        out[f.get("severity", "info")] = out.get(f.get("severity", "info"), 0) + 1
    return out


def _executive_insights(model: Dict[str, Any], cluster_rows: List[Dict[str, Any]],
                         findings: List[Dict[str, Any]], policy: capacity.Policy) -> List[Dict[str, str]]:
    insights = []
    cpu_pct, ram_pct, storage_pct = model.get("cpuPct"), model.get("ramPct"), model.get("storagePct")
    sev = _severity_counts(findings)

    if _finite(cpu_pct) and cpu_pct >= 100:
        insights.append({"risk": True, "title": "CPU đã vượt ngưỡng overcommit toàn hệ thống",
                          "body": f"Overcommit CPU hiện tại {model.get('cpuOvercommit', float('nan')):.2f}x, vượt ngưỡng cấu hình {policy.cpu_overcommit_max:.1f}x. Cần bổ sung năng lực CPU hoặc cân bằng lại tải giữa các cluster trong thời gian ngắn hạn."})
    elif _finite(cpu_pct) and cpu_pct >= 80:
        insights.append({"risk": False, "title": "CPU đang tiến gần ngưỡng overcommit",
                          "body": f"Sử dụng {cpu_pct:.0f}% ngưỡng CPU overcommit cho phép ({policy.cpu_overcommit_max:.1f}x). Nên đưa vào kế hoạch mở rộng trong 1-2 quý tới để tránh nghẽn năng lực."})

    if _finite(ram_pct) and ram_pct >= 100:
        insights.append({"risk": True, "title": "RAM đã vượt ngưỡng overcommit toàn hệ thống",
                          "body": f"Overcommit RAM hiện tại {model.get('ramOvercommit', float('nan')):.2f}x, vượt ngưỡng cấu hình {policy.ram_overcommit_max:.1f}x. Rủi ro cao về hiệu năng — cần bổ sung RAM vật lý hoặc thu hồi tài nguyên từ VM idle."})
    elif _finite(ram_pct) and ram_pct >= 80:
        insights.append({"risk": False, "title": "RAM đang tiến gần ngưỡng overcommit",
                          "body": f"Sử dụng {ram_pct:.0f}% ngưỡng RAM overcommit cho phép ({policy.ram_overcommit_max:.1f}x). Cân nhắc bổ sung RAM hoặc rà soát VM cấp phát dư thừa."})

    if _finite(storage_pct) and storage_pct >= 90:
        insights.append({"risk": True, "title": "Datastore tổng thể gần đầy",
                          "body": f"Tỷ lệ sử dụng dung lượng đã cấp phát đạt {storage_pct:.0f}%. Cần rà soát thin-provisioning, dọn snapshot cũ hoặc bổ sung dung lượng."})

    risky_clusters = [c for c in cluster_rows if c["overall"]["cls"] in ("bad", "crit")]
    if risky_clusters:
        names = ", ".join(c["name"] for c in risky_clusters[:5])
        insights.append({"risk": True, "title": f"{len(risky_clusters)} cluster cần đầu tư/mở rộng ưu tiên",
                          "body": f"Các cluster có mức sử dụng vượt ngưỡng cảnh báo: {names}. Đề xuất ưu tiên phân bổ ngân sách mở rộng host cho các cluster này trước."})

    if sev["critical"] > 0:
        insights.append({"risk": True, "title": f"{sev['critical']} phát hiện compliance ở mức nghiêm trọng",
                          "body": "Cần xử lý ngay để giảm rủi ro downtime hoặc mất khả năng đáp ứng SLA. Xem chi tiết tại báo cáo kỹ thuật."})

    idle_findings = [f for f in findings if f.get("type") in ("vm_idle", "vm_off")]
    if len(idle_findings) >= 5:
        insights.append({"risk": False, "title": f"{len(idle_findings)} VM idle/tắt lâu ngày có thể thu hồi",
                          "body": "Thu hồi tài nguyên từ các VM này giúp giải phóng năng lực mà không cần đầu tư phần cứng mới — nên xem xét trước khi phê duyệt mở rộng."})

    if not insights:
        insights.append({"risk": False, "title": "Hạ tầng đang trong ngưỡng an toàn",
                          "body": "Không phát hiện rủi ro trọng yếu về năng lực CPU/RAM/Storage tại thời điểm báo cáo. Tiếp tục theo dõi định kỳ theo chu kỳ polling hiện tại."})
    return insights[:6]


# ---------------------------------------------------------------------------
# Executive report
# ---------------------------------------------------------------------------
def render_executive_html(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                           scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None,
                           include_types: Optional[List[str]] = None, logo_data_url: Optional[str] = None) -> str:
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    all_findings = capacity.build_compliance_findings(hosts, vms, policy)
    type_filter = set(include_types) if include_types is not None else None
    findings = [f for f in all_findings if type_filter is None or f.get("type") in type_filter]
    sev = _severity_counts(findings)
    cluster_finding_counts: Dict[str, Dict[str, int]] = {}
    for f in findings:
        key = f.get("cluster") or "—"
        d = cluster_finding_counts.setdefault(key, {"critical": 0, "warning": 0, "info": 0})
        d[f.get("severity", "info")] = d.get(f.get("severity", "info"), 0) + 1
    overall = _overall_status(model)
    generated_at = _now_str()
    insights = _executive_insights(model, cluster_rows, findings, policy)

    powered_on, powered_off = model["poweredOnCount"], model["poweredOffCount"]
    donut = svg_donut([
        {"label": "Powered On", "value": powered_on, "color": COLORS["ok"]},
        {"label": "Powered Off", "value": powered_off, "color": COLORS["na"]},
    ], size=88, center_label=fmt_int(powered_on + powered_off), center_sub="VM")

    cpu_risk_chart = svg_cluster_risk_chart(cluster_rows, "cpu", cluster_finding_counts, width=246)
    ram_risk_chart = svg_cluster_risk_chart(cluster_rows, "ram", cluster_finding_counts, width=246)

    # Headline utilization gauges — give the cover-page numbers a chart, not
    # just text, for a livelier and more scannable first impression.
    cpu_status = capacity.get_status_for_pct(model.get("cpuPct"), 80, 100)
    ram_status = capacity.get_status_for_pct(model.get("ramPct"), 80, 100)
    ds_status = capacity.get_status_for_pct(model.get("storagePct"), 80, 90)
    gauge_cpu = svg_gauge(model.get("cpuPct"), COLORS[STATUS_COLOR.get(cpu_status["cls"], STATUS_COLOR["na"])[0]])
    gauge_ram = svg_gauge(model.get("ramPct"), COLORS[STATUS_COLOR.get(ram_status["cls"], STATUS_COLOR["na"])[0]])
    gauge_ds = svg_gauge(model.get("storagePct"), COLORS[STATUS_COLOR.get(ds_status["cls"], STATUS_COLOR["na"])[0]])

    findings_donut = svg_donut([
        {"label": "Nghiêm trọng", "value": sev["critical"], "color": COLORS["crit"]},
        {"label": "Cảnh báo", "value": sev["warning"], "color": COLORS["warn"]},
        {"label": "Thông tin", "value": sev["info"], "color": COLORS["primary"]},
    ], size=88, center_label=fmt_int(sev["critical"] + sev["warning"] + sev["info"]), center_sub="Finding")

    top_risk_clusters = sorted(cluster_rows, key=lambda c: max(
        c["model"]["cpuPct"] if _finite(c["model"]["cpuPct"]) else -1,
        c["model"]["ramPct"] if _finite(c["model"]["ramPct"]) else -1), reverse=True)[:8]

    cluster_rows_html = "".join(f"""
      <tr>
        <td><b>{esc(c['name'])}</b></td>
        <td>{fmt_int(c['hosts'])}</td>
        <td>{fmt_int(c['vms'])}</td>
        <td>{bar_cell(c['model']['cpuPct'], c['cpuStatus']['cls'])}</td>
        <td>{bar_cell(c['model']['ramPct'], c['ramStatus']['cls'])}</td>
        <td>{status_pill(c['overall'])}</td>
      </tr>""" for c in top_risk_clusters)

    top_findings = sorted(findings, key=lambda f: {"critical": 0, "warning": 1, "info": 2}.get(f.get("severity"), 3))[:10]
    findings_html = "".join(f"""
      <tr>
        <td>{status_pill({'text': SEVERITY_LABEL_VI.get(f.get('severity'), ''), 'cls': SEVERITY_COLOR.get(f.get('severity'), ('na','na_bg'))[0]})}</td>
        <td><b>{esc(f.get('entity'))}</b><div class="small-note" style="margin-top:1px;">{esc(f.get('cluster') or '')}</div></td>
        <td>{esc(f.get('typeLabel'))}</td>
        <td style="max-width:230px;">{esc(f.get('recommendation'))}</td>
      </tr>""" for f in top_findings) if top_findings else '<tr><td colspan="4" class="small-note">Không có phát hiện nào.</td></tr>'

    insights_html = "".join(f"""
      <div class="insight-box{' risk' if ins['risk'] else ''}"><b>{esc(ins['title'])}</b><br/>{esc(ins['body'])}</div>
    """ for ins in insights)

    scope_note = ""
    if run_meta:
        parts = []
        for rm in run_meta:
            ts = rm.get("finishedAt")
            ts_fmt = ts[:16].replace("T", " ") if ts else "—"
            parts.append(f"{esc(rm.get('vcenterKey'))} ({ts_fmt})")
        scope_note = " · ".join(parts)

    filter_note = ""
    if type_filter is not None and len(type_filter) < len(ALL_FINDING_TYPES):
        excluded = [FINDING_TYPE_LABEL_VI.get(t, t) for t in ALL_FINDING_TYPES if t not in type_filter]
        filter_note = f"Đã loại {len(excluded)}/{len(ALL_FINDING_TYPES)} loại finding khỏi báo cáo: {', '.join(excluded)}."

    logo_html = f'<img class="cover-logo" src="{esc(logo_data_url)}"/>' if logo_data_url else ''

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo điều hành', generated_at)}</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Executive Summary</div>
      <div class="cover-title">Báo cáo Điều hành — Capacity Ops</div>
      <div class="cover-sub">Tổng quan năng lực hạ tầng ảo hóa VMware phục vụ ra quyết định đầu tư và mở rộng.</div>
    </div>
    {logo_html}
  </div>
  <div class="meta-row">
    <span>Phạm vi: <b>{esc(scope_label)}</b></span>
    <span>Thời điểm tạo báo cáo: <b>{generated_at}</b></span>
    <span>Trạng thái tổng thể: {status_pill(overall)}</span>
  </div>

  <div class="section">
    <div class="section-title">Tổng quan năng lực</div>
    <div class="gauge-row">
      <div class="gauge-card">{gauge_cpu}<div class="kpi-label" style="margin-top:2px;">CPU Usage</div></div>
      <div class="gauge-card">{gauge_ram}<div class="kpi-label" style="margin-top:2px;">RAM Usage</div></div>
      <div class="gauge-card">{gauge_ds}<div class="kpi-label" style="margin-top:2px;">Storage Usage</div></div>
    </div>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">CPU (pCPU logic)</div><div class="kpi-value">{fmt_int(model['usedVcpu'])} / {fmt_int(model['totalLogicalCores'])}</div><div class="kpi-sub">Overcommit {fmt_num(model['cpuOvercommit'],2,'x')} · {fmt_pct(model['cpuPct'])} ngưỡng</div></div>
      <div class="kpi-card"><div class="kpi-label">RAM (GB)</div><div class="kpi-value">{fmt_int(model['usedRamGB'])} / {fmt_int(model['totalRamGB'])}</div><div class="kpi-sub">Overcommit {fmt_num(model['ramOvercommit'],2,'x')} · {fmt_pct(model['ramPct'])} ngưỡng</div></div>
      <div class="kpi-card"><div class="kpi-label">Storage (GB)</div><div class="kpi-value">{fmt_int(model['usedStorageGB'])} / {fmt_int(model['provStorageGB'])}</div><div class="kpi-sub">{fmt_pct(model['storagePct'])} đã dùng / cấp phát</div></div>
      <div class="kpi-card"><div class="kpi-label">Hosts / VMs</div><div class="kpi-value">{fmt_int(model['hostCount'])} / {fmt_int(model['vmCount'])}</div><div class="kpi-sub">{fmt_int(powered_on)} bật · {fmt_int(powered_off)} tắt</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Nhận định &amp; đề xuất đầu tư</div>
    {insights_html}
  </div>

  <div class="section">
    <div class="section-title">Rủi ro theo Cluster</div>
    <div class="section-sub">Màu thanh thể hiện trạng thái rủi ro của từng cluster (không phải màu cố định theo chỉ số). Số trong vòng tròn ở cuối thanh là số finding nghiêm trọng/cảnh báo của cluster đó.</div>
    <div class="two-col">
      <div>
        <div class="section-title" style="font-size:10.5px;border-left:none;padding-left:0;">% CPU</div>
        {cpu_risk_chart}
      </div>
      <div>
        <div class="section-title" style="font-size:10.5px;border-left:none;padding-left:0;">% RAM</div>
        {ram_risk_chart}
      </div>
      <div style="flex:0 0 118px;text-align:center;">
        <div class="section-title" style="font-size:10.5px;border-left:none;padding-left:0;text-align:left;">VM Power State</div>
        {donut}
        <div class="legend" style="justify-content:center;flex-direction:column;gap:2px;"><span><span class="dot" style="background:{COLORS['ok']}"></span>On ({fmt_int(powered_on)})</span><span><span class="dot" style="background:{COLORS['na']}"></span>Off ({fmt_int(powered_off)})</span></div>
      </div>
    </div>
    <div class="legend">
      <span><span class="dot" style="background:{COLORS['ok']}"></span>An toàn</span>
      <span><span class="dot" style="background:{COLORS['warn']}"></span>Theo dõi</span>
      <span><span class="dot" style="background:{COLORS['bad']}"></span>Cảnh báo</span>
      <span><span class="dot" style="background:{COLORS['crit']}"></span>Vượt ngưỡng</span>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Cluster cần quan tâm nhất</div>
    <table>
      <thead><tr><th>Cluster</th><th>Hosts</th><th>VMs</th><th>CPU</th><th>RAM</th><th>Trạng thái</th></tr></thead>
      <tbody>{cluster_rows_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Rủi ro compliance nổi bật</div>
    <div class="chart-row">
      <div style="flex:0 0 118px;text-align:center;">
        {findings_donut}
        <div class="legend" style="justify-content:center;flex-direction:column;gap:2px;text-align:left;">
          <span><span class="dot" style="background:{COLORS['crit']}"></span>Nghiêm trọng ({fmt_int(sev['critical'])})</span>
          <span><span class="dot" style="background:{COLORS['warn']}"></span>Cảnh báo ({fmt_int(sev['warning'])})</span>
          <span><span class="dot" style="background:{COLORS['primary']}"></span>Thông tin ({fmt_int(sev['info'])})</span>
        </div>
      </div>
      <div style="flex:1;">
        <table>
          <thead><tr><th>Mức độ</th><th>Đối tượng</th><th>Loại</th><th>Khuyến nghị</th></tr></thead>
          <tbody>{findings_html}</tbody>
        </table>
      </div>
    </div>
    <div class="small-note">Danh sách đầy đủ và chi tiết kỹ thuật xem tại Báo cáo Kỹ thuật hoặc Export CSV.</div>
  </div>
</body></html>"""


# ---------------------------------------------------------------------------
# Technical detail report
# ---------------------------------------------------------------------------
def render_technical_html(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                           scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None,
                           include_types: Optional[List[str]] = None, logo_data_url: Optional[str] = None) -> str:
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    host_rows = sorted(capacity.build_host_rows(hosts, vms, policy),
                        key=lambda h: max(h["cpuPct"] if _finite(h["cpuPct"]) else -1, h["ramPct"] if _finite(h["ramPct"]) else -1),
                        reverse=True)
    ds_rows = capacity.build_datastore_rows(vms)
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
                   f"Hệ số HT {policy.ht_factor:.1f}x · Ngưỡng idle {policy.idle_days} ngày")

    cluster_html = "".join(f"""
      <tr>
        <td><b>{esc(c['name'])}</b></td><td>{fmt_int(c['hosts'])}</td><td>{fmt_int(c['vms'])}</td>
        <td>{fmt_num(c['model']['cpuOvercommit'],2,'x')}</td>
        <td>{bar_cell(c['model']['cpuPct'], c['cpuStatus']['cls'])}</td>
        <td>{fmt_num(c['model']['ramOvercommit'],2,'x')}</td>
        <td>{bar_cell(c['model']['ramPct'], c['ramStatus']['cls'])}</td>
        <td>{bar_cell(c['model']['storagePct'], capacity.get_status_for_pct(c['model']['storagePct'], 80, 90)['cls'], max_scale=100.0)}</td>
        <td>{status_pill(c['overall'])}</td>
      </tr>""" for c in cluster_rows)

    host_cpu_chart = svg_ranked_bar_chart(
        [{"name": h.get("host"), "pct": h.get("cpuPct"), "cls": h["cpuStatus"]["cls"]} for h in host_rows],
        width=330, top_n=10)
    host_ram_chart = svg_ranked_bar_chart(
        [{"name": h.get("host"), "pct": h.get("ramPct"), "cls": h["ramStatus"]["cls"]} for h in host_rows],
        width=330, top_n=10)
    ds_chart = svg_ranked_bar_chart(
        [{"name": d["name"], "pct": d["pct"], "cls": d["status"]["cls"]} for d in ds_rows],
        width=680, height=None, max_scale=100.0, name_max_len=20, top_n=10)

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

    findings_html = "".join(f"""
      <tr>
        <td>{status_pill({'text': SEVERITY_LABEL_VI.get(f.get('severity'), ''), 'cls': SEVERITY_COLOR.get(f.get('severity'), ('na','na_bg'))[0]})}</td>
        <td>{esc(f.get('typeLabel'))}</td>
        <td><b>{esc(f.get('entity'))}</b></td>
        <td>{esc(f.get('cluster') or '—')}</td>
        <td>{esc(f.get('metric'))}</td>
        <td style="max-width:190px;">{esc(f.get('recommendation'))}</td>
      </tr>""" for f in findings) if findings else '<tr><td colspan="6" class="small-note">Không có phát hiện nào.</td></tr>'

    logo_html = f'<img class="cover-logo" src="{esc(logo_data_url)}"/>' if logo_data_url else ''

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo kỹ thuật', generated_at)}</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Technical Detail</div>
      <div class="cover-title">Báo cáo Kỹ thuật — Capacity Ops</div>
      <div class="cover-sub">Chi tiết overcommit, host, datastore và compliance findings phục vụ vận hành kỹ thuật.</div>
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

  <div class="section">
    <div class="section-title">Overcommit theo Cluster</div>
    <table>
      <thead><tr><th>Cluster</th><th>Hosts</th><th>VMs</th><th>CPU x</th><th>% CPU</th><th>RAM x</th><th>% RAM</th><th>% Storage</th><th>Trạng thái</th></tr></thead>
      <tbody>{cluster_html}</tbody>
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

  <div class="section">
    <div class="section-title">Compliance Findings đầy đủ ({fmt_int(len(findings))})</div>
    <table>
      <thead><tr><th>Mức độ</th><th>Loại</th><th>Đối tượng</th><th>Cluster</th><th>Chỉ số</th><th>Khuyến nghị</th></tr></thead>
      <tbody>{findings_html}</tbody>
    </table>
  </div>
</body></html>"""


def html_to_pdf_bytes(html_str: str) -> bytes:
    return HTML(string=html_str).write_pdf()


def build_report_pdf(view: str, hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                      scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None,
                      include_types: Optional[List[str]] = None, logo_data_url: Optional[str] = None) -> bytes:
    if view == "technical":
        html_str = render_technical_html(hosts, vms, policy, scope_label, run_meta, include_types, logo_data_url)
    else:
        html_str = render_executive_html(hosts, vms, policy, scope_label, run_meta, include_types, logo_data_url)
    return html_to_pdf_bytes(html_str)
