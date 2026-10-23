"""PDF renderer for the host-sizing proposal ("Đề xuất cấu hình máy chủ"):
management layout shared with the Operations report — summary box with the
decision requested first, then the candidate comparison table, then the
assumptions, with the requested VM list and the existing-cluster absorption
check as appendices. Meant to be attached to a procurement proposal."""
from typing import Any, Dict, List, Optional

from . import sizing
from .report_pdf import (COLORS, STATUS_COLOR, _base_css, _exec_visual_css, _now_str, _stat_tiles_html, esc, fmt_int,
                         fmt_num, fmt_pct, html_to_pdf_bytes)
from .report_planning import _planning_css

PRIORITY_CSS = {"cao": "priority-cao", "tb": "priority-tb", "thap": "priority-thap", "info": "priority-info"}
PRIORITY_TXT = {"cao": "Quyết định chính", "tb": "Kèm theo", "thap": "Tuỳ chọn", "info": "Thông tin"}


def _gb(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{fmt_num(v / 1024, 1)} TB" if v >= 1024 else f"{fmt_int(v)} GB"


def _money(v: Optional[float], currency: str) -> str:
    if v is None:
        return "—"
    s = f"{int(round(v)):,}".replace(",", ".")
    return f"{s} {currency}".strip()


def _pill(text: str, cls: str) -> str:
    fg, bg = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    return f'<span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;">{esc(text)}</span>'


def _summary_box(result: Dict[str, Any], best: Optional[Dict[str, Any]], existing: List[Dict[str, Any]],
                 currency: str) -> str:
    d, p = result["demand"], result["params"]
    items: List[Dict[str, str]] = []
    status_cls, status_label = "ok", "ĐỀ XUẤT"
    if best:
        prof = best["profile"]
        budget = _money(best["budget"], currency) if best["budget"] is not None else None
        items.append({"pri": "cao",
                      "title": f"Mua {best['hostsTotal']} host {prof['name']} ({prof['cores']} cores · {_gb(prof['ramGB'])} RAM/host)",
                      "decision": f"Duyệt ngân sách {budget}" if budget else "Duyệt chủ trương mua sắm (chưa có báo giá)",
                      "time": "Theo tiến độ dự án"})
        if p["storage_mode"] == "san":
            items.append({"pri": "tb", "title": f"Cấp {_gb(d['storageNeededGB'])} trên SAN cho dự án (đã gồm +{p['storage_overhead_pct']:g}% overhead)",
                          "decision": "Duyệt cấp dung lượng lưu trữ", "time": "Cùng đợt lắp host"})
        else:
            st = best["storage"]
            items.append({"pri": "tb", "title": f"Đĩa cục bộ ~{_gb(st.get('perHostNeededGB'))}/host (tổng {_gb(d['storageNeededGB'])}, hệ số bảo vệ ×{p['storage_protection_factor']:g})",
                          "decision": "Đạt với cấu hình đề xuất" if st.get("ok") else "Cần tăng đĩa cục bộ mỗi host", "time": "Trong cấu hình host"})
            if st.get("ok") is False:
                status_cls = "warn"
        f = best["failure"]
        if best["redundancy"] and f["ok"]:
            items.append({"pri": "info", "title": f"Mất {best['redundancy']} host vẫn chạy được: CPU {fmt_pct(f['cpuPct'], 0)} · RAM {fmt_pct(f['ramPct'], 0)} năng lực mục tiêu",
                          "decision": f"Đạt dự phòng N+{best['redundancy']}", "time": ""})
        elif best["redundancy"]:
            status_cls = "warn"
            items.append({"pri": "cao", "title": f"Chưa đạt dự phòng N+{best['redundancy']}", "decision": "Thêm host hoặc hạ overcommit mục tiêu", "time": ""})
    if existing:
        can = [e for e in existing if e.get("cls") == "ok" and e.get("n1Ok", True)]
        if can:
            items.append({"pri": "thap",
                          "title": f"Phương án thay thế: cluster {', '.join(e['name'] for e in can[:3])} hiện hữu hấp thụ được toàn bộ nhu cầu (<80% ngưỡng, giữ N+1)",
                          "decision": "Cân nhắc dùng chung thay vì mua mới", "time": ""})
        else:
            items.append({"pri": "info", "title": "Không cluster hiện hữu nào hấp thụ được toàn bộ nhu cầu mà vẫn an toàn",
                          "decision": "Bắt buộc bổ sung host", "time": ""})

    li = ""
    for i, it in enumerate(items, 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    stats = [
        {"label": "VM yêu cầu", "value": fmt_int(d["vmCount"]), "sub": f"TB {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} / VM", "cls": "na"},
        {"label": "vCPU", "value": f"{fmt_int(d['vcpu'])} → {fmt_int(d['vcpuPlanned'])}", "sub": f"sau +{p['growth_pct']:g}% tăng trưởng", "cls": "na"},
        {"label": "RAM (GB)", "value": f"{fmt_int(d['ramGB'])} → {fmt_int(d['ramPlannedGB'])}", "sub": f"sau tăng trưởng · overcommit {p['ram_ratio']:g}x", "cls": "na"},
        {"label": "Host đề xuất", "value": f"{best['hostsTotal']} host" if best else "—",
         "sub": (f"{best['profile']['name']} · N+{best['redundancy']}" if best else ""), "cls": "ok" if best else "na"},
        {"label": "SAN cần cấp" if p["storage_mode"] == "san" else "Lưu trữ cục bộ", "value": _gb(d["storageNeededGB"]),
         "sub": f"từ {_gb(d['diskGB'])} yêu cầu", "cls": "na"},
    ]
    fg, bg = STATUS_COLOR[status_cls]
    headline = result["recommendation"]["headline"] if best else "Chưa có VM nào trong danh sách yêu cầu."
    return f"""
    <div class="exec-brief {status_cls}">
      <div class="exec-brief-head">
        <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{status_label}</span>
        <span class="exec-brief-title">Tổng kết — đề xuất &amp; quyết định đề nghị</span>
      </div>
      <div class="exec-brief-headline">{esc(headline)}</div>
      <ol class="rec-sum">{li}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


def _comparison_table(result: Dict[str, Any], currency: str) -> str:
    rows = result["results"]
    local = result["params"]["storage_mode"] == "local"
    any_price = any(r["budget"] is not None for r in rows)
    body = ""
    for r in sorted(rows, key=lambda x: x["rank"] or 99):
        pr = r["profile"]
        f = r["failure"]
        fail_txt = f"{fmt_pct(f['cpuPct'], 0)} / {fmt_pct(f['ramPct'], 0)}" if f["hostsLeft"] else "—"
        fail_pill = _pill("N", "na") if r["redundancy"] == 0 else (_pill(f"Đạt N+{r['redundancy']}", "ok") if f["ok"] else _pill("Chưa đạt", "crit"))
        style = f' style="background:{COLORS["ok_bg"]};"' if r["recommended"] else ""
        tag = _pill("Đề xuất", "ok") if r["recommended"] else f'<span style="color:{COLORS["muted"]};">#{r["rank"]}</span>'
        storage_td = ""
        if local:
            st = r["storage"]
            storage_td = f"<td>{_gb(st.get('perHostNeededGB'))}/host {'' if st.get('ok') else _pill('thiếu đĩa', 'crit')}</td>"
        price_td = f"<td style=\"text-align:right;white-space:nowrap;\">{_money(r['budget'], currency)}</td>" if any_price else ""
        body += (f'<tr{style}><td style="white-space:nowrap;"><b>{esc(pr["name"])}</b><div style="color:{COLORS["muted"]};font-size:7.6px;">{pr["sockets"]}×{pr["coresPerSocket"]}C = {pr["cores"]} cores · {_gb(pr["ramGB"])} RAM</div></td>'
                 f'<td style="text-align:center;">{r["hostsForCpu"]} / {r["hostsForRam"]}</td>'
                 f'<td style="text-align:center;">+{r["redundancy"]}</td>'
                 f'<td style="text-align:center;font-size:11px;font-weight:700;">{r["hostsTotal"]}</td>'
                 f'<td>{esc(r["binding"])}</td>'
                 f'<td style="white-space:nowrap;">{fmt_int(r["totals"]["cores"])} / {_gb(r["totals"]["ramGB"])}</td>'
                 f'<td>{fmt_num(r["after"]["cpuOvercommit"], 2, "x")} / {fmt_num(r["after"]["ramOvercommit"], 2, "x")}</td>'
                 f'<td style="white-space:nowrap;">{fail_txt} {fail_pill}</td>'
                 f'<td style="text-align:center;">{"—" if r["spare"]["extraAvgVms"] is None else "~" + fmt_int(r["spare"]["extraAvgVms"])}</td>'
                 f'{storage_td}{price_td}<td>{tag}</td></tr>')
    return f"""<table class="decision-table">
      <thead><tr><th>Cấu hình host</th><th>Host CPU / RAM</th><th>Dự phòng</th><th>Tổng host</th><th>Ràng buộc</th>
      <th>Tổng cores / RAM</th><th>Overcommit sau CPU / RAM</th><th>Mất host: CPU / RAM</th><th>Thêm ~VM</th>
      {'<th>Đĩa cục bộ</th>' if local else ''}{'<th style="text-align:right;">Ngân sách ước tính</th>' if any_price else ''}<th></th></tr></thead>
      <tbody>{body}</tbody></table>"""


def _assumptions_html(result: Dict[str, Any]) -> str:
    p = result["params"]
    lines = "".join(f"<div>• {esc(x)}</div>" for x in result["assumptions"])
    return f"""<div class="chart-card soft" style="font-size:8.6px;line-height:1.55;">
      <div class="chart-card-title">Giả định sizing</div>
      {lines}
      <div class="small-note" style="margin-top:4px;">Cách tính: số host = max(host theo CPU, host theo RAM) + dự phòng N+{p['redundancy']}, tối thiểu {p['min_hosts']} host.
      Host theo CPU = vCPU yêu cầu (sau tăng trưởng) ÷ [(cores − {p['reserve_cores']:g}) × HT {p['ht_factor']:g} × {p['cpu_ratio']:g}x × {p['max_util_pct']:g}%];
      host theo RAM = RAM yêu cầu ÷ [(RAM host − {p['reserve_ram_gb']:g} GB) × {p['ram_ratio']:g}x × {p['max_util_pct']:g}%].
      Phương án đề xuất: ít host nhất → ngân sách thấp nhất → hiệu suất dùng cao nhất. Overcommit thực tế sau khi lắp luôn thấp hơn mục tiêu vì đã trừ headroom và dự phòng.</div>
    </div>"""


def _vm_table(vms: List[Dict[str, Any]], demand: Dict[str, Any]) -> str:
    rows = ""
    for v in vms:
        q = v["qty"]
        rows += (f'<tr><td>{esc(v["name"])}</td><td style="text-align:center;">{fmt_int(q)}</td><td style="text-align:right;">{fmt_num(v["vcpu"], 0)}</td>'
                 f'<td style="text-align:right;">{fmt_num(v["ramGB"], 0)}</td><td style="text-align:right;">{fmt_num(v["diskGB"], 0)}</td>'
                 f'<td style="text-align:right;">{fmt_num(q * v["vcpu"], 0)}</td><td style="text-align:right;">{fmt_num(q * v["ramGB"], 0)}</td>'
                 f'<td style="text-align:right;">{fmt_num(q * v["diskGB"], 0)}</td><td style="color:{COLORS["muted"]};">{esc(v.get("note") or "")}</td></tr>')
    rows += (f'<tr style="font-weight:700;background:{COLORS["surface2"]};"><td>Tổng</td><td style="text-align:center;">{fmt_int(demand["vmCount"])}</td><td></td><td></td><td></td>'
             f'<td style="text-align:right;">{fmt_int(demand["vcpu"])}</td><td style="text-align:right;">{fmt_int(demand["ramGB"])}</td><td style="text-align:right;">{fmt_int(demand["diskGB"])}</td><td></td></tr>')
    return f"""<table class="appendix-table">
      <thead><tr><th>Tên VM / nhóm</th><th>Số lượng</th><th style="text-align:right;">vCPU / VM</th><th style="text-align:right;">RAM GB / VM</th><th style="text-align:right;">Disk GB / VM</th>
      <th style="text-align:right;">Tổng vCPU</th><th style="text-align:right;">Tổng RAM GB</th><th style="text-align:right;">Tổng Disk GB</th><th>Ghi chú</th></tr></thead>
      <tbody>{rows}</tbody></table>"""


def _feasible(e: Dict[str, Any]) -> bool:
    return (e.get("cls") == "ok") and (e.get("n1Ok") is not False)


def _pick_target(existing: List[Dict[str, Any]], target_cluster: Optional[str]) -> Optional[Dict[str, Any]]:
    """Target cluster for the reuse scenario: the one named by the user, else
    the feasible cluster with the most headroom left, else None."""
    if not existing:
        return None
    if target_cluster:
        for e in existing:
            if e["name"] == target_cluster:
                return e
    feasible = [e for e in existing if _feasible(e)]
    if not feasible:
        return None
    return min(feasible, key=lambda e: max(e.get("cpuAfter") or 0, e.get("ramAfter") or 0))


def _existing_table(existing: List[Dict[str, Any]], policy_txt: str, highlight: Optional[str] = None) -> str:
    rows = ""
    show_spare = any(e.get("extraAvgVms") is not None for e in existing)
    for e in existing:
        cls = e.get("cls") or "na"
        label = {"ok": "Hấp thụ được", "warn": "Sát ngưỡng", "crit": "Vượt ngưỡng"}.get(cls, "—")
        n1 = e.get("n1CpuPct")
        n1_txt = (f"{fmt_pct(e.get('n1CpuPct'), 0)} / {fmt_pct(e.get('n1RamPct'), 0)} " + (_pill("Đạt", "ok") if e.get("n1Ok") else _pill("Không", "crit"))) if n1 is not None else "—"
        is_target = bool(highlight) and e["name"] == highlight
        style = f' style="background:{COLORS["ok_bg" if _feasible(e) else "warn_bg"]};"' if is_target else ""
        tag = (" " + (_pill("Tiếp nhận", "ok") if _feasible(e) else _pill("Đang xét", "warn"))) if is_target else ""
        spare_td = ""
        if show_spare:
            sp = e.get("extraAvgVms")
            spare_td = f'<td style="text-align:center;">{"—" if sp is None else "~" + fmt_int(sp)}</td>'
        rows += (f'<tr{style}><td><b>{esc(e["name"])}</b> <span style="color:{COLORS["muted"]};">· {fmt_int(e.get("hostCount"))} host</span>{tag}</td>'
                 f'<td>{fmt_pct(e.get("cpuBefore"), 0)} → <b>{fmt_pct(e.get("cpuAfter"), 0)}</b></td>'
                 f'<td>{fmt_pct(e.get("ramBefore"), 0)} → <b>{fmt_pct(e.get("ramAfter"), 0)}</b></td>'
                 f'<td style="white-space:nowrap;">{n1_txt}</td>{spare_td}<td>{_pill(label, cls)}</td></tr>')
    return f"""<table>
      <thead><tr><th>Cluster hiện hữu</th><th>CPU % ngưỡng (trước → sau)</th><th>RAM % ngưỡng (trước → sau)</th><th>Khi mất 1 host (CPU / RAM)</th>{'<th>Dư địa còn lại ~VM</th>' if show_spare else ''}<th>Kết luận</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div class="small-note">Giả định đưa toàn bộ nhu cầu (sau tăng trưởng) vào từng cluster; % tính theo ngưỡng overcommit của chính sách hiện hành{(' (' + esc(policy_txt) + ')') if policy_txt else ''}.{' Dư địa còn lại: số VM cỡ trung bình của dự án còn thêm được tới 80% ngưỡng.' if show_spare else ''}</div>"""


def _reuse_summary_box(result: Dict[str, Any], best: Optional[Dict[str, Any]], existing: List[Dict[str, Any]],
                       target: Optional[Dict[str, Any]], include_new: bool, currency: str, project: str) -> str:
    d, p = result["demand"], result["params"]
    items: List[Dict[str, str]] = []
    n_new = best["hostsTotal"] if best else 0
    budget = _money(best["budget"], currency) if (best and best["budget"] is not None) else None
    if target and _feasible(target):
        status_cls, status_label = "ok", "TẬN DỤNG HIỆN HỮU"
        headline = (f"Triển khai {fmt_int(d['vmCount'])} VM của {project} trên cluster {target['name']} hiện hữu "
                    f"({fmt_int(target.get('hostCount'))} host) — không cần mua host mới; sau tiếp nhận CPU {fmt_pct(target.get('cpuAfter'), 0)} · "
                    f"RAM {fmt_pct(target.get('ramAfter'), 0)} ngưỡng, vẫn giữ N+1.")
    elif target and target.get("cls") == "crit":
        status_cls, status_label = "crit", "KHÔNG AN TOÀN"
        headline = (f"Cluster {target['name']} không tiếp nhận được an toàn {fmt_int(d['vmCount'])} VM — "
                    f"CPU {fmt_pct(target.get('cpuAfter'), 0)} · RAM {fmt_pct(target.get('ramAfter'), 0)} ngưỡng sau tiếp nhận"
                    + (", mất N+1" if target.get("n1Ok") is False else "") + " — "
                    + (f"cần bổ sung host cho cluster hoặc chọn phương án B ({n_new} host mới)." if include_new and best else "cần bổ sung host cho cluster trước khi triển khai."))
    elif target:
        status_cls, status_label = "warn", "CẦN CÂN NHẮC"
        why = "sát ngưỡng" if target.get("cls") == "warn" else "không giữ được N+1"
        headline = (f"Cluster {target['name']} tiếp nhận được {fmt_int(d['vmCount'])} VM nhưng {why} "
                    f"(CPU {fmt_pct(target.get('cpuAfter'), 0)} · RAM {fmt_pct(target.get('ramAfter'), 0)} ngưỡng sau tiếp nhận) — "
                    + (f"nên kết hợp bổ sung host hoặc chọn phương án B ({n_new} host mới)." if include_new and best else "cần bổ sung host dự phòng."))
    else:
        status_cls, status_label = "crit", "KHÔNG KHẢ THI"
        headline = (f"Không cluster hiện hữu nào tiếp nhận được toàn bộ {fmt_int(d['vmCount'])} VM mà vẫn an toàn — "
                    + (f"cần trang bị mới {n_new} host (phương án B)." if include_new and best else "cần trang bị mới host."))

    if target:
        items.append({"pri": "cao",
                      "title": f"Triển khai trên {target['name']}: CPU {fmt_pct(target.get('cpuBefore'), 0)} → {fmt_pct(target.get('cpuAfter'), 0)}, RAM {fmt_pct(target.get('ramBefore'), 0)} → {fmt_pct(target.get('ramAfter'), 0)} ngưỡng",
                      "decision": (f"Duyệt chủ trương dùng chung hạ tầng, thống nhất với System Owner cluster {target['name']}" if _feasible(target)
                                   else f"Chỉ duyệt dùng chung kèm bổ sung host; System Owner cluster {target['name']} xác nhận"),
                      "time": "Theo tiến độ dự án"})
        if not _feasible(target):
            alt = _pick_target(existing, None)
            if alt and alt["name"] != target["name"]:
                items.append({"pri": "cao", "title": f"Thay thế: {alt['name']} hấp thụ được (CPU {fmt_pct(alt.get('cpuAfter'), 0)} · RAM {fmt_pct(alt.get('ramAfter'), 0)} ngưỡng, N+1 đạt)",
                              "decision": f"Cân nhắc chuyển dự án sang cluster {alt['name']}", "time": ""})
        if target.get("n1Ok") is False:
            items.append({"pri": "cao", "title": f"Sau tiếp nhận, {target['name']} không chịu được mất 1 host (CPU {fmt_pct(target.get('n1CpuPct'), 0)} · RAM {fmt_pct(target.get('n1RamPct'), 0)})",
                          "decision": "Duyệt thêm 1 host dự phòng cho cluster", "time": "Trước go-live"})
        elif target.get("n1Ok"):
            items.append({"pri": "info", "title": f"Mất 1 host vẫn chạy được: CPU {fmt_pct(target.get('n1CpuPct'), 0)} · RAM {fmt_pct(target.get('n1RamPct'), 0)} ngưỡng",
                          "decision": "Không cần host dự phòng thêm", "time": ""})
    if p["storage_mode"] == "san":
        items.append({"pri": "tb", "title": f"Cấp {_gb(d['storageNeededGB'])} trên SAN cho dự án (đã gồm +{p['storage_overhead_pct']:g}% overhead)",
                      "decision": "Duyệt cấp dung lượng lưu trữ", "time": "Trước triển khai"})
    if target and target.get("extraAvgVms") is not None:
        items.append({"pri": "thap", "title": f"Dư địa còn lại của {target['name']} sau tiếp nhận: ~{fmt_int(target['extraAvgVms'])} VM cỡ dự án (tới 80% ngưỡng)",
                      "decision": "Theo dõi; đưa mở rộng vào kỳ quy hoạch tài nguyên" if target["extraAvgVms"] > 0 else "Lập kế hoạch mở rộng ngay", "time": ""})
    if include_new and best:
        items.append({"pri": "thap", "title": f"Phương án B — trang bị mới {n_new} host {best['profile']['name']}" + (f", ngân sách {budget}" if budget else ""),
                      "decision": "Không phải chi nếu dùng chung" if (target and _feasible(target)) else "Phương án dự phòng nếu không dùng chung được", "time": ""})

    li = ""
    for i, it in enumerate(items, 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    tgt_cls = "ok" if (target and _feasible(target)) else ("crit" if (not target or target.get("cls") == "crit") else "warn")
    stats = [
        {"label": "VM yêu cầu", "value": fmt_int(d["vmCount"]), "sub": f"TB {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} / VM", "cls": "na"},
        {"label": "vCPU", "value": f"{fmt_int(d['vcpu'])} → {fmt_int(d['vcpuPlanned'])}", "sub": f"sau +{p['growth_pct']:g}% tăng trưởng", "cls": "na"},
        {"label": "RAM (GB)", "value": f"{fmt_int(d['ramGB'])} → {fmt_int(d['ramPlannedGB'])}", "sub": "sau tăng trưởng", "cls": "na"},
        {"label": "Cluster tiếp nhận", "value": target["name"] if target else "Không có",
         "sub": (f"{fmt_int(target.get('hostCount'))} host · N+1 {'Đạt' if target.get('n1Ok') else 'Không' if target.get('n1Ok') is False else '—'}" if target else "cần trang bị mới"), "cls": tgt_cls},
        ({"label": "Chi phí tránh được", "value": budget, "sub": f"= {n_new} host {best['profile']['name']}", "cls": "ok"}
         if (budget and target and _feasible(target)) else
         {"label": "SAN cần cấp" if p["storage_mode"] == "san" else "Lưu trữ cục bộ", "value": _gb(d["storageNeededGB"]), "sub": f"từ {_gb(d['diskGB'])} yêu cầu", "cls": "na"}),
    ]
    fg, bg = STATUS_COLOR[status_cls]
    return f"""
    <div class="exec-brief {status_cls}">
      <div class="exec-brief-head">
        <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{status_label}</span>
        <span class="exec-brief-title">Tổng kết — đề xuất &amp; quyết định đề nghị</span>
      </div>
      <div class="exec-brief-headline">{esc(headline)}</div>
      <ol class="rec-sum">{li}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


def _target_detail(target: Dict[str, Any], d: Dict[str, Any]) -> str:
    parts = [f"<b>Cluster {esc(target['name'])}</b>: {fmt_int(target.get('hostCount'))} host"]
    if target.get("totalCores") is not None:
        parts[-1] += f" · {fmt_int(target['totalCores'])} cores / {_gb(target.get('totalRamGB'))} RAM vật lý"
    parts[-1] += "."
    if target.get("usedVcpu") is not None:
        parts.append(f"Đang cấp phát {fmt_int(target['usedVcpu'])} vCPU / {_gb(target.get('usedRamGB'))} cho {fmt_int(target.get('poweredOnCount'))} VM bật; "
                     f"dự án thêm {fmt_int(d['vcpuPlanned'])} vCPU / {_gb(d['ramPlannedGB'])} (đã gồm tăng trưởng).")
    if target.get("cpuOcAfter") is not None:
        parts.append(f"Overcommit sau tiếp nhận: CPU {fmt_num(target['cpuOcAfter'], 2, 'x')} · RAM {fmt_num(target.get('ramOcAfter'), 2, 'x')} "
                     f"(= {fmt_pct(target.get('cpuAfter'), 0)} · {fmt_pct(target.get('ramAfter'), 0)} ngưỡng chính sách).")
    if target.get("n1Ok") is not None:
        parts.append(("Mất 1 host lớn nhất vẫn trong ngưỡng" if target["n1Ok"] else "Mất 1 host lớn nhất sẽ vượt ngưỡng")
                     + f" (CPU {fmt_pct(target.get('n1CpuPct'), 0)} · RAM {fmt_pct(target.get('n1RamPct'), 0)}).")
    return f'<div class="insight-box" style="margin-top:8px;">{" ".join(parts)}</div>'


def render_sizing_html(vms: List[sizing.VmSpec], params: sizing.SizingParams, profiles: List[sizing.HostProfile],
                       existing: Optional[List[Dict[str, Any]]] = None, policy_txt: str = "", currency: str = "VND",
                       logo_data_url: Optional[str] = None, prepared_by: str = "", mode: str = "new",
                       target_cluster: Optional[str] = None, include_new: bool = True, include_existing: bool = True) -> str:
    result = sizing.build_sizing(vms, params, profiles)
    best = result["results"][result["recommendedIndex"]] if result["recommendedIndex"] is not None else None
    existing = existing or []
    generated_at = _now_str()
    project = params.environment.strip() or "dự án mới"
    logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    vm_dicts = [{"name": v.name, "qty": v.qty, "vcpu": v.vcpu, "ramGB": v.ram_gb, "diskGB": v.disk_gb, "note": v.note} for v in vms]
    appendix_break = "breakable" if len(vm_dicts) > 12 else ""
    comparison_html = f"""
  <div class="section">
    <div class="section-title">{'Phương án B — trang bị mới (đối chiếu)' if mode == 'reuse' else 'So sánh cấu hình host ứng viên'}</div>
    <div class="section-sub">Số host vật lý cần cho {fmt_int(result['demand']['vmCount'])} VM theo từng cấu hình; dòng tô xanh là phương án {'mua mới tốt nhất' if mode == 'reuse' else 'đề xuất'}.</div>
    {_comparison_table(result, currency)}
    {('<div class="insight-box" style="margin-top:8px;"><b>' + ('Nếu phải mua mới.' if mode == 'reuse' else 'Phương án đề xuất.') + '</b> ' + esc(result['recommendation']['detail']) + '</div>') if best else ''}
  </div>"""
    if mode == "reuse":
        target = _pick_target(existing, target_cluster)
        summary_html = _reuse_summary_box(result, best, existing, target, include_new, currency, project)
        if existing:
            existing_html = f"""
  <div class="section">
    <div class="section-title">Hạ tầng hiện hữu — khả năng tiếp nhận dự án</div>
    <div class="section-sub">Mọi cluster trong phạm vi vCenter đang chọn; dòng tô màu là cluster đang xét tiếp nhận (xanh: an toàn, cam: cần bổ sung host).</div>
    {_existing_table(existing, policy_txt, highlight=target['name'] if target else None)}
    {_target_detail(target, result['demand']) if target else ''}
  </div>"""
        else:
            existing_html = ('<div class="section"><div class="section-title">Hạ tầng hiện hữu — khả năng tiếp nhận dự án</div>'
                             '<div class="small-note" style="font-size:9px;">Không có dữ liệu cluster hiện hữu (chưa đồng bộ vCenter hoặc chưa import host/VM).</div></div>')
        main_html = existing_html + (comparison_html if include_new else "")
    else:
        summary_html = _summary_box(result, best, existing if include_existing else [], currency)
        existing_html = ""
        if existing and include_existing:
            existing_html = f"""
  <div class="section">
    <div class="section-title">Hạ tầng hiện hữu có hấp thụ được không?</div>
    {_existing_table(existing, policy_txt)}
  </div>"""
        main_html = comparison_html + existing_html
    body = f"""
  {summary_html}
  {main_html}
  <div class="section">
    <div class="section-title">Giả định</div>
    {_assumptions_html(result)}
  </div>
  <div class="section {appendix_break}" style="{'page-break-before:always;' if len(vm_dicts) > 12 else ''}">
    <div class="section-title">Phụ lục — Danh sách VM yêu cầu (team Dev cung cấp)</div>
    {_vm_table(vm_dicts, result['demand'])}
    <div class="small-note">Đề xuất này là tài liệu sizing dựa trên giả định; cấu hình và số lượng cuối cùng cần đối chiếu báo giá nhà cung cấp và xác nhận của chủ dự án trước khi trình duyệt ngân sách.{(' Người lập: ' + esc(prepared_by) + '.') if prepared_by else ''}</div>
  </div>"""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Đề xuất cấu hình máy chủ', generated_at)}{_exec_visual_css()}{_planning_css()}
.decision-table td {{ vertical-align:middle; }}
.appendix-table td {{ font-size:8.3px; padding:3.5px 6px; }}
</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Host Sizing · {'Tận dụng hiện hữu' if mode == 'reuse' else 'Trang bị mới'} · {esc(project)} · {esc(generated_at.split(' ')[-1])}</div>
      <div class="cover-title" style="margin-bottom:6px;">Đề xuất cấu hình máy chủ — {esc(project)}</div>
    </div>
    {logo_html}
  </div>
  {body}
</body></html>"""


def build_sizing_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_sizing_html(*args, **kwargs))
