"""Guest OS classification from vCenter evidence.

Evidence quality, strongest first:
  manual  – operator override (always wins, confidence 'confirmed')
  tools   – guest.guestFullName / guestFamily reported by a running VMware Tools
  config  – config.guestId / guestFullName chosen when the VM was created (can be stale)
  name    – heuristics on the VM name (weak; only used when nothing else exists)

Categories: windows_server | windows_client | linux | appliance | other_unknown

Edition (Standard / Datacenter) is NOT exposed by vCenter APIs — it is only
ever filled from a manual override. The assessment therefore never pretends to
know which edition a VM runs; it evaluates both licensing options per cluster.
"""
import re
from typing import Any, Dict, List, Optional

CATEGORIES = ["windows_server", "windows_client", "linux", "appliance", "other_unknown"]
CATEGORY_LABEL = {
    "windows_server": "Windows Server",
    "windows_client": "Windows Client",
    "linux": "Linux",
    "appliance": "Appliance",
    "other_unknown": "Other / Unknown",
}
CONFIDENCE_ORDER = {"confirmed": 4, "high": 3, "medium": 2, "low": 1, "none": 0}

# Appliance markers in VM names (VMware / common vendor virtual appliances).
_APPLIANCE_NAME_RE = re.compile(
    r"(vcsa|vcenter|nsx|vrops|vrli|aria|vrealize|vcf|sddc|avi-|avi_|photon|esxi|vsan-witness|witness|"
    r"veeam-proxy|fortigate|paloalto|pa-vm|f5-|bigip|citrix-adc|netscaler|nutanix|idrac|ilo|"
    r"nexus1000|ucs|cisco|checkpoint|sophos|unifi|vapp)",
    re.IGNORECASE,
)
_WIN_SERVER_RE = re.compile(r"windows\s*(server|.*srv)|win(dows)?\s*(server|20(03|08|12|16|19|22|25))|longhorn", re.IGNORECASE)
_WIN_CLIENT_RE = re.compile(r"windows\s*(xp|vista|7|8|8\.1|10|11)\b|win(dows)?\s*(7|8|10|11)\b", re.IGNORECASE)
_LINUX_RE = re.compile(
    r"linux|rhel|red\s*hat|centos|ubuntu|debian|suse|sles|oracle\s*linux|fedora|rocky|alma|amazon|photon|coreos|flatcar|asianux|freebsd|other\s*[2345]\.x",
    re.IGNORECASE,
)
_WIN_SERVER_VERSION_RE = re.compile(r"windows\s*server\s*(20\d\d(?:\s*R2)?)", re.IGNORECASE)
_WIN_CLIENT_VERSION_RE = re.compile(r"windows\s*(xp|vista|7|8\.1|8|10|11)\b", re.IGNORECASE)
_LINUX_VERSION_RE = re.compile(
    r"(red\s*hat\s*enterprise\s*linux|rhel|centos|ubuntu|debian|sles|suse|oracle\s*linux(?:\s*server)?|rocky\s*linux|almalinux|fedora|photon\s*os|amazon\s*linux)\s*([0-9][0-9.]*(?:\s*lts)?)?",
    re.IGNORECASE,
)

# guestId prefixes (vim.vm.GuestOsDescriptor.GuestOsIdentifier)
_GUESTID_SERVER_RE = re.compile(r"^(windows(7|8|9)Server|windows20(03|08|12|16|19|22|25)|windows\d{4}srv|winNetEnterprise|winNetStandard|winNetDatacenter|winNetWeb|winNetBusiness|winLonghorn|windowsHyperV)", re.IGNORECASE)
_GUESTID_CLIENT_RE = re.compile(r"^(windows(7|8|9|11|12)(_64)?Guest|winXP|winVista|win7|win8|win9|win10|win11|windows9_64|windows11_64|windows12_64)", re.IGNORECASE)
_GUESTID_PHOTON_RE = re.compile(r"photon", re.IGNORECASE)
_GUESTID_LINUX_RE = re.compile(r"(linux|rhel|centos|ubuntu|debian|sles|suse|oracle|fedora|asianux|coreos|freebsd|rocky|alma|amazon|other\dx)", re.IGNORECASE)


