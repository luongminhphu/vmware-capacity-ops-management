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
import math
from typing import Any, Dict, List, Optional

from weasyprint import HTML

from . import capacity

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
    "primary": "#2563eb",
    "primary_dark": "#1d4ed8",
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
}

STATUS_COLOR = {"ok": ("ok", "ok_bg"), "warn": ("warn", "warn_bg"), "bad": ("bad", "bad_bg"),
                "crit": ("crit", "crit_bg"), "na": ("na", "na_bg")}

SEVERITY_COLOR = {"critical": ("crit", "crit_bg"), "warning": ("warn", "warn_bg"), "info": ("primary", "surface2")}
SEVERITY_LABEL_VI = {"critical": "Nghiêm trọng", "warning": "Cảnh báo", "info": "Thông tin"}


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


def bar_cell(pct: Optional[float], cls: str = "ok", width_px: int = 74) -> str:
    fg_key, _ = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    clamped = 0.0 if not _finite(pct) else max(0.0, min(140.0, pct))
    fill_w = min(100.0, clamped) / 140.0 * 100.0 if clamped > 100 else clamped / 140.0 * 100.0
    # scale bar to a 140%-max visual range so overcommit-over-100% is visible
    label = fmt_pct(pct)
    return (
        f'<div class="barcell" style="width:{width_px}px">'
        f'<div class="barcell-track"><div class="barcell-fill" style="width:{fill_w:.1f}%;background:{COLORS[fg_key]}"></div></div>'
        f'<div class="barcell-label">{esc(label)}</div></div>'
    )


def svg_donut(segments: List[Dict[str, Any]], size: int = 120, hole: float = 0.62) -> str:
    """segments: [{label, value, color}] — color is a hex string."""
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
    return f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">{"".join(arcs)}</svg>'


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

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Noto Sans, sans-serif">']
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


