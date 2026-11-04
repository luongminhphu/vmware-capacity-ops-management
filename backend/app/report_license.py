"""PDF renderer for the license executive summary ("Tổng quan license theo
cluster"). Takes an assessment result (live or a stored snapshot) — the
same dict the License tab renders — and lays it out management-first:
status + decisions requested, purchase scope, per-cluster overview, demand
vs entitlement vs shortfall, then the basis (rules, formula, assumptions,
data gaps). Target length 2–3 pages."""
from typing import Any, Dict, List, Optional

from .report_pdf import (COLORS, STATUS_COLOR, _base_css, _exec_visual_css, _now_str, _stat_tiles_html, esc, fmt_int,
                         html_to_pdf_bytes)
from .report_planning import _planning_css

PRIORITY_CSS = {"cao": "priority-cao", "tb": "priority-tb", "thap": "priority-thap", "info": "priority-info"}
PRIORITY_TXT = {"cao": "Quyết định chính", "tb": "Kèm theo", "thap": "Tuỳ chọn", "info": "Thông tin"}
STATUS_TXT = {"ok": ("Đủ license", "ok"), "shortfall": ("Thiếu hụt", "crit"), "insufficient_data": ("Chưa đủ dữ liệu", "warn"),
              "no_entitlement": ("Chưa nhập entitlement", "na")}
ED_TXT = {"standard": "Standard", "datacenter": "Datacenter"}


def _money(v: Optional[float], currency: Optional[str]) -> str:
    if v is None:
        return "—"
    s = f"{int(round(v)):,}".replace(",", ".")
    cur = currency if currency and currency != "mixed" else ""
    return f"{s} {cur}".strip()


def _rng(a, b) -> str:
    if b is None or a == b:
        return fmt_int(a)
    return f"{fmt_int(a)} – {fmt_int(b)}"


def _pill(text: str, cls: str) -> str:
    fg, bg = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    return f'<span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;">{esc(text)}</span>'


def _status_pill(status: str) -> str:
    txt, cls = STATUS_TXT.get(status, (status or "—", "na"))
    return _pill(txt, cls)


def _ckey_label(k: str) -> str:
    vc, _, cl = k.partition("::")
    return f"{cl} ({vc})" if vc else cl


def _packs_txt(p: Dict[str, Any]) -> str:
    if not p:
        return "—"
    return f"{fmt_int(p.get('pack16', 0))} × 16 + {fmt_int(p.get('pack2', 0))} × 2"


# ---------------------------------------------------------------------------
# Summary box (decision first)
# ---------------------------------------------------------------------------