def _clean(s: Optional[str]) -> str:
    return (s or "").strip()


def _server_version(text: str) -> Optional[str]:
    m = _WIN_SERVER_VERSION_RE.search(text)
    if m:
        return "Windows Server " + re.sub(r"\s+", " ", m.group(1)).upper().replace("R2", "R2")
    return None


def _client_version(text: str) -> Optional[str]:
    m = _WIN_CLIENT_VERSION_RE.search(text)
    return ("Windows " + m.group(1)) if m else None


def _linux_version(text: str) -> Optional[str]:
    m = _LINUX_VERSION_RE.search(text)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    ver = (m.group(2) or "").strip()
    return (name + (" " + ver if ver else "")).strip()


def _classify_text(text: str, guest_id: str = "") -> Dict[str, Any]:
    """Classify one free-text OS description (+ optional guestId)."""
    t = text or ""
    gid = guest_id or ""
    # Appliance first: Photon OS is VMware's appliance base OS.
    if _GUESTID_PHOTON_RE.search(gid) or re.search(r"photon", t, re.IGNORECASE):
        return {"category": "appliance", "osVersion": _linux_version(t) or "VMware Photon OS"}
    if _GUESTID_SERVER_RE.search(gid) or _WIN_SERVER_RE.search(t):
        return {"category": "windows_server", "osVersion": _server_version(t)}
    if _GUESTID_CLIENT_RE.search(gid) or _WIN_CLIENT_RE.search(t):
        return {"category": "windows_client", "osVersion": _client_version(t)}
    if re.search(r"windows", t, re.IGNORECASE) or re.search(r"^win", gid, re.IGNORECASE):
        # Windows but neither server nor client pattern matched: keep as unknown Windows flavour
        return {"category": "other_unknown", "osVersion": _clean(t) or None, "windowsLike": True}
    if _GUESTID_LINUX_RE.search(gid) or _LINUX_RE.search(t):
        return {"category": "linux", "osVersion": _linux_version(t)}
    return {"category": "other_unknown", "osVersion": _clean(t) or None}