# ---------------------------------------------------------------------------
# Shared page chrome
# ---------------------------------------------------------------------------
def _base_css(report_title: str, generated_at: str) -> str:
    return f"""
@page {{
  size: A4;
  margin: 16mm 14mm 18mm 14mm;
  @top-left {{ content: "VMware Capacity Ops Management"; font-family:'Noto Sans',sans-serif; font-size:8.5px; color:{COLORS['faint']}; letter-spacing:.04em; text-transform:uppercase; }}
  @top-right {{ content: "{report_title}"; font-family:'Noto Sans',sans-serif; font-size:8.5px; color:{COLORS['faint']}; letter-spacing:.04em; text-transform:uppercase; }}
  @bottom-left {{ content: "Tạo lúc {generated_at}"; font-family:'Noto Sans',sans-serif; font-size:8px; color:{COLORS['faint']}; }}
  @bottom-right {{ content: "Trang " counter(page) " / " counter(pages); font-family:'Noto Sans',sans-serif; font-size:8px; color:{COLORS['faint']}; }}
}}
* {{ box-sizing:border-box; }}
body {{ font-family:'Noto Sans',sans-serif; color:{COLORS['ink']}; font-size:10.3px; line-height:1.5; }}
h1,h2,h3,h4 {{ font-family:'Noto Sans',sans-serif; font-weight:700; color:{COLORS['ink']}; margin:0; }}
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
.barcell {{ display:flex; align-items:center; gap:6px; }}
.barcell-track {{ flex:1; height:5px; border-radius:99px; background:{COLORS['surface2']}; overflow:hidden; }}
.barcell-fill {{ height:100%; border-radius:99px; }}
.barcell-label {{ font-size:8px; color:{COLORS['muted']}; font-variant-numeric:tabular-nums; min-width:32px; text-align:right; }}
.insight-box {{ background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-left:3px solid {COLORS['accent']}; border-radius:0 6px 6px 0; padding:9px 12px; margin-bottom:8px; font-size:9.5px; }}
.insight-box b {{ color:{COLORS['primary_dark']}; }}
.insight-box.risk {{ border-left-color:{COLORS['crit']}; }}
.two-col {{ display:flex; gap:14px; }}
.two-col > div {{ flex:1; }}
.legend {{ display:flex; gap:12px; flex-wrap:wrap; font-size:8.5px; color:{COLORS['muted']}; margin-top:6px; }}
.legend .dot {{ width:7px; height:7px; border-radius:2px; display:inline-block; margin-right:4px; vertical-align:middle; }}
.small-note {{ font-size:8px; color:{COLORS['faint']}; margin-top:6px; }}
.chip {{ display:inline-block; padding:1px 6px; border-radius:99px; background:{COLORS['surface2']}; color:{COLORS['muted']}; font-size:8px; font-weight:600; margin-right:4px; }}
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
                           scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None) -> str:
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    findings = capacity.build_compliance_findings(hosts, vms, policy)
    sev = _severity_counts(findings)
    overall = _overall_status(model)
    generated_at = _now_str()
    insights = _executive_insights(model, cluster_rows, findings, policy)

    powered_on, powered_off = model["poweredOnCount"], model["poweredOffCount"]
    donut = svg_donut([
        {"label": "Powered On", "value": powered_on, "color": COLORS["ok"]},
        {"label": "Powered Off", "value": powered_off, "color": COLORS["na"]},
    ], size=104)

    cluster_labels = [c["name"] for c in cluster_rows]
    cluster_chart = svg_bar_chart(
        cluster_labels,
        [
            {"name": "CPU %", "color": COLORS["primary"], "values": [c["model"]["cpuPct"] if _finite(c["model"]["cpuPct"]) else 0 for c in cluster_rows]},
            {"name": "RAM %", "color": COLORS["accent"], "values": [c["model"]["ramPct"] if _finite(c["model"]["ramPct"]) else 0 for c in cluster_rows]},
        ],
        width=430, height=190,
    )

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

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo điều hành', generated_at)}</style></head>
<body>
  <div class="eyebrow">Infrastructure Capacity Ops · Executive Summary</div>
  <div class="cover-title">Báo cáo Điều hành — Capacity Ops</div>
  <div class="cover-sub">Tổng quan năng lực hạ tầng ảo hóa VMware phục vụ ra quyết định đầu tư và mở rộng.</div>
  <div class="meta-row">
    <span>Phạm vi: <b>{esc(scope_label)}</b></span>
    <span>Thời điểm tạo báo cáo: <b>{generated_at}</b></span>
    <span>Trạng thái tổng thể: {status_pill(overall)}</span>
  </div>
  {f'<div class="small-note" style="margin:-10px 0 14px;">Dữ liệu tổng hợp từ: {scope_note}</div>' if scope_note else ''}

  <div class="section">
    <div class="section-title">Tổng quan năng lực</div>
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

  <div class="section two-col">
    <div>
      <div class="section-title">Utilization theo Cluster</div>
      {cluster_chart}
      <div class="legend"><span><span class="dot" style="background:{COLORS['primary']}"></span>CPU %</span><span><span class="dot" style="background:{COLORS['accent']}"></span>RAM %</span></div>
    </div>
    <div style="flex:0 0 150px;text-align:center;">
      <div class="section-title" style="text-align:left;">VM Power State</div>
      {donut}
      <div class="legend" style="justify-content:center;"><span><span class="dot" style="background:{COLORS['ok']}"></span>On ({fmt_int(powered_on)})</span><span><span class="dot" style="background:{COLORS['na']}"></span>Off ({fmt_int(powered_off)})</span></div>
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
    <div class="section-title">Rủi ro compliance nổi bật ({fmt_int(sev['critical'])} nghiêm trọng · {fmt_int(sev['warning'])} cảnh báo · {fmt_int(sev['info'])} thông tin)</div>
    <table>
      <thead><tr><th>Mức độ</th><th>Đối tượng</th><th>Loại</th><th>Khuyến nghị</th></tr></thead>
      <tbody>{findings_html}</tbody>
    </table>
    <div class="small-note">Danh sách đầy đủ và chi tiết kỹ thuật xem tại Báo cáo Kỹ thuật hoặc Export CSV.</div>
  </div>
</body></html>"""


