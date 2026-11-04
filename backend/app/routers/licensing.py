"""Inventory & License Assessment API.

All endpoints reuse the existing cookie-JWT auth (`get_current_user`) and the
latest snapshot per vCenter from data_access, so live-polled and imported
inventories are assessed through the same path.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import data_access, report_license
from ..config import get_settings
from ..database import get_db
from ..licensing import assess as assess_mod
from ..licensing import os_classify, rules as rules_mod, whatif as whatif_mod
from ..models import LicenseAssessment, LicenseEntitlement, LicenseRuleSet, ReportLogo, User, VmOsOverride
from ..security import get_current_user
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/licensing", tags=["licensing"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _scope_keys(vcenter: Optional[str]) -> Optional[List[str]]:
    return None if (not vcenter or vcenter == "all") else [vcenter]


def _parse_clusters(cluster: Optional[str], clusters: Optional[Any]) -> Dict[str, Any]:
    """Normalise the two ways a cluster scope can arrive:
    - `cluster`  : single name from the global filter (legacy, name-only);
    - `clusters` : comma-separated string or list of "vcenterKey::cluster"
                   keys chosen in the License tab (purchase scope, may be several).
    Returns {"clusterKeys": [...] | None, "clusters": [names] | None}."""
    keys: List[str] = []
    names: List[str] = []
    items: List[str] = []
    if isinstance(clusters, str):
        items = [x.strip() for x in clusters.split(",") if x.strip()]
    elif isinstance(clusters, (list, tuple)):
        items = [str(x).strip() for x in clusters if str(x).strip()]
    for it in items:
        if "::" in it:
            keys.append(it)
            names.append(it.split("::", 1)[1])
        else:
            names.append(it)
    if cluster and cluster != "all":
        # global filter narrows further: keep only selected keys matching that name
        if keys:
            keys = [k for k in keys if k.split("::", 1)[1] == cluster] or [f"::{cluster}"]
            names = [cluster]
        else:
            names = [cluster]
    if not keys and not names:
        return {"clusterKeys": None, "clusters": None}
    return {"clusterKeys": keys or None, "clusters": sorted(set(names)) or None}


def _filter_scope(rows: List[Dict[str, Any]], sc: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not sc.get("clusterKeys") and not sc.get("clusters"):
        return rows
    return [r for r in rows if assess_mod.cluster_in_scope(r, sc)]


def _overrides_map(db: Session) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for o in db.query(VmOsOverride).all():
        d = _override_dict(o)
        out[os_classify.override_key(o.vcenter_key, o.instance_uuid, o.vm)] = d
    return out


def _override_dict(o: VmOsOverride) -> Dict[str, Any]:
    return {"id": o.id, "vcenterKey": o.vcenter_key, "instanceUuid": o.instance_uuid, "vm": o.vm,
            "category": o.category, "osVersion": o.os_version, "osEdition": o.os_edition, "note": o.note,
            "updatedBy": o.updated_by, "updatedAt": to_iso_utc(o.updated_at)}


def _ent_dict(e: LicenseEntitlement) -> Dict[str, Any]:
    return {"id": e.id, "product": e.product, "label": assess_mod.PRODUCT_LABEL.get(e.product, e.product),
            "metric": e.metric, "quantity": e.quantity, "kind": e.kind or "owned",
            "unitPrice": e.unit_price, "currency": e.currency,
            "hasSa": bool(e.has_sa), "scopeVcenter": e.scope_vcenter, "scopeCluster": e.scope_cluster,
            "validTo": to_iso_utc(e.valid_to) if e.valid_to else None, "note": e.note,
            "updatedBy": e.updated_by, "updatedAt": to_iso_utc(e.updated_at)}


def _load_inventory(db: Session, vcenter: Optional[str], cluster: Optional[str] = None, clusters: Optional[Any] = None):
    hosts, vms, run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter), include_import=True)
    sc = _parse_clusters(cluster, clusters)
    all_clusters = sorted({f"{h.get('vcenterKey') or ''}::{h.get('cluster') or ''}" for h in hosts})
    hosts = _filter_scope(hosts, sc)
    vms = _filter_scope(vms, sc)
    classified = os_classify.classify_all(vms, _overrides_map(db))
    return hosts, classified, {"runs": run_meta, "allClusterKeys": all_clusters}


def _entitlements(db: Session) -> List[Dict[str, Any]]:
    return [_ent_dict(e) for e in db.query(LicenseEntitlement).order_by(LicenseEntitlement.product, LicenseEntitlement.id).all()]


def _rules(db: Session) -> Dict[str, Dict[str, Any]]:
    rules_mod.seed_rule_sets(db)
    return rules_mod.active_rules(db)


def _scope(vcenter: Optional[str], cluster: Optional[str], run_meta, clusters: Optional[Any] = None) -> Dict[str, Any]:
    sc = _parse_clusters(cluster, clusters)
    all_keys = (run_meta or {}).get("allClusterKeys") or []
    runs = (run_meta or {}).get("runs")
    in_scope = [k for k in all_keys if assess_mod.cluster_in_scope({"vcenterKey": k.split("::", 1)[0] or None, "cluster": k.split("::", 1)[1]}, sc)]
    excluded = [k for k in all_keys if k not in in_scope]
    return {"vcenterKeys": _scope_keys(vcenter), "clusters": sc["clusters"], "clusterKeys": sc["clusterKeys"],
            "allClusterKeys": all_keys, "excludedClusterKeys": excluded, "partial": bool(excluded),
            "runs": runs}


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@router.get("/clusters")
def clusters_list(vcenter: Optional[str] = Query(default="all"), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every cluster in the vCenter scope (no cluster filter) — drives the
    purchase-scope selector so a subset of production clusters can be licensed first."""
    hosts, vms, _ = data_access.get_latest_combined(db, _scope_keys(vcenter), include_import=True)
    out: Dict[str, Dict[str, Any]] = {}
    for h in hosts:
        k = f"{h.get('vcenterKey') or ''}::{h.get('cluster') or ''}"
        row = out.setdefault(k, {"key": k, "vcenterKey": h.get("vcenterKey"), "cluster": h.get("cluster"), "hostCount": 0,
                                 "physicalCores": 0, "vmCount": 0, "ha": False, "drs": False})
        row["hostCount"] += 1
        row["physicalCores"] += int(h.get("cores") or 0)
        row["ha"] = row["ha"] or bool(h.get("clusterHa"))
        row["drs"] = row["drs"] or bool(h.get("clusterDrs"))
    for v in vms:
        k = f"{v.get('vcenterKey') or ''}::{v.get('cluster') or ''}"
        if k in out:
            out[k]["vmCount"] += 1
    return sorted(out.values(), key=lambda r: (str(r["vcenterKey"]), str(r["cluster"])))


