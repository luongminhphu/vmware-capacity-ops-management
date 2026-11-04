"""Versioned licensing rule sets.

The engine only reads parameters from the active LicenseRuleSet row of each
product. Seeded defaults are taken from public vendor documentation (URL kept
on the row) and start as `needs_verification`: the UI shows this status on
every result until someone records verified_by / verified_at.

Nothing here contains prices. Unit prices are user-entered entitlements.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import LicenseRuleSet

PRODUCTS = ["windows_server", "vmware"]

# --- Windows Server (core-based, 2016 and later) -----------------------------
WS_DEFAULT_PARAMS: Dict[str, Any] = {
    # Per physical host licensing floor (Microsoft core-based model)
    "min_cores_per_proc": 8,
    "min_cores_per_host": 16,
    # Packaging
    "pack_sizes": [2, 16],
    "default_pack_size": 16,
    # Standard edition: 1 full host license (all cores) grants 2 OSEs; stack licenses for more
    "standard_oses_per_license": 2,
    # How to count Standard OSEs when the cluster has HA/DRS/vMotion:
    #   cluster_total      – every Windows Server VM in the cluster may land on any host (default, conservative)
    #   current_placement  – license only VMs on each host today (reference only, requires license mobility/SA)
    "standard_peak_mode": "cluster_total",
    "include_powered_off": True,
    # Datacenter edition: license all cores once per host, unlimited OSEs
    "datacenter_unlimited_oses": True,
    # Windows Client VMs (Win 10/11) are NOT covered by Windows Server licenses
    "client_os_out_of_scope": True,
    # Unknown/other VMs: model as range (0 .. count) rather than guess
    "unknown_as_range": True,
    # Optional: per-user/device CAL reminder (not computed)
    "cal_note": "CAL (User/Device) không tính trong công cụ này — kiểm tra riêng theo số người dùng.",
}

# --- VMware (Broadcom per-core subscription, 2024+) ---------------------------
VMWARE_CORE_PARAMS: Dict[str, Any] = {
    "metric": "core",
    "min_cores_per_cpu": 16,          # Broadcom KB 313548: minimum 16 cores per physical CPU
    "count_all_hosts": True,          # every ESXi host in the vCenter must be licensed
    "min_cores_per_order": 0,         # 72-core per-order minimum reported Mar-2025 then withdrawn; keep 0 unless confirmed in your contract
    "min_cores_per_order_note": "Không có trong tài liệu Broadcom hiện hành — chỉ bật khi hợp đồng ghi rõ.",
    "products": ["vsphere_std", "vsphere_ent_plus", "vvf", "vcf"],
}

VMWARE_CPU_LEGACY_PARAMS: Dict[str, Any] = {
    "metric": "cpu",
    "cores_per_cpu_license": 32,      # legacy perpetual: 1 CPU license covers up to 32 cores; >32 cores needs a 2nd license
    "count_all_hosts": True,
    "products": ["vmware_cpu_legacy"],
}

SEED_RULE_SETS: List[Dict[str, Any]] = [
    {
        "product": "windows_server", "key": "ws_core_based", "version": 1,
        "name": "Windows Server 2016–2025 core-based (Standard / Datacenter)",
        "params": WS_DEFAULT_PARAMS,
        "source_url": "https://www.microsoft.com/licensing/guidance/Core-based-licensing-models",
        "source_note": "Microsoft Core-based licensing guidance; Product Terms (Windows Server Standard/Datacenter); Windows Server virtualization guidance. "
                       "Tối thiểu 8 core/CPU, 16 core/host; Standard: 2 OSE mỗi lần license đủ core; Datacenter: không giới hạn OSE. "
                       "Nguồn phụ: https://www.microsoft.com/licensing/terms/productoffering/WindowsServerStandardDatacenterEssentials/MCA ; "
                       "https://www.microsoft.com/licensing/guidance/Windows-Server-Virtualization-Technologies ; "
                       "https://www.microsoft.com/licensing/guidance/Windows-Server-2025",
        "is_active": True,
    },
    {
        "product": "vmware", "key": "vmware_core_subscription", "version": 1,
        "name": "VMware per-core subscription (VCF / VVF / vSphere Std, Broadcom 2024+)",
        "params": VMWARE_CORE_PARAMS,
        "source_url": "https://knowledge.broadcom.com/external/article/313548/counting-cores-for-vmware-cloud-foundati.html",
        "source_note": "Broadcom KB 313548 (đếm core, tối thiểu 16 core/CPU); VCF licensing model; vSphere Standard SPD 11/2025. "
                       "Nguồn phụ: https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/licensing/licensing-overview/licensing-model.html ; "
                       "https://ftpdocs.broadcom.com/cadocs/0/contentimages/VMware_vSphere_Standard_SPD_November2025.pdf",
        "is_active": True,
    },
    {
        "product": "vmware", "key": "vmware_cpu_legacy", "version": 1,
        "name": "VMware per-CPU perpetual (legacy, ≤32 core/CPU)",
        "params": VMWARE_CPU_LEGACY_PARAMS,
        "source_url": "https://www.vmware.com/docs/vmware-product-guide-april-2024",
        "source_note": "VMware Product Guide 04/2024: 1 license/CPU tối đa 32 core vật lý; CPU >32 core cần thêm license. Chỉ dùng cho entitlement perpetual còn hiệu lực.",
        "is_active": False,
    },
]


def seed_rule_sets(db: Session) -> int:
    """Insert seed rows that do not exist yet (by product+key+version). Idempotent."""
    added = 0
    for s in SEED_RULE_SETS:
        exists = db.query(LicenseRuleSet).filter_by(product=s["product"], key=s["key"], version=s["version"]).first()
        if exists:
            continue
        db.add(LicenseRuleSet(
            product=s["product"], key=s["key"], version=s["version"], name=s["name"],
            params_json=json.dumps(s["params"], ensure_ascii=False),
            source_url=s["source_url"], source_note=s["source_note"],
            status="needs_verification", is_active=bool(s.get("is_active")), created_by="seed",
        ))
        added += 1
    if added:
        db.commit()
    return added


def rule_to_dict(r: LicenseRuleSet) -> Dict[str, Any]:
    try:
        params = json.loads(r.params_json or "{}")
    except Exception:
        params = {}
    return {
        "id": r.id, "product": r.product, "key": r.key, "version": r.version, "name": r.name,
        "params": params, "sourceUrl": r.source_url, "sourceNote": r.source_note,
        "verifiedAt": r.verified_at.isoformat() if r.verified_at else None, "verifiedBy": r.verified_by,
        "status": r.status, "isActive": bool(r.is_active),
        "createdAt": r.created_at.isoformat() if r.created_at else None, "createdBy": r.created_by,
    }


def list_rules(db: Session) -> List[Dict[str, Any]]:
    rows = db.query(LicenseRuleSet).order_by(LicenseRuleSet.product, LicenseRuleSet.key, LicenseRuleSet.version.desc()).all()
    return [rule_to_dict(r) for r in rows]


def active_rules(db: Session) -> Dict[str, Dict[str, Any]]:
    """{product: rule_dict} for active rule per product. VMware may have one active
    rule; the engine evaluates core-subscription and (if active) legacy per-CPU."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in db.query(LicenseRuleSet).filter(LicenseRuleSet.is_active.is_(True)).all():
        d = rule_to_dict(r)
        out.setdefault(d["product"], d)
        out[d["key"]] = d  # also reachable by key
    return out


