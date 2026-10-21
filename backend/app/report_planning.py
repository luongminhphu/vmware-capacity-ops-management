"""PDF renderer for the Resource Planning report ("Báo cáo Quy hoạch tài
nguyên"): same header/summary layout as the Operations and Technical
reports, then current state + assumptions, demand projections vs capacity,
node roadmap per cluster and per-cluster proposals. Reuses the shared
formatting / chart helpers from report_pdf."""
from typing import Any, Dict, List, Optional

from . import planning
from .capacity import Policy, _finite
from .report_pdf import (COLORS, PRIORITY_LABEL_VI, STATUS_COLOR, _base_css, _exec_visual_css, _now_str,
                         _stat_tiles_html, esc, fmt_int, fmt_num, fmt_pct, html_to_pdf_bytes, status_pill,
                         svg_bar_chart)

SCENARIO_COLORS = ["#15803d", "#2681FF", "#c2410c", "#7c3aed"]  # low, base, high, trend


def _sc_color(i: int, sc: Dict[str, Any]) -> str:
    return SCENARIO_COLORS[3] if sc.get("source") == "trend" else SCENARIO_COLORS[i % 3]


def svg_projection_chart(values_by_series: List[Dict[str, Any]], month_labels: List[str], target_pct: float,
                         width: int = 330, height: int = 150, title: str = "") -> str:
    """Demand as % of the overcommit threshold over the horizon: one line per
    scenario, dashed red line at 100% and dashed amber line at the planning
    target. Values may be None (no capacity)."""
    n = len(month_labels)
    if n < 2:
        return '<div class="small-note">Không đủ dữ liệu.</div>'
    pad_l, pad_r, pad_t, pad_b = 30, 16, 14, 18
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    all_vals = [v for s in values_by_series for v in s["values"] if v is not None and _finite(v)]
    vmax = max([120.0, target_pct] + all_vals) if all_vals else 120.0
    vmax = min(vmax, 200.0)  # cap: runaway scenarios flatten at the top instead of squashing the rest
    vmax = (int(vmax / 20) + 1) * 20 if vmax < 200 else 200

    def x(i: int) -> float:
        return pad_l + pw * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + ph * (1 - min(v, vmax) / vmax)

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="DejaVu Sans, Arial" font-size="7">']
    parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}" fill="{COLORS["surface"]}" rx="3"/>')
    step = 20 if vmax <= 160 else 40
    for g in range(0, int(vmax) + 1, step):
        yy = y(g)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + pw}" y2="{yy:.1f}" stroke="#ffffff" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 4}" y="{yy + 2.5:.1f}" text-anchor="end" fill="{COLORS["muted"]}">{g}%</text>')
    # month labels: every 6 months
    for i in range(0, n, 6 if n <= 40 else 12):
        anchor = "start" if i == 0 else ("end" if i >= n - 3 else "middle")
        parts.append(f'<text x="{x(i):.1f}" y="{height - 5}" text-anchor="{anchor}" fill="{COLORS["muted"]}">{esc(month_labels[i])}</text>')
    # target + threshold lines
    yt = y(target_pct)
    parts.append(f'<line x1="{pad_l}" y1="{yt:.1f}" x2="{pad_l + pw}" y2="{yt:.1f}" stroke="{COLORS["warn"]}" stroke-width="1" stroke-dasharray="3,3"/>')
    parts.append(f'<text x="{pad_l + pw - 2}" y="{yt - 2.5:.1f}" text-anchor="end" fill="{COLORS["warn"]}" font-weight="700">Mục tiêu {target_pct:.0f}%</text>')
    y100 = y(100.0)
    parts.append(f'<line x1="{pad_l}" y1="{y100:.1f}" x2="{pad_l + pw}" y2="{y100:.1f}" stroke="{COLORS["crit"]}" stroke-width="1" stroke-dasharray="4,3"/>')
    parts.append(f'<text x="{pad_l + 2}" y="{y100 - 2.5:.1f}" fill="{COLORS["crit"]}" font-weight="700">Ngưỡng 100%</text>')
    labels = []
    for s in values_by_series:
        pts = [(x(i), y(v)) for i, v in enumerate(s["values"]) if v is not None and _finite(v)]
        if len(pts) < 2:
            continue
        d = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py) in enumerate(pts))
        dash = ' stroke-dasharray="5,3"' if s.get("dashed") else ""
        parts.append(f'<path d="{d}" fill="none" stroke="{s["color"]}" stroke-width="{2.2 if s.get("bold") else 1.5}"{dash}/>')
        lx, ly = pts[-1]
        lv = [v for v in s["values"] if v is not None][-1]
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" fill="{s["color"]}"/>')
        lab = f"{lv:.0f}%" if lv <= vmax else f">{vmax:.0f}% ({lv:.0f}%)"
        labels.append([lx, ly - 4, lab, s["color"]])
    # push end labels apart (min 8px) so values of close lines stay legible
    labels.sort(key=lambda t: t[1])
    for k in range(1, len(labels)):
        if labels[k][1] - labels[k - 1][1] < 9:
            labels[k][1] = labels[k - 1][1] + 9
    for lx, ly, lab, color in labels:
        parts.append(f'<text x="{lx - 3:.1f}" y="{ly:.1f}" text-anchor="end" fill="{color}" font-weight="700">{lab}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _pair_rows(cards: List[str]) -> str:
    """WeasyPrint handles flex-wrap poorly: emit explicit two-column rows."""
    rows = ""
    for i in range(0, len(cards), 2):
        pair = cards[i:i + 2]
        if len(pair) == 1:
            pair.append('<div class="chart-card" style="visibility:hidden;border:none;"></div>')
        rows += f'<div class="two-col" style="margin-bottom:8px;">{"".join(pair)}</div>'
    return rows


def _legend(scenarios: List[Dict[str, Any]]) -> str:
    items = "".join(
        f'<span><span class="dot" style="background:{_sc_color(i, sc)}"></span>{esc(sc["label"])} (+{sc["growthPct"]:.1f}%/quý)</span>'
        for i, sc in enumerate(scenarios))
    return f'<div class="legend">{items}</div>'


def _summary_box(plan: Dict[str, Any], rec: Dict[str, Any]) -> str:
    fg_key, bg_key = STATUS_COLOR.get(rec["statusCls"], STATUS_COLOR["na"])
    items = ""
    for i, r in enumerate(rec["recommendations"], 1):
        items += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
                  f'<span class="priority-pill priority-{r["priority"]}">{PRIORITY_LABEL_VI.get(r["priority"], "")}</span>'
                  f'<span class="rec-sum-text"><b>{esc(r["title"])}</b> — {esc(r["decision"])}'
                  f' <span class="rec-sum-time">· {esc(r["timeframe"])}</span></span></li>')
    base = plan["totals"][plan["baseIndex"]]
    high_i = max(range(len(plan["scenarios"])),
                 key=lambda i: plan["scenarios"][i]["growthPct"] if plan["scenarios"][i]["source"] == "assumed" else -1)
    high = plan["totals"][high_i]
    model = plan["model"]
    horizon = plan["params"]["horizonMonths"]
    bind = [c["scenarios"][plan["baseIndex"]]["final"]["binding"] for c in plan["clusters"]
            if c["scenarios"][plan["baseIndex"]]["final"]["nodes"] > 0]
    bind_txt = ", ".join(sorted(set(bind))) if bind else "—"
    stats = [
        {"label": "Host hiện có", "value": fmt_int(model["hostCount"]),
         "sub": f"{fmt_int(len(plan['clusters']))} cluster · {fmt_int(model['poweredOnCount'])} VM bật", "cls": "na"},
        {"label": f"Node cần thêm · {horizon} th", "value": fmt_int(base["finalNodes"]),
         "sub": f"kịch bản cơ sở +{plan['scenarios'][plan['baseIndex']]['growthPct']:.0f}%/quý", "cls": rec["statusCls"] if base["finalNodes"] else "ok"},
        {"label": "Kịch bản cao", "value": fmt_int(high["finalNodes"]),
         "sub": f"+{plan['scenarios'][high_i]['growthPct']:.0f}%/quý · {fmt_int(high['addCores'])} cores / {fmt_int(high['addRamGB'])} GB", "cls": "na"},
        {"label": "Đặt hàng trước", "value": base["orderByLabel"] or "—",
         "sub": ("đã thiếu năng lực hiện tại" if base["firstNeedMonth"] == 0 else
                 f"cần vận hành từ {base['firstNeedLabel']}" if base["firstNeedLabel"] else f"không cần trong {horizon} tháng"),
         "cls": rec["statusCls"] if base["finalNodes"] else "ok"},
        {"label": "Ràng buộc", "value": bind_txt,
         "sub": f"CPU {fmt_pct(model['cpuPct'], 0)} · RAM {fmt_pct(model['ramPct'], 0)} ngưỡng hiện tại", "cls": "na"},
    ]
    return f"""
    <div class="exec-brief {rec['statusCls']}">
      <div class="exec-brief-head">
        <span class="pill" style="color:{COLORS[fg_key]};background:{COLORS[bg_key]};border-color:{COLORS[fg_key]}55;font-size:9px;">{esc(rec['statusLabel'])}</span>
        <span class="exec-brief-title">Tổng kết đề xuất</span>
      </div>
      <div class="exec-brief-headline">{esc(rec['headline'])}</div>
      <ol class="rec-sum">{items}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


def _current_state_table(plan: Dict[str, Any]) -> str:
    rows = ""
    for c in plan["clusters"]:
        m = c["model"]
        ha = c["ha"]
        ha_txt = ("—" if not ha.get("eligible") else ("Đạt" if ha.get("survives") else "Không đạt"))
        ha_cls = "na" if not ha.get("eligible") else ("ok" if ha.get("survives") else "crit")
        rows += (f'<tr><td><b>{esc(c["name"])}</b></td><td>{fmt_int(c["hosts"])}</td><td>{fmt_int(c["poweredOn"])}/{fmt_int(c["vms"])}</td>'
                 f'<td>{fmt_int(m["totalCores"])} · {fmt_int(m["totalRamGB"])} GB</td>'
                 f'<td>{fmt_int(m["usedVcpu"])} vCPU · {fmt_int(m["usedRamGB"])} GB</td>'
                 f'<td>{fmt_num(m["cpuOvercommit"], 2, "x")} → {fmt_pct(m["cpuPct"], 0)}</td>'
                 f'<td>{fmt_num(m["ramOvercommit"], 2, "x")} → {fmt_pct(m["ramPct"], 0)}</td>'
                 f'<td><span class="pill" style="color:{COLORS[ha_cls]};background:{COLORS[STATUS_COLOR[ha_cls][1]]};border-color:{COLORS[ha_cls]}55;">{ha_txt}</span></td>'
                 f'<td>{status_pill(c["cpuStatus"] if c["cpuStatus"]["cls"] != "ok" else c["ramStatus"])}</td></tr>')
    return f"""<table>
      <thead><tr><th>Cluster</th><th>Host</th><th>VM bật/tổng</th><th>Năng lực vật lý</th><th>Đã cấp phát</th>
      <th>CPU overcommit · % ngưỡng</th><th>RAM overcommit · % ngưỡng</th><th>N+1</th><th>Trạng thái</th></tr></thead>
      <tbody>{rows}</tbody></table>"""


def _assumptions_html(plan: Dict[str, Any]) -> str:
    p, pol, tr = plan["params"], plan["policy"], plan["trend"]
    node_txt = (f"{p['nodeCores']} cores / {p['nodeRamGB']} GB (do người lập chỉ định)" if p["nodeCores"] and p["nodeRamGB"]
                else "theo host lớn nhất của từng cluster")
    sc_txt = " · ".join(f"{sc['label']} +{sc['growthPct']:.1f}%/quý" for sc in plan["scenarios"] if sc["source"] == "assumed")
    trend_txt = (f"Xu hướng thực tế {tr['months']} tháng: vCPU +{fmt_num(tr['cpu'], 1, '%')}/quý · RAM +{fmt_num(tr['ram'], 1, '%')}/quý (dùng làm kịch bản tham chiếu)."
                 if tr else "Chưa có lịch sử snapshot đủ dài để tính xu hướng thực tế.")
    return f"""<div class="chart-card soft" style="font-size:8.6px;line-height:1.55;">
      <div class="chart-card-title">Giả định dự phóng</div>
      <div><b>Thời gian:</b> {p['horizonMonths']} tháng · mốc {', '.join(str(m) for m in p['checkpoints'])} tháng.</div>
      <div><b>Tăng trưởng cấp phát (vCPU, RAM):</b> {esc(sc_txt)}, lãi kép theo quý.</div>
      <div><b>{esc(trend_txt)}</b></div>
      <div><b>Điều kiện đủ năng lực:</b> mức dùng ≤ {p['targetPct']:.0f}% ngưỡng overcommit (CPU {pol['cpuOvercommitMax']:.1f}x, RAM {pol['ramOvercommitMax']:.1f}x, HT {pol['htFactor']:.1f}x) <u>và</u> vẫn ≤ 100% khi mất 1 host lớn nhất (N+1).</div>
      <div><b>Node bổ sung:</b> {esc(node_txt)}. <b>Thời gian mua sắm/triển khai:</b> {p['leadMonths']} tháng → "đặt hàng trước" = thời điểm cần − {p['leadMonths']} tháng.</div>
      <div class="small-note" style="margin-top:4px;">Dự phóng dựa trên tài nguyên <i>đã cấp phát</i> cho VM đang bật (không phải mức dùng thực), nhất quán với chính sách overcommit của hệ thống. Storage không nằm trong phạm vi (SAN dùng chung).</div>
    </div>"""


def _projection_section(plan: Dict[str, Any]) -> str:
    labels = plan["monthLabels"]
    tp = plan["params"]["targetPct"]
    scs = plan["scenarios"]

    def series(getter) -> List[Dict[str, Any]]:
        return [{"values": getter(i), "color": _sc_color(i, sc), "bold": sc.get("base"), "dashed": sc.get("source") == "trend"}
                for i, sc in enumerate(scs)]

    cpu = svg_projection_chart(series(lambda i: plan["totals"][i]["series"]["cpuPct"]), labels, tp)
    ram = svg_projection_chart(series(lambda i: plan["totals"][i]["series"]["ramPct"]), labels, tp)
    per_cluster: List[str] = []
    for c in plan["clusters"]:
        ccpu = svg_projection_chart(series(lambda i, c=c: c["scenarios"][i]["series"]["cpuPct"]), labels, tp, width=330, height=120)
        cram = svg_projection_chart(series(lambda i, c=c: c["scenarios"][i]["series"]["ramPct"]), labels, tp, width=330, height=120)
        base = c["scenarios"][plan["baseIndex"]]
        need_txt = (f'cơ sở: +{base["final"]["nodes"]} node, cần từ {base["firstNeedLabel"]}' if base["final"]["nodes"]
                    else "cơ sở: không cần thêm node")
        per_cluster.append(f'<div class="chart-card keep-together" style="margin-bottom:8px;"><div class="chart-card-title">{esc(c["name"])} · {fmt_int(c["hosts"])} host · <span style="font-weight:400;color:{COLORS["muted"]};">{esc(need_txt)}</span></div>'
                           f'<div class="two-col"><div><div class="kpi-label">CPU</div>{ccpu}</div><div><div class="kpi-label">RAM</div>{cram}</div></div></div>')
    return f"""
  <div class="section">
    <div class="section-title">Dự phóng nhu cầu so với năng lực hiện có</div>
    <div class="section-sub">% ngưỡng overcommit theo tháng, năng lực giữ nguyên như hiện tại. Vượt vạch "Mục tiêu" = phải có node mới vận hành; vượt 100% = vi phạm chính sách.</div>
    <div class="two-col">
      <div class="chart-card"><div class="chart-card-title">Toàn phạm vi · CPU</div>{cpu}</div>
      <div class="chart-card"><div class="chart-card-title">Toàn phạm vi · RAM</div>{ram}</div>
    </div>
    {_legend(scs)}
  </div>
  <div class="section breakable">
    <div class="section-title">Dự phóng theo cluster</div>
    {"".join(per_cluster)}
  </div>"""


def _roadmap_section(plan: Dict[str, Any]) -> str:
    cps = plan["params"]["checkpoints"]
    scs = plan["scenarios"]
    head_cp = "".join(f"<th>{m} th</th>" for m in cps)
    rows = ""
    for c in plan["clusters"]:
        for i, sc in enumerate(scs):
            r = c["scenarios"][i]
            cells = "".join(
                f'<td style="text-align:center;{"font-weight:700;color:" + COLORS["crit"] if cp["nodes"] else ""}">{"+" + str(cp["nodes"]) if cp["nodes"] else "0"}</td>'
                for cp in r["checkpoints"])
            first_td = (f'<td>{esc(c["name"])}</td>' if i == 0 else "<td></td>")
            base_mark = ' style="background:#f4f8ff;"' if sc.get("base") else ""
            rows += (f'<tr{base_mark}>{first_td}<td><span class="dot" style="display:inline-block;width:7px;height:7px;border-radius:2px;background:{_sc_color(i, sc)};margin-right:4px;"></span>{esc(sc["label"])} +{sc["growthPct"]:.1f}%</td>'
                     f'{cells}<td>{esc(r["final"]["binding"])}</td><td>{esc(r["firstNeedLabel"] or "—")}</td><td><b>{esc(r["orderByLabel"] or "—")}</b></td></tr>')
    # totals
    for i, sc in enumerate(scs):
        t = plan["totals"][i]
        cells = "".join(f'<td style="text-align:center;font-weight:700;">{"+" + str(cp["nodes"]) if cp["nodes"] else "0"}</td>' for cp in t["checkpoints"])
        rows += (f'<tr style="background:{COLORS["surface2"]};font-weight:700;"><td>{"Tổng" if i == 0 else ""}</td><td>{esc(sc["label"])}</td>{cells}'
                 f'<td>{fmt_int(t["addCores"])} cores · {fmt_int(t["addRamGB"])} GB</td><td>{esc(t["firstNeedLabel"] or "—")}</td><td>{esc(t["orderByLabel"] or "—")}</td></tr>')

    # bar chart: nodes to add at horizon by cluster per scenario
    labels = [c["name"] for c in plan["clusters"]]
    bar_series = [{"name": f'{sc["label"]} +{sc["growthPct"]:.0f}%', "color": _sc_color(i, sc),
                   "values": [c["scenarios"][i]["final"]["nodes"] for c in plan["clusters"]]} for i, sc in enumerate(scs)]
    chart = svg_bar_chart(labels, bar_series, width=720, height=170, unit="", show_values=True)
    return f"""
  <div class="section">
    <div class="section-title">Lộ trình bổ sung node theo cluster</div>
    <div class="section-sub">Số node (cộng dồn) cần có tại mỗi mốc để giữ ≤ {plan['params']['targetPct']:.0f}% ngưỡng và N+1. Dòng nền xanh nhạt = kịch bản cơ sở dùng cho đề xuất.</div>
    <table>
      <thead><tr><th>Cluster</th><th>Kịch bản</th>{head_cp}<th>Ràng buộc</th><th>Cần từ</th><th>Đặt hàng trước</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="chart-card" style="margin-top:8px;"><div class="chart-card-title">Số node cần thêm sau {plan['params']['horizonMonths']} tháng theo cluster và kịch bản</div>{chart}{_legend(scs)}</div>
  </div>"""


def _proposals_section(plan: Dict[str, Any]) -> str:
    base_i = plan["baseIndex"]
    cards: List[str] = []
    for c in plan["clusters"]:
        r = c["scenarios"][base_i]
        spec = c["nodeSpec"]
        n = r["final"]["nodes"]
        m = c["model"]
        ht = plan["policy"]["htFactor"]
        if n == 0:
            body = (f'<div style="color:{COLORS["ok"]};font-weight:700;">Không cần bổ sung node trong {plan["params"]["horizonMonths"]} tháng.</div>'
                    f'<div>Cuối kỳ: CPU {fmt_pct(r["final"]["cpuPct"], 0)} · RAM {fmt_pct(r["final"]["ramPct"], 0)} ngưỡng (năng lực hiện tại).</div>')
        else:
            new_logical = m["totalLogicalCores"] + n * spec["cores"] * ht
            new_ram = m["totalRamGB"] + n * spec["ramGB"]
            cpu_after = r["final"]["demandVcpu"] / (new_logical * plan["policy"]["cpuOvercommitMax"]) * 100 if new_logical else None
            ram_after = r["final"]["demandRamGB"] / (new_ram * plan["policy"]["ramOvercommitMax"]) * 100 if new_ram else None
            alt = ""
            if r["ramUpgrade"]:
                u = r["ramUpgrade"]
                alt = (f'<div style="margin-top:4px;"><b>Phương án B — nâng RAM:</b> +{fmt_int(u["perHostGB"])} GB/host × {u["hosts"]} host '
                       f'(+{fmt_int(u["totalGB"])} GB) thay vì thêm node; cần xác nhận slot DIMM còn trống và không giải quyết N+1 về CPU.</div>')
            body = (f'<div><b>Phương án A — bổ sung {n} node</b> {spec["cores"]} cores / {fmt_int(spec["ramGB"])} GB '
                    f'(+{fmt_int(n * spec["cores"])} cores, +{fmt_int(n * spec["ramGB"])} GB).</div>'
                    f'<div>Sau khi bổ sung, cuối kỳ: CPU {fmt_pct(cpu_after, 0)} · RAM {fmt_pct(ram_after, 0)} ngưỡng; '
                    f'ràng buộc chính: <b>{esc(r["final"]["binding"])}</b>.</div>'
                    f'<div>Node đầu tiên cần vận hành từ <b>{esc(r["firstNeedLabel"])}</b> → đặt hàng trước <b>{esc(r["orderByLabel"])}</b>.</div>'
                    f'{alt}')
        cards.append(f'<div class="chart-card keep-together" style="font-size:8.8px;line-height:1.5;">'
                     f'<div class="chart-card-title">{esc(c["name"])} · {fmt_int(c["hosts"])} host · {fmt_int(m["totalCores"])} cores / {fmt_int(m["totalRamGB"])} GB</div>{body}</div>')
    return f"""
  <div class="section breakable">
    <div class="section-title">Đề xuất theo cluster (kịch bản cơ sở)</div>
    {_pair_rows(cards)}
    <div class="small-note">Báo cáo Quy hoạch tài nguyên là tài liệu lập kế hoạch dựa trên giả định; số node đề xuất cần được System Owner của từng cluster xác nhận cùng kế hoạch dự án trước khi lập ngân sách. Chi tiết hiện trạng: Báo cáo Kỹ thuật cùng kỳ.</div>
  </div>"""


def _planning_css() -> str:
    return f"""