# ---------------------------------------------------------------------------
# Technical detail report
# ---------------------------------------------------------------------------
def render_technical_html(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: capacity.Policy,
                           scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None) -> str:
    model = capacity.build_capacity_model(hosts, vms, policy)
    cluster_rows = capacity.build_cluster_rows(hosts, vms, policy)
    host_rows = sorted(capacity.build_host_rows(hosts, vms, policy),
                        key=lambda h: max(h["cpuPct"] if _finite(h["cpuPct"]) else -1, h["ramPct"] if _finite(h["ramPct"]) else -1),
                        reverse=True)
    ds_rows = capacity.build_datastore_rows(vms)
    findings = sorted(capacity.build_compliance_findings(hosts, vms, policy),
                       key=lambda f: {"critical": 0, "warning": 1, "info": 2}.get(f.get("severity"), 3))
    generated_at = _now_str()
    sev = _severity_counts(findings)

    policy_note = (f"Ngưỡng overcommit CPU {policy.cpu_overcommit_max:.1f}x · RAM {policy.ram_overcommit_max:.1f}x · "
                   f"Hệ số HT {policy.ht_factor:.1f}x · Ngưỡng idle {policy.idle_days} ngày")

    cluster_html = "".join(f"""
      <tr>
        <td><b>{esc(c['name'])}</b></td><td>{fmt_int(c['hosts'])}</td><td>{fmt_int(c['vms'])}</td>
        <td>{fmt_num(c['model']['cpuOvercommit'],2,'x')}</td>
        <td>{bar_cell(c['model']['cpuPct'], c['cpuStatus']['cls'])}</td>
        <td>{fmt_num(c['model']['ramOvercommit'],2,'x')}</td>
        <td>{bar_cell(c['model']['ramPct'], c['ramStatus']['cls'])}</td>
        <td>{fmt_pct(c['model']['storagePct'])}</td>
        <td>{status_pill(c['overall'])}</td>
      </tr>""" for c in cluster_rows)

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

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo kỹ thuật', generated_at)}</style></head>
<body>
  <div class="eyebrow">Infrastructure Capacity Ops · Technical Detail</div>
  <div class="cover-title">Báo cáo Kỹ thuật — Capacity Ops</div>
  <div class="cover-sub">Chi tiết overcommit, host, datastore và compliance findings phục vụ vận hành kỹ thuật.</div>
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
      <thead><tr><th>Cluster</th><th>Hosts</th><th>VMs</th><th>CPU x</th><th>CPU %ngưỡng</th><th>RAM x</th><th>RAM %ngưỡng</th><th>Storage %</th><th>Trạng thái</th></tr></thead>
      <tbody>{cluster_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Chi tiết Host ({fmt_int(len(host_rows))})</div>
    <table>
      <thead><tr><th>Cluster</th><th>Host</th><th>Cores</th><th>RAM GB</th><th>vCPU dùng</th><th>CPU %ngưỡng</th><th>RAM dùng GB</th><th>RAM %ngưỡng</th><th>VM</th><th>Trạng thái</th></tr></thead>
      <tbody>{host_html}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Utilization theo Datastore</div>
    <table>
      <thead><tr><th>Datastore</th><th>Provisioned GB</th><th>Used GB</th><th>% Used</th><th>VM</th><th>Trạng thái</th></tr></thead>
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
                      scope_label: str, run_meta: Optional[List[Dict[str, Any]]] = None) -> bytes:
    if view == "technical":
        html_str = render_technical_html(hosts, vms, policy, scope_label, run_meta)
    else:
        html_str = render_executive_html(hosts, vms, policy, scope_label, run_meta)
    return html_to_pdf_bytes(html_str)