def _summary_box(res: Dict[str, Any]) -> str:
    ws, vm, tot, sc = res["windowsServer"], res["vmware"], res["totals"], res.get("scope") or {}
    rec = ws.get("recommendedEdition") or {}
    ed_key = rec.get("byCost") or rec.get("byCores")
    ed = ws.get(ed_key) if ed_key else None
    overall = res.get("overallStatus")
    status_label, status_cls = STATUS_TXT.get(overall, ("—", "na"))
    n_clusters = len(res.get("clusters") or [])
    partial = bool(sc.get("partial"))
    scope_txt = (f"{n_clusters} cluster trong đợt mua này" if partial else f"toàn bộ {n_clusters} cluster")

    # Headline
    parts: List[str] = []
    if vm["status"] == "shortfall":
        parts.append(f"VMware thiếu {fmt_int(vm['shortfallCores'])} / {fmt_int(vm['demandCores'])} core")
    elif vm["status"] == "ok":
        parts.append(f"VMware đủ ({fmt_int(vm['entitledCores'])} core cho {fmt_int(vm['demandCores'])} core nhu cầu)")
    else:
        parts.append(f"VMware cần {fmt_int(vm['demandCores'])} core (chưa có entitlement để đối chiếu)")
    if tot.get("windowsServerVms"):
        if ed:
            if ed["status"] == "shortfall":
                parts.append(f"Windows Server {ED_TXT[ed_key]} thiếu {_rng(ed['shortfallCores'], ed.get('shortfallCoresMax'))} / {_rng(ed['demandCores'], ed.get('demandCoresMax'))} core")
            elif ed["status"] == "ok":
                parts.append(f"Windows Server {ED_TXT[ed_key]} đủ license")
            else:
                parts.append(f"Windows Server nên dùng {ED_TXT[ed_key]} ({_rng(ed['demandCores'], ed.get('demandCoresMax'))} core)")
    else:
        parts.append("không có VM Windows Server trong phạm vi")
    headline = f"Phạm vi {scope_txt}: {'; '.join(parts)}."

    items: List[Dict[str, str]] = []
    # 1. VMware decision
    if vm["status"] == "shortfall":
        cost = vm.get("cost")
        title = f"Mua bổ sung {fmt_int(vm['orderCores'])} core VMware per-core cho {fmt_int(tot['hosts'])} ESXi host ({fmt_int(vm['demandCores'])} core nhu cầu, có {fmt_int(vm['entitledCores'])})"
        if cost and cost.get("shortfallCost") is not None:
            title += f" — ước {_money(cost['shortfallCost'], cost.get('currency'))} ({esc(cost.get('basis') or '')})"
        items.append({"pri": "cao", "title": title, "decision": "Duyệt ngân sách license VMware" if cost else "Duyệt chủ trương; lấy báo giá per-core", "time": "Trước khi gia hạn / mở rộng"})
    elif vm["status"] == "ok":
        items.append({"pri": "info", "title": f"VMware: {fmt_int(vm['entitledCores'])} core entitlement đủ cho {fmt_int(vm['demandCores'])} core nhu cầu", "decision": "Không cần mua thêm", "time": ""})
    else:
        items.append({"pri": "tb", "title": f"VMware cần {fmt_int(vm['demandCores'])} core per-core (max(core, 16 × socket) mỗi host); chưa nhập entitlement", "decision": "Nhập entitlement/hợp đồng hiện có hoặc xác nhận 'Chưa có' để kết luận", "time": ""})
    # 2. Windows Server
    if tot.get("windowsServerVms"):
        if ed_key:
            other_key = "datacenter" if ed_key == "standard" else "standard"
            other = ws[other_key]
            why = f"{ED_TXT[ed_key]} cần {_rng(ed['demandCores'], ed.get('demandCoresMax'))} core so với {_rng(other['demandCores'], other.get('demandCoresMax'))} core nếu {ED_TXT[other_key]}"
            if rec.get("byCost") and rec.get("saving") is not None:
                why += f"; rẻ hơn {_money(rec['saving'], rec.get('currency'))}"
            items.append({"pri": "cao", "title": f"Chọn Windows Server {ED_TXT[ed_key]} cho {fmt_int(tot['windowsServerVms'])} VM Windows Server trên {n_clusters} cluster — {why}",
                          "decision": "Chốt edition theo cluster" if rec.get("byCost") else "Chốt edition; bổ sung đơn giá để so sánh chi phí", "time": ""})
            if ed["status"] == "shortfall":
                cost = ed.get("cost")
                t = f"Mua {_rng(ed['shortfallCores'], ed.get('shortfallCoresMax'))} core Windows Server {ED_TXT[ed_key]} ({_packs_txt(ed.get('shortfallPacks'))})"
                if cost and cost.get("shortfallCost") is not None:
                    t += f" — ước {_money(cost['shortfallCost'], cost.get('currency'))}" + (f" đến {_money(cost.get('shortfallCostMax'), cost.get('currency'))}" if cost.get("shortfallCostMax") not in (None, cost.get("shortfallCost")) else "")
                items.append({"pri": "cao", "title": t, "decision": "Duyệt ngân sách license Windows Server" if cost else "Duyệt chủ trương; lấy báo giá pack 16/2 core", "time": ""})
            elif ed["status"] == "ok":
                items.append({"pri": "info", "title": f"Windows Server {ED_TXT[ed_key]}: entitlement {fmt_int(ed['entitledCores'])} core đủ cho nhu cầu", "decision": "Không cần mua thêm", "time": ""})
            elif ed["status"] == "insufficient_data":
                items.append({"pri": "tb", "title": f"Windows Server {ED_TXT[ed_key]}: đủ cho VM đã xác nhận nhưng {fmt_int(tot.get('unknownVms'))} VM chưa rõ OS có thể làm thiếu tới {_rng(ed['shortfallCores'], ed.get('shortfallCoresMax'))} core", "decision": "Xác nhận OS các VM chưa rõ trước khi chốt", "time": ""})
            else:
                items.append({"pri": "tb", "title": f"Windows Server {ED_TXT[ed_key]}: nhu cầu {_rng(ed['demandCores'], ed.get('demandCoresMax'))} core, chưa nhập entitlement", "decision": "Nhập entitlement hoặc xác nhận 'Chưa có'", "time": ""})
    # 3. Scope
    excluded = sc.get("excludedClusterKeys") or []
    if partial:
        items.append({"pri": "tb", "title": f"Ngoài phạm vi đợt này: {len(excluded)} cluster ({', '.join(_ckey_label(k) for k in excluded[:6])}{'…' if len(excluded) > 6 else ''}) — chưa tính license",
                      "decision": "Lập đợt mua tiếp theo cho các cluster còn lại", "time": "Đợt sau"})
    # 4. Data gaps
    gaps = (ws.get("dataGaps") or []) + (vm.get("dataGaps") or [])
    unverified = [r for r in (res.get("rules") or {}).values() if r and r.get("status") != "verified"]
    if unverified or gaps:
        items.append({"pri": "info", "title": f"{len(gaps)} điểm cần xác nhận dữ liệu" + (f"; {len(unverified)} rule chưa xác minh" if unverified else ""),
                      "decision": "System Owner của từng cluster xác nhận trước khi ký hợp đồng", "time": ""})

    li = ""
    for i, it in enumerate(items[:6], 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    ws_short = _rng(ed["shortfallCores"], ed.get("shortfallCoresMax")) if ed else "—"
    stats = [
        {"label": "Cluster / host", "value": f"{n_clusters} / {fmt_int(tot['hosts'])}", "sub": f"{fmt_int(tot['sockets'])} socket · {fmt_int(tot['physicalCores'])} physical core", "cls": "na"},
        {"label": "VM Windows Server", "value": fmt_int(tot.get("windowsServerVms")), "sub": f"trên {fmt_int(tot['vms'])} VM · {fmt_int(tot.get('unknownVms'))} chưa rõ OS", "cls": "warn" if tot.get("unknownVms") else "na"},
        {"label": "VMware core thiếu", "value": fmt_int(vm["shortfallCores"]), "sub": f"nhu cầu {fmt_int(vm['demandCores'])} · có {fmt_int(vm['entitledCores'])}", "cls": STATUS_TXT.get(vm["status"], ("", "na"))[1]},
        {"label": f"WS {ED_TXT.get(ed_key, '')} core thiếu".strip(), "value": ws_short, "sub": (f"nhu cầu {_rng(ed['demandCores'], ed.get('demandCoresMax'))} · có {fmt_int(ed['entitledCores'])}" if ed else "không có VM Windows Server"), "cls": STATUS_TXT.get(ed["status"], ("", "na"))[1] if ed else "na"},
    ]
    total_cost = 0.0
    cur = None
    any_cost = False
    if vm.get("cost") and vm["status"] == "shortfall":
        total_cost += vm["cost"]["shortfallCost"] or 0; cur = vm["cost"].get("currency"); any_cost = True
    if ed and ed.get("cost") and ed["status"] == "shortfall":
        total_cost += ed["cost"]["shortfallCost"] or 0; cur = cur or ed["cost"].get("currency"); any_cost = True
    stats.append({"label": "Chi phí bù thiếu hụt", "value": _money(total_cost, cur) if any_cost else "Chưa có đơn giá",
                  "sub": "VMware + Windows Server (edition đề xuất)" if any_cost else "nhập đơn giá để ước tính", "cls": "crit" if any_cost and total_cost > 0 else "na"})
    fg, bg = STATUS_COLOR[status_cls]
    return f"""
    <div class="exec-brief {status_cls}">
      <div class="exec-brief-head">
        <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{esc(status_label.upper())}</span>
        <span class="exec-brief-title">Tổng kết — kết luận &amp; quyết định đề nghị</span>
      </div>
      <div class="exec-brief-headline">{esc(headline)}</div>
      <ol class="rec-sum">{li}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _scope_html(res: Dict[str, Any]) -> str:
    sc = res.get("scope") or {}
    vcs = sc.get("vcenterKeys") or ["Tất cả vCenter"]
    in_scope = [f"{c['cluster']} ({c.get('vcenterKey') or 'import'})" for c in res.get("clusters") or []]
    excluded = [_ckey_label(k) for k in (sc.get("excludedClusterKeys") or [])]
    ex_html = (f"<div><b>Ngoài phạm vi (chưa tính):</b> {esc(', '.join(excluded))}</div>" if excluded
               else "<div><b>Ngoài phạm vi:</b> không — toàn bộ cluster trong vCenter đã được tính.</div>")
    return f"""<div class="insight-box" style="margin-top:6px;">
      <div><b>vCenter:</b> {esc(', '.join(vcs))} &nbsp;·&nbsp; <b>Cluster trong phạm vi mua ({len(in_scope)}):</b> {esc(', '.join(in_scope) or '—')}</div>
      {ex_html}
      <div style="color:{COLORS['muted']};">Windows Server được tính theo cluster vì HA/DRS/vMotion cho phép mọi VM chạy trên mọi host trong cluster; VMware per-core tính cho mọi ESXi host trong phạm vi. Entitlement có phạm vi vCenter/cluster chỉ được đối chiếu khi khớp phạm vi này.</div>
    </div>"""


def _cluster_table(res: Dict[str, Any]) -> str:
    rows = ""
    legacy = bool(res["vmware"].get("legacy"))
    for c in res.get("clusters") or []:
        mob = []
        if c.get("ha"):
            mob.append("HA")
        if c.get("drs"):
            mob.append("DRS")
        rec = c.get("recommendedEdition")
        rec_html = _pill(ED_TXT[rec], "crit" if rec == "datacenter" else "ok") if rec else '<span style="color:#98a2b3;">—</span>'
        notes = []
        if c.get("hostsWithAssumedSockets"):
            notes.append(f"{c['hostsWithAssumedSockets']} host giả định socket")
        if c.get("wsVmsLowConfidence"):
            notes.append(f"{c['wsVmsLowConfidence']} VM WS confidence thấp")
        std = c["standard"]
        rows += (f'<tr><td><b>{esc(c["cluster"])}</b><div style="color:{COLORS["muted"]};font-size:7.4px;">{esc(c.get("vcenterKey") or "import")}</div></td>'
                 f'<td style="text-align:center;">{fmt_int(c["hostCount"])}</td><td style="text-align:center;">{fmt_int(c["sockets"])}</td>'
                 f'<td style="text-align:right;"><b>{fmt_int(c["physicalCores"])}</b></td>'
                 f'<td style="text-align:center;white-space:nowrap;">{esc(" + ".join(mob)) or "—"}</td>'
                 f'<td style="text-align:center;">{fmt_int(c["vmCount"])} / <b>{fmt_int(c["wsVms"])}</b> / {fmt_int(c.get("unknownVms"))}</td>'
                 f'<td style="text-align:right;"><b>{fmt_int(c["vmwareCores"])}</b>{("<div style=\'color:" + COLORS["muted"] + ";font-size:7.2px;\'>" + fmt_int(c.get("vmwareLegacyCpuLicenses")) + " CPU lic.</div>") if legacy else ""}</td>'
                 f'<td style="text-align:right;">{(fmt_int(std.get("sets")) + " bộ → ") if std.get("sets") is not None else ""}<b>{_rng(std["cores"], std.get("coresMax"))}</b></td>'
                 f'<td style="text-align:right;"><b>{fmt_int(c["datacenter"]["cores"])}</b></td>'
                 f'<td style="text-align:center;">{rec_html}</td>'
                 f'<td style="color:{COLORS["muted"]};font-size:7.4px;">{esc("; ".join(notes))}</td></tr>')
    t = res["totals"]
    ws = res["windowsServer"]
    rows += (f'<tr style="font-weight:700;background:{COLORS["surface2"]};"><td>Tổng phạm vi</td><td style="text-align:center;">{fmt_int(t["hosts"])}</td>'
             f'<td style="text-align:center;">{fmt_int(t["sockets"])}</td><td style="text-align:right;">{fmt_int(t["physicalCores"])}</td><td></td>'
             f'<td style="text-align:center;">{fmt_int(t["vms"])} / {fmt_int(t.get("windowsServerVms"))} / {fmt_int(t.get("unknownVms"))}</td>'
             f'<td style="text-align:right;">{fmt_int(res["vmware"]["demandCores"])}</td>'
             f'<td style="text-align:right;">{_rng(ws["standard"]["demandCores"], ws["standard"].get("demandCoresMax"))}</td>'
             f'<td style="text-align:right;">{fmt_int(ws["datacenter"]["demandCores"])}</td><td></td><td></td></tr>')
    return f"""<table class="decision-table lic-table">
      <thead><tr><th>Cluster</th><th>Host</th><th>Socket</th><th style="text-align:right;">Physical core</th><th>HA / DRS</th>
      <th>VM tổng / Windows Server / chưa rõ</th><th style="text-align:right;">VMware core</th><th style="text-align:right;">WS Standard core</th>
      <th style="text-align:right;">WS Datacenter core</th><th>Edition ít core hơn</th><th>Ghi chú</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div class="small-note">Physical core = core vật lý của CPU (không phải vCPU, không đếm hyper-thread). WS Standard core = Σ core licensable mỗi host × số bộ license (ceil(VM Windows Server trong cluster / 2)); khoảng giá trị xuất hiện khi còn VM chưa rõ OS. WS Datacenter core = core licensable của mọi host tính một lần.</div>"""


def _entitlement_table(res: Dict[str, Any]) -> str:
    ws, vm = res["windowsServer"], res["vmware"]
    rec = ws.get("recommendedEdition") or {}
    ed_key = rec.get("byCost") or rec.get("byCores")

    def ent_txt(ent: Dict[str, Any], units: float, unit: str = "core") -> str:
        if ent.get("declaredNone") and units <= 0:
            return _pill("Chưa có (đã xác nhận)", "crit")
        if units <= 0:
            return '<span style="color:#98a2b3;">chưa nhập</span>'
        return f"{fmt_int(units)} {unit}" + (" " + _pill("SA", "ok") if ent.get("hasSa") else "")

    rows = ""
    # VMware per-core
    vmw_ents = vm.get("entitlements") or {}
    owned = [f"{e['label']}: {fmt_int(e['units'])}" for e in vmw_ents.values() if e.get("units")]
    vm_ent = {"declaredNone": vm.get("declaredNone"), "hasSa": False}
    cost = vm.get("cost")
    rows += (f'<tr><td><b>VMware per-core</b> (VCF / VVF / vSphere)<div style="color:{COLORS["muted"]};font-size:7.4px;">{esc("; ".join(owned)) if owned else "max(core, 16 × socket) mỗi host"}</div></td>'
             f'<td style="text-align:right;"><b>{fmt_int(vm["demandCores"])}</b> core</td>'
             f'<td style="text-align:right;">{ent_txt(vm_ent, vm["entitledCores"])}</td>'
             f'<td style="text-align:right;"><b>{fmt_int(vm["shortfallCores"])}</b> core{(" (đặt tối thiểu " + fmt_int(vm.get("orderCores")) + ")") if vm.get("orderCores") not in (None, vm["shortfallCores"]) else ""}</td>'
             f'<td style="text-align:right;">{_money(cost["pricePerCore"], cost.get("currency")) if cost else "—"}{("<div style=\'color:" + COLORS["muted"] + ";font-size:7.2px;\'>" + esc(cost.get("basis") or "") + "</div>") if cost else ""}</td>'
             f'<td style="text-align:right;">{(_money(cost["shortfallCost"], cost.get("currency")) if cost else "—")}</td>'
             f'<td style="text-align:center;">{_status_pill(vm["status"])}</td></tr>')
    leg = vm.get("legacy")
    if leg:
        rows += (f'<tr><td><b>VMware per-CPU</b> (perpetual, legacy)</td><td style="text-align:right;">{fmt_int(leg["demandCpuLicenses"])} CPU</td>'
                 f'<td style="text-align:right;">{ent_txt(leg.get("entitlement") or {}, leg["entitled"], "CPU")}</td><td style="text-align:right;">{fmt_int(leg["shortfall"])} CPU</td>'
                 f'<td></td><td></td><td style="text-align:center;">{_status_pill(leg["status"])}</td></tr>')
    for key in ("standard", "datacenter"):
        o = ws[key]
        ent = o.get("entitlement") or {}
        c = o.get("cost")
        hi = f' style="background:{COLORS["ok_bg"]};"' if key == ed_key else ""
        tag = (" " + _pill("Đề xuất", "ok")) if key == ed_key and res["totals"].get("windowsServerVms") else ""
        rows += (f'<tr{hi}><td><b>Windows Server {ED_TXT[key]}</b>{tag}<div style="color:{COLORS["muted"]};font-size:7.4px;">{_packs_txt(o.get("packs"))}{(" (tối đa " + _packs_txt(o.get("packsMax")) + ")") if o.get("demandCoresMax") not in (None, o["demandCores"]) else ""}</div></td>'
                 f'<td style="text-align:right;"><b>{_rng(o["demandCores"], o.get("demandCoresMax"))}</b> core</td>'
                 f'<td style="text-align:right;">{ent_txt(ent, o["entitledCores"])}</td>'
                 f'<td style="text-align:right;"><b>{_rng(o["shortfallCores"], o.get("shortfallCoresMax"))}</b> core<div style="color:{COLORS["muted"]};font-size:7.2px;">{_packs_txt(o.get("shortfallPacks"))}</div></td>'
                 f'<td style="text-align:right;">{_money(c["pricePerCore"], c.get("currency")) if c else "—"}</td>'
                 f'<td style="text-align:right;">{(_money(c["shortfallCost"], c.get("currency")) + ((" – " + _money(c.get("shortfallCostMax"), c.get("currency"))) if c.get("shortfallCostMax") not in (None, c["shortfallCost"]) else "")) if c else "—"}</td>'
                 f'<td style="text-align:center;">{_status_pill(o["status"])}</td></tr>')
    rec_txt = ""
    if rec.get("byCores"):
        rec_txt = f"Theo số core: <b>{ED_TXT[rec['byCores']]}</b> cần ít core hơn."
        if rec.get("byCost"):
            rec_txt += f" Theo chi phí: <b>{ED_TXT[rec['byCost']]}</b> rẻ hơn {_money(rec.get('saving'), rec.get('currency'))}."
        elif rec.get("note"):
            rec_txt += " " + esc(rec["note"])
    return f"""<table class="decision-table lic-table">
      <thead><tr><th>Sản phẩm</th><th style="text-align:right;">Nhu cầu</th><th style="text-align:right;">Entitlement hiện có</th><th style="text-align:right;">Thiếu hụt</th>
      <th style="text-align:right;">Đơn giá / core</th><th style="text-align:right;">Chi phí bù thiếu</th><th>Trạng thái</th></tr></thead>
      <tbody>{rows}</tbody></table>
      {('<div class="insight-box" style="margin-top:6px;"><b>So sánh edition Windows Server.</b> ' + rec_txt + '</div>') if rec_txt else ''}
      <div class="small-note">Thiếu hụt = max(0, nhu cầu − entitlement trong phạm vi). Chi phí = thiếu hụt × đơn giá/core quy đổi từ đơn giá pack đã nhập; không có giá mặc định. "Chưa có (đã xác nhận)" nghĩa là người dùng đã khai báo chưa mua license sản phẩm này — toàn bộ nhu cầu được coi là thiếu hụt phải mua.</div>"""


def _basis_html(res: Dict[str, Any]) -> str:
    ws, vm = res["windowsServer"], res["vmware"]
    rules = res.get("rules") or {}

    def rule_line(r: Optional[Dict[str, Any]], label: str) -> str:
        if not r:
            return f"<div>• {esc(label)}: <b>chưa có rule active</b> — không thể kết luận.</div>"
        st = "Đã xác minh" if r.get("status") == "verified" else "Chưa xác minh"
        cls = "ok" if r.get("status") == "verified" else "warn"
        ver = f" bởi {esc(r.get('verifiedBy'))} ngày {esc(str(r.get('verifiedAt'))[:10])}" if r.get("verifiedAt") else ""
        src = f'<div style="color:{COLORS["muted"]};font-size:7.4px;word-break:break-all;">Nguồn: {esc(r.get("sourceUrl") or "—")}</div>' if r.get("sourceUrl") else ""
        return f"<div>• {esc(label)}: <b>{esc(r.get('name'))}</b> v{esc(r.get('version'))} {_pill(st, cls)}{ver}{src}</div>"

    def lines(arr: List[str]) -> str:
        return "".join(f"<div>• {esc(x)}</div>" for x in (arr or [])) or "<div>—</div>"

    gaps = (ws.get("dataGaps") or []) + (vm.get("dataGaps") or [])
    return f"""
    <div class="chart-card soft" style="font-size:8.4px;line-height:1.5;margin-bottom:6px;">
      <div class="chart-card-title">Rule licensing áp dụng (version · trạng thái xác minh · nguồn)</div>
      {rule_line(rules.get("windows_server"), "Windows Server")}
      {rule_line(rules.get("vmware"), "VMware per-core")}
      {rule_line(rules.get("vmware_cpu_legacy"), "VMware per-CPU legacy") if rules.get("vmware_cpu_legacy") else ""}
    </div>
    <div class="two-col">
      <div class="chart-card soft" style="font-size:8.2px;line-height:1.5;"><div class="chart-card-title">Công thức &amp; giả định — Windows Server</div>{lines(ws.get("formula"))}<div style="margin-top:3px;color:{COLORS['muted']};">Giả định</div>{lines(ws.get("assumptions"))}</div>
      <div class="chart-card soft" style="font-size:8.2px;line-height:1.5;"><div class="chart-card-title">Công thức &amp; giả định — VMware</div>{lines(vm.get("formula"))}<div style="margin-top:3px;color:{COLORS['muted']};">Giả định</div>{lines(vm.get("assumptions"))}</div>
    </div>
    <div class="insight-box risk" style="margin-top:6px;font-size:8.4px;"><b>Thiếu dữ liệu / cần xác nhận trước khi quyết định ({len(gaps)}).</b> {lines(gaps) if gaps else 'Không có.'}</div>"""


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

def render_license_html(res: Dict[str, Any], logo_data_url: Optional[str] = None, prepared_by: str = "",
                        snapshot: Optional[Dict[str, Any]] = None) -> str:
    generated_at = _now_str()
    sc = res.get("scope") or {}
    n_clusters = len(res.get("clusters") or [])
    scope_eyebrow = ", ".join(sc.get("vcenterKeys") or ["Tất cả vCenter"]) + (f" · {n_clusters} cluster" if sc.get("partial") else "")
    logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    snap_txt = (f"Snapshot #{snapshot['id']} — {esc(snapshot.get('title') or '')} · lưu {esc(str(snapshot.get('createdAt') or '')[:16].replace('T', ' '))} bởi {esc(snapshot.get('createdBy') or '')} · hash {esc(snapshot.get('resultHash') or '')}"
                if snapshot else f"Tính từ inventory hiện tại lúc {esc(str(res.get('generatedAt') or '')[:16].replace('T', ' '))} UTC · hash {esc(res.get('resultHash') or '')}")
    body = f"""
  {_summary_box(res)}
  <div class="section">
    <div class="section-title">Phạm vi đánh giá</div>
    {_scope_html(res)}
  </div>
  <div class="section breakable">
    <div class="section-title">Tổng quan license theo cluster</div>
    <div class="section-sub">Nhu cầu core theo từng cluster trong phạm vi; dòng Edition cho biết edition Windows Server cần ít core hơn ở cluster đó.</div>
    {_cluster_table(res)}
  </div>
  <div class="section">
    <div class="section-title">Nhu cầu · entitlement · thiếu hụt · chi phí</div>
    {_entitlement_table(res)}
  </div>
  <div class="section breakable">
    <div class="section-title">Cơ sở tính toán</div>
    {_basis_html(res)}
    <div class="small-note">{snap_txt}. Báo cáo chỉ dựa trên inventory vCenter, override thủ công, entitlement và rule đã khai báo trong hệ thống; không thay thế tư vấn licensing của hãng/đối tác. Số lượng và chi phí cuối cùng cần đối chiếu hợp đồng, Product Terms hiện hành và báo giá nhà cung cấp.{(' Người lập: ' + esc(prepared_by) + '.') if prepared_by else ''}</div>
  </div>"""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Tổng quan license theo cluster', generated_at)}{_exec_visual_css()}{_planning_css()}
.two-col {{ display:flex; gap:6px; }}
.two-col .chart-card {{ flex:1; }}
.lic-table th, .lic-table td {{ font-size:8px; padding:3.5px 5px; vertical-align:middle; }}
.lic-table th {{ white-space:normal; line-height:1.25; }}
.lic-table td:first-child {{ white-space:nowrap; }}
.rec-sum-item .priority-pill {{ flex:0 0 96px; }}
</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · License Assessment · {esc(scope_eyebrow)} · {esc(generated_at.split(' ')[-1])}</div>
      <div class="cover-title" style="margin-bottom:6px;">Tổng quan license theo cluster — Executive summary</div>
    </div>
    {logo_html}
  </div>
  {body}
</body></html>"""


def build_license_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_license_html(*args, **kwargs))