def create_version(db: Session, base: LicenseRuleSet, params: Dict[str, Any], name: Optional[str],
                   source_url: Optional[str], source_note: Optional[str], user: str, activate: bool) -> LicenseRuleSet:
    """Rules are never edited in place; a new version row is created so old
    assessments still reference exactly the parameters they used."""
    latest = db.query(LicenseRuleSet).filter_by(product=base.product, key=base.key).order_by(LicenseRuleSet.version.desc()).first()
    nv = LicenseRuleSet(
        product=base.product, key=base.key, version=(latest.version if latest else base.version) + 1,
        name=name or base.name, params_json=json.dumps(params, ensure_ascii=False),
        source_url=source_url if source_url is not None else base.source_url,
        source_note=source_note if source_note is not None else base.source_note,
        status="needs_verification", is_active=False, created_by=user,
    )
    db.add(nv)
    db.flush()
    if activate:
        set_active(db, nv)
    db.commit()
    db.refresh(nv)
    return nv


def set_active(db: Session, rule: LicenseRuleSet) -> None:
    """Activate one version of a key; deactivate other versions of the same key."""
    for other in db.query(LicenseRuleSet).filter_by(product=rule.product, key=rule.key).all():
        other.is_active = (other.id == rule.id)
    db.flush()


def mark_verified(db: Session, rule: LicenseRuleSet, user: str, note: Optional[str]) -> None:
    rule.status = "verified"
    rule.verified_at = datetime.utcnow()
    rule.verified_by = user
    if note:
        rule.source_note = ((rule.source_note or "") + "\n" + note).strip()
    db.commit()