@router.get("/inventory")
def inventory(vcenter: Optional[str] = Query(default="all"), cluster: Optional[str] = Query(default=None),
              clusters: Optional[str] = Query(default=None),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """ESXi hardware inventory (vCenter → cluster → host) + VM OS classification."""
    hosts, classified, run_meta = _load_inventory(db, vcenter, cluster, clusters)
    rules = _rules(db)
    ws = (rules.get("windows_server") or {}).get("params") or {}
    vmc = (rules.get("vmware") or {}).get("params") or {}
    legacy = rules.get("vmware_cpu_legacy")
    host_rows = [assess_mod.host_license_row(h, ws, vmc, (legacy or {}).get("params") if legacy and legacy.get("isActive") else None) for h in hosts]
    vm_by_host: Dict[Any, int] = {}
    for v in classified:
        k = (v.get("vcenterKey"), v.get("host"))
        vm_by_host[k] = vm_by_host.get(k, 0) + 1
    for r in host_rows:
        r["vmCount"] = vm_by_host.get((r["vcenterKey"], r["host"]), 0)
    vm_rows = [{
        "vcenterKey": v.get("vcenterKey"), "cluster": v.get("cluster"), "host": v.get("host"), "vm": v.get("vm"),
        "instanceUuid": v.get("instanceUuid"), "isPoweredOn": v.get("isPoweredOn"), "vcpu": v.get("vcpu"), "ramGB": v.get("ramGB"),
        "overrideKey": v.get("overrideKey"), **{"os": v["os"]},
    } for v in classified]
    summary = os_classify.summarize(classified)
    # ESXi version / model distribution
    dist: Dict[str, Dict[str, int]] = {"esxiVersion": {}, "model": {}}
    for r in host_rows:
        for k in dist:
            val = r.get(k) or "—"
            dist[k][val] = dist[k].get(val, 0) + 1
    return {"hosts": host_rows, "vms": vm_rows, "summary": summary, "distribution": dist, "runMeta": run_meta.get("runs"),
            "scope": _scope(vcenter, cluster, run_meta, clusters),
            "totals": {"hosts": len(host_rows), "sockets": sum(h["sockets"] for h in host_rows),
                       "physicalCores": sum(h["cores"] for h in host_rows),
                       "logicalThreads": sum((h["threads"] or 0) for h in host_rows),
                       "vcpuAllocated": sum(float(v.get("vcpu") or 0) for v in classified), "vms": len(classified)}}


# ---------------------------------------------------------------------------
# OS overrides
# ---------------------------------------------------------------------------

class OverrideIn(BaseModel):
    vcenterKey: Optional[str] = None
    instanceUuid: Optional[str] = None
    vm: str
    category: str
    osVersion: Optional[str] = None
    osEdition: Optional[str] = None
    note: Optional[str] = None


@router.get("/overrides")
def list_overrides(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_override_dict(o) for o in db.query(VmOsOverride).order_by(VmOsOverride.updated_at.desc()).all()]


@router.put("/overrides")
def upsert_override(payload: OverrideIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.category not in os_classify.CATEGORIES:
        raise HTTPException(400, f"category phải thuộc {os_classify.CATEGORIES}")
    q = db.query(VmOsOverride).filter(VmOsOverride.vcenter_key == payload.vcenterKey)
    if payload.instanceUuid:
        row = q.filter(VmOsOverride.instance_uuid == payload.instanceUuid).first()
    else:
        row = q.filter(VmOsOverride.instance_uuid.is_(None), VmOsOverride.vm == payload.vm).first()
    if not row:
        row = VmOsOverride(vcenter_key=payload.vcenterKey, instance_uuid=payload.instanceUuid, vm=payload.vm)
        db.add(row)
    row.vm = payload.vm
    row.category = payload.category
    row.os_version = payload.osVersion
    row.os_edition = payload.osEdition
    row.note = payload.note
    row.updated_by = user.username
    db.commit()
    db.refresh(row)
    return _override_dict(row)


@router.delete("/overrides/{override_id}")
def delete_override(override_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(VmOsOverride).filter(VmOsOverride.id == override_id).first()
    if not row:
        raise HTTPException(404, "Không tìm thấy override")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Rule sets
# ---------------------------------------------------------------------------

class RuleVersionIn(BaseModel):
    params: Dict[str, Any]
    name: Optional[str] = None
    sourceUrl: Optional[str] = None
    sourceNote: Optional[str] = None
    activate: bool = False


class RuleVerifyIn(BaseModel):
    note: Optional[str] = None


@router.get("/rules")
def list_rules(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rules_mod.seed_rule_sets(db)
    return rules_mod.list_rules(db)


@router.post("/rules/{rule_id}/versions")
def new_rule_version(rule_id: int, payload: RuleVersionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    base = db.query(LicenseRuleSet).filter(LicenseRuleSet.id == rule_id).first()
    if not base:
        raise HTTPException(404, "Không tìm thấy rule")
    nv = rules_mod.create_version(db, base, payload.params, payload.name, payload.sourceUrl, payload.sourceNote, user.username, payload.activate)
    return rules_mod.rule_to_dict(nv)


@router.post("/rules/{rule_id}/activate")
def activate_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(LicenseRuleSet).filter(LicenseRuleSet.id == rule_id).first()
    if not r:
        raise HTTPException(404, "Không tìm thấy rule")
    rules_mod.set_active(db, r)
    db.commit()
    return rules_mod.list_rules(db)


@router.post("/rules/{rule_id}/deactivate")
def deactivate_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(LicenseRuleSet).filter(LicenseRuleSet.id == rule_id).first()
    if not r:
        raise HTTPException(404, "Không tìm thấy rule")
    r.is_active = False
    db.commit()
    return rules_mod.list_rules(db)


@router.post("/rules/{rule_id}/verify")
def verify_rule(rule_id: int, payload: RuleVerifyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(LicenseRuleSet).filter(LicenseRuleSet.id == rule_id).first()
    if not r:
        raise HTTPException(404, "Không tìm thấy rule")
    rules_mod.mark_verified(db, r, user.username, payload.note)
    return rules_mod.rule_to_dict(r)


# ---------------------------------------------------------------------------
# Entitlements
# ---------------------------------------------------------------------------

class EntitlementIn(BaseModel):
    product: str
    metric: str = "core"
    kind: str = "owned"          # owned | none ("Chưa có" — xác nhận chưa mua license sản phẩm này)
    quantity: float = Field(default=0, ge=0)
    unitPrice: Optional[float] = Field(default=None, ge=0)
    currency: str = "VND"
    hasSa: bool = False
    scopeVcenter: Optional[str] = None
    scopeCluster: Optional[str] = None
    validTo: Optional[str] = None
    note: Optional[str] = None


@router.get("/entitlements")
def list_entitlements(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"items": _entitlements(db), "products": assess_mod.PRODUCT_LABEL,
            "metrics": {"core": "core", "pack2": "pack 2 core", "pack16": "pack 16 core", "cpu": "CPU (legacy)"},
            "kinds": {"owned": "Đang sở hữu", "none": "Chưa có (chưa mua)"}}


def _apply_ent(row: LicenseEntitlement, p: EntitlementIn, user: User):
    if p.product not in assess_mod.PRODUCT_LABEL:
        raise HTTPException(400, f"product phải thuộc {list(assess_mod.PRODUCT_LABEL)}")
    if p.metric not in ("core", "pack2", "pack16", "cpu"):
        raise HTTPException(400, "metric không hợp lệ")
    if p.product == "vmware_cpu_legacy" and p.metric != "cpu":
        raise HTTPException(400, "VMware per-CPU legacy phải dùng metric 'cpu'")
    if p.product != "vmware_cpu_legacy" and p.metric == "cpu":
        raise HTTPException(400, "Metric 'cpu' chỉ dùng cho VMware per-CPU legacy")
    if p.kind not in ("owned", "none"):
        raise HTTPException(400, "kind phải là 'owned' hoặc 'none'")
    if p.kind == "owned" and not (p.quantity > 0):
        raise HTTPException(400, "Số lượng entitlement phải lớn hơn 0 (hoặc chọn 'Chưa có' nếu chưa mua license)")
    row.kind = p.kind
    row.product, row.metric, row.quantity = p.product, p.metric, (0.0 if p.kind == "none" else p.quantity)
    row.unit_price, row.currency, row.has_sa = p.unitPrice, (p.currency or "VND").upper()[:8], p.hasSa
    row.scope_vcenter = p.scopeVcenter or None
    row.scope_cluster = p.scopeCluster or None
    row.valid_to = datetime.fromisoformat(p.validTo[:10]) if p.validTo else None
    row.note = p.note
    row.updated_by = user.username


@router.post("/entitlements")
def create_entitlement(payload: EntitlementIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = LicenseEntitlement()
    _apply_ent(row, payload, user)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ent_dict(row)


@router.put("/entitlements/{ent_id}")
def update_entitlement(ent_id: int, payload: EntitlementIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(LicenseEntitlement).filter(LicenseEntitlement.id == ent_id).first()
    if not row:
        raise HTTPException(404, "Không tìm thấy entitlement")
    _apply_ent(row, payload, user)
    db.commit()
    db.refresh(row)
    return _ent_dict(row)


@router.delete("/entitlements/{ent_id}")
def delete_entitlement(ent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(LicenseEntitlement).filter(LicenseEntitlement.id == ent_id).first()
    if not row:
        raise HTTPException(404, "Không tìm thấy entitlement")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Assessment (live + snapshots)
# ---------------------------------------------------------------------------

def _run_assess(db: Session, vcenter: Optional[str], cluster: Optional[str], clusters: Optional[Any] = None):
    hosts, classified, run_meta = _load_inventory(db, vcenter, cluster, clusters)
    rules = _rules(db)
    ents = _entitlements(db)
    scope = _scope(vcenter, cluster, run_meta, clusters)
    result = assess_mod.assess(hosts, classified, rules, ents, scope)
    result["generatedAt"] = datetime.utcnow().isoformat() + "Z"
    result["resultHash"] = assess_mod.result_hash(result)
    inputs = {"hosts": hosts, "vms": classified, "rules": rules, "entitlements": ents, "scope": scope}
    return result, inputs


@router.get("/assess")
def assess_live(vcenter: Optional[str] = Query(default="all"), cluster: Optional[str] = Query(default=None),
                clusters: Optional[str] = Query(default=None),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    result, _ = _run_assess(db, vcenter, cluster, clusters)
    return result


class SnapshotIn(BaseModel):
    vcenter: Optional[str] = "all"
    cluster: Optional[str] = None
    clusters: Optional[List[str]] = None
    title: Optional[str] = None
    note: Optional[str] = None


def _snap_meta(s: LicenseAssessment) -> Dict[str, Any]:
    try:
        res = json.loads(s.result_json)
        summary = {"overallStatus": res.get("overallStatus"), "totals": res.get("totals"),
                   "vmware": {k: res["vmware"].get(k) for k in ("demandCores", "entitledCores", "shortfallCores", "status")},
                   "windowsServer": {ed: {k: res["windowsServer"][ed].get(k) for k in ("demandCores", "demandCoresMax", "entitledCores", "shortfallCores", "status")} for ed in ("standard", "datacenter")}}
    except Exception:
        summary = None
    return {"id": s.id, "createdAt": to_iso_utc(s.created_at), "createdBy": s.created_by, "title": s.title, "note": s.note,
            "scope": json.loads(s.scope_json or "{}"), "resultHash": s.result_hash, "summary": summary}


@router.post("/assessments")
def create_snapshot(payload: SnapshotIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    result, inputs = _run_assess(db, payload.vcenter, payload.cluster, payload.clusters)
    snap = LicenseAssessment(
        created_by=user.username, title=payload.title or f"Assessment {datetime.utcnow():%Y-%m-%d %H:%M} UTC",
        note=payload.note, scope_json=json.dumps(result["scope"], ensure_ascii=False, default=str),
        inputs_json=json.dumps(inputs, ensure_ascii=False, default=str),
        result_json=json.dumps(result, ensure_ascii=False, default=str), result_hash=result["resultHash"],
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return _snap_meta(snap)


@router.get("/assessments")
def list_snapshots(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(LicenseAssessment).order_by(LicenseAssessment.created_at.desc()).limit(100).all()
    return [_snap_meta(s) for s in rows]


@router.get("/assessments/{snap_id}")
def get_snapshot(snap_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.query(LicenseAssessment).filter(LicenseAssessment.id == snap_id).first()
    if not s:
        raise HTTPException(404, "Không tìm thấy snapshot")
    meta = _snap_meta(s)
    meta["result"] = json.loads(s.result_json)
    return meta


@router.post("/assessments/{snap_id}/recompute")
def recompute_snapshot(snap_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Re-run the engine on the stored inputs and compare hashes — proves the
    snapshot is reproducible independent of the current inventory."""
    s = db.query(LicenseAssessment).filter(LicenseAssessment.id == snap_id).first()
    if not s:
        raise HTTPException(404, "Không tìm thấy snapshot")
    inputs = json.loads(s.inputs_json)
    res = assess_mod.assess(inputs["hosts"], inputs["vms"], inputs["rules"], inputs["entitlements"], inputs["scope"])
    h = assess_mod.result_hash(res)
    return {"id": s.id, "storedHash": s.result_hash, "recomputedHash": h, "reproducible": h == s.result_hash}


@router.delete("/assessments/{snap_id}")
def delete_snapshot(snap_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.query(LicenseAssessment).filter(LicenseAssessment.id == snap_id).first()
    if not s:
        raise HTTPException(404, "Không tìm thấy snapshot")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# What-if
# ---------------------------------------------------------------------------

class WhatifIn(BaseModel):
    vcenter: Optional[str] = "all"
    cluster: Optional[str] = None
    clusters: Optional[List[str]] = None
    scenario: Dict[str, Any]


@router.post("/whatif")
def whatif(payload: WhatifIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.scenario.get("type") not in whatif_mod.SCENARIO_TYPES:
        raise HTTPException(400, f"scenario.type phải thuộc {whatif_mod.SCENARIO_TYPES}")
    hosts, classified, run_meta = _load_inventory(db, payload.vcenter, None)   # capacity needs every cluster
    rules = _rules(db)
    ents = _entitlements(db)
    scope = _scope(payload.vcenter, payload.cluster, run_meta, payload.clusters)   # license totals follow purchase scope
    policy = data_access.get_effective_policy(db, get_settings())
    try:
        return whatif_mod.run_whatif(hosts, classified, rules, ents, scope, policy, payload.scenario, os_classify.classify_vm)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Executive summary PDF (per-cluster license overview)
# ---------------------------------------------------------------------------

def _ascii_slug(v: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")[:40]


@router.get("/report/pdf")
def report_pdf(vcenter: Optional[str] = Query(default="all"), cluster: Optional[str] = Query(default=None),
               clusters: Optional[str] = Query(default=None), snapshot: Optional[int] = Query(default=None),
               preparedBy: Optional[str] = Query(default=None),
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Executive summary of license demand/shortfall per cluster — from the live
    scope or from a stored snapshot (same renderer, so the PDF matches the UI)."""
    snap_meta = None
    if snapshot:
        s = db.query(LicenseAssessment).filter(LicenseAssessment.id == snapshot).first()
        if not s:
            raise HTTPException(404, "Không tìm thấy snapshot")
        result = json.loads(s.result_json)
        snap_meta = _snap_meta(s)
    else:
        result, _ = _run_assess(db, vcenter, cluster, clusters)
    logo_row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    logo_data_url = f"data:{logo_row.mime_type};base64,{logo_row.data_base64}" if logo_row else None
    try:
        pdf_bytes = report_license.build_license_pdf(result, logo_data_url=logo_data_url, prepared_by=preparedBy or user.username,
                                                     snapshot=snap_meta)
    except Exception as exc:  # pragma: no cover - surfaced to the UI
        raise HTTPException(status_code=500, detail=f"Không thể tạo PDF: {exc}")
    sc = result.get("scope") or {}
    label = "_".join(_ascii_slug(c) for c in (sc.get("clusters") or [])[:3]) or (_ascii_slug(vcenter) if vcenter and vcenter != "all" else "tat_ca")
    filename = f"VCO_License_executive_summary_{label}{('_snapshot' + str(snapshot)) if snapshot else ''}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    from urllib.parse import quote
    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers=headers)