def classify_vm(vm: Dict[str, Any], override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return classification dict for one VM row (camelCase keys from data_access).

    {
      category, categoryLabel, osVersion, osEdition, source, confidence,
      evidence: {guestId, guestFullName, toolsGuestFullName, toolsGuestFamily, toolsStatus},
      overridden: bool, note
    }
    """
    evidence = {
        "guestId": vm.get("guestId"),
        "guestFullName": vm.get("guestFullName"),
        "toolsGuestFullName": vm.get("toolsGuestFullName"),
        "toolsGuestFamily": vm.get("toolsGuestFamily"),
        "toolsStatus": vm.get("toolsStatus"),
        "guestHostname": vm.get("guestHostname"),
    }
    name = _clean(vm.get("vm"))
    result: Dict[str, Any] = {
        "category": "other_unknown", "osVersion": None, "osEdition": None,
        "source": "none", "confidence": "none", "evidence": evidence, "overridden": False, "note": None,
    }

    tools_full = _clean(vm.get("toolsGuestFullName"))
    tools_family = _clean(vm.get("toolsGuestFamily"))
    tools_running = _clean(vm.get("toolsStatus")).lower() == "guesttoolsrunning"
    cfg_full = _clean(vm.get("guestFullName"))
    cfg_id = _clean(vm.get("guestId"))

    # 1) VMware Tools evidence (what the guest itself reports)
    if tools_full or tools_family:
        c = _classify_text(tools_full, "")
        if c["category"] == "other_unknown" and tools_family:
            if tools_family.lower() == "windowsguest":
                c = {"category": "other_unknown", "osVersion": tools_full or None, "windowsLike": True}
            elif tools_family.lower() == "linuxguest":
                c = {"category": "linux", "osVersion": _linux_version(tools_full)}
        result.update({"category": c["category"], "osVersion": c.get("osVersion"),
                       "source": "tools", "confidence": "high" if tools_running else "medium"})
        if c.get("windowsLike"):
            result["note"] = "VMware Tools báo Windows nhưng không xác định Server/Client"
    # 2) Configured guest OS (set by the admin at creation; may be stale)
    elif cfg_full or cfg_id:
        c = _classify_text(cfg_full, cfg_id)
        result.update({"category": c["category"], "osVersion": c.get("osVersion"),
                       "source": "config", "confidence": "medium" if c["category"] != "other_unknown" else "low"})
        if c["category"] == "windows_server" and not tools_running:
            result["note"] = "Theo cấu hình VM (VMware Tools không chạy) — cần xác nhận OS thực tế"
    # 3) VM name heuristics
    elif name:
        c = _classify_text(name, "")
        if c["category"] != "other_unknown":
            result.update({"category": c["category"], "osVersion": c.get("osVersion"), "source": "name", "confidence": "low"})

    # Appliance by name even when the OS evidence says Linux/Other (vendor appliances)
    if result["category"] in ("linux", "other_unknown") and _APPLIANCE_NAME_RE.search(name):
        result["category"] = "appliance"
        result["note"] = (result.get("note") or "") + (" · " if result.get("note") else "") + "Tên VM khớp mẫu appliance"
        if result["confidence"] == "none":
            result["source"], result["confidence"] = "name", "low"

    # Generic Linux "Other 4.x Linux" configured type → still Linux but low confidence
    if result["category"] == "other_unknown" and re.search(r"other.*linux", cfg_full, re.IGNORECASE):
        result.update({"category": "linux", "source": result["source"] if result["source"] != "none" else "config", "confidence": "low"})

    # 4) Manual override wins
    if override and override.get("category") in CATEGORIES:
        result.update({
            "category": override["category"],
            "osVersion": override.get("osVersion") or result.get("osVersion"),
            "osEdition": override.get("osEdition"),
            "source": "manual", "confidence": "confirmed", "overridden": True,
            "note": override.get("note") or result.get("note"),
        })

    result["categoryLabel"] = CATEGORY_LABEL[result["category"]]
    return result


def override_key(vcenter_key: Optional[str], instance_uuid: Optional[str], vm_name: str) -> str:
    """Lookup key used to match overrides to VM rows."""
    if instance_uuid:
        return f"{vcenter_key or 'import'}|uuid|{instance_uuid}"
    return f"{vcenter_key or 'import'}|name|{vm_name}"


def classify_all(vms: List[Dict[str, Any]], overrides: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach `os` classification to each VM row (returns new dicts)."""
    out = []
    for v in vms:
        key = override_key(v.get("vcenterKey"), v.get("instanceUuid"), v.get("vm") or "")
        ov = overrides.get(key)
        if ov is None and v.get("instanceUuid"):
            ov = overrides.get(override_key(v.get("vcenterKey"), None, v.get("vm") or ""))
        row = dict(v)
        row["os"] = classify_vm(v, ov)
        row["overrideKey"] = key
        out.append(row)
    return out


def summarize(classified: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_cat = {c: {"count": 0, "poweredOn": 0, "vcpu": 0.0, "ramGB": 0.0} for c in CATEGORIES}
    by_conf = {"confirmed": 0, "high": 0, "medium": 0, "low": 0, "none": 0}
    by_source = {"manual": 0, "tools": 0, "config": 0, "name": 0, "none": 0}
    versions: Dict[str, int] = {}
    for v in classified:
        o = v["os"]
        b = by_cat[o["category"]]
        b["count"] += 1
        b["poweredOn"] += 1 if v.get("isPoweredOn") else 0
        b["vcpu"] += float(v.get("vcpu") or 0)
        b["ramGB"] += float(v.get("ramGB") or 0)
        by_conf[o["confidence"]] = by_conf.get(o["confidence"], 0) + 1
        by_source[o["source"]] = by_source.get(o["source"], 0) + 1
        if o["category"] in ("windows_server", "windows_client") and o.get("osVersion"):
            versions[o["osVersion"]] = versions.get(o["osVersion"], 0) + 1
    return {"byCategory": by_cat, "byConfidence": by_conf, "bySource": by_source,
            "windowsVersions": dict(sorted(versions.items(), key=lambda kv: -kv[1])), "total": len(classified)}