.keep-together {{ break-inside:avoid; page-break-inside:avoid; }}
.rec-sum {{ list-style:none; margin:2px 0 6px; padding:0; }}
.rec-sum-item {{ display:flex; align-items:flex-start; gap:6px; padding:2.5px 0; border-bottom:1px dashed {COLORS['border']}; font-size:8.8px; line-height:1.4; }}
.rec-sum-item:last-child {{ border-bottom:none; }}
.rec-sum-item .rec-num {{ flex:0 0 14px; width:14px; height:14px; line-height:14px; border-radius:50%; background:{COLORS['primary']}; color:#fff; font-size:7.6px; font-weight:700; text-align:center; margin-top:1px; }}
.rec-sum-item .priority-pill {{ flex:0 0 96px; text-align:center; margin-top:1px; }}
.rec-sum-text {{ flex:1; color:{COLORS['ink']}; }}
.rec-sum-time {{ color:{COLORS['muted']}; font-size:7.6px; white-space:nowrap; }}
"""


def render_planning_html(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: Policy, scope_label: str,
                         params: planning.PlanningParams, logo_data_url: Optional[str] = None,
                         history: Optional[Dict[str, Any]] = None) -> str:
    plan = planning.build_plan(hosts, vms, policy, params, history)
    rec = planning.plan_recommendations(plan)
    generated_at = _now_str()
    logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    if not plan["clusters"]:
        body = '<div class="small-note" style="font-size:10px;">Không có host trong phạm vi đã chọn — không có gì để quy hoạch.</div>'
    else:
        body = f"""
  {_summary_box(plan, rec)}
  <div class="section">
    <div class="section-title">Hiện trạng và giả định</div>
    {_current_state_table(plan)}
    <div style="margin-top:8px;">{_assumptions_html(plan)}</div>
  </div>
  {_projection_section(plan)}
  {_roadmap_section(plan)}
  {_proposals_section(plan)}"""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo quy hoạch tài nguyên', generated_at)}{_exec_visual_css()}{_planning_css()}</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Resource Planning · {esc(scope_label)} · {esc(generated_at.split(' ')[-1])}</div>
      <div class="cover-title" style="margin-bottom:6px;">Báo cáo Quy hoạch tài nguyên — Capacity Ops</div>
    </div>
    {logo_html}
  </div>
  {body}
</body></html>"""


def build_planning_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_planning_html(*args, **kwargs))
