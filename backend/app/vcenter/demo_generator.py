"""
Synthetic inventory generator used when a vCenter is configured with DEMO=true
(see .env.example). Lets the app be evaluated end-to-end — login, live "sync",
Executive/Technical views, compliance engine, historical trend — before real
vCenter credentials are wired in. Clearly surfaced in the UI as demo data.
"""
import hashlib
import math
import random
import time
import uuid
from typing import Any, Dict, List, Tuple

from ..config import VCenterConfig

CLUSTER_PLAN = [
    # (cluster, hosts, cores/host, RAM GB/host, sockets, vendor/model, ESXi version/build, cpu model)
    ("PROD-CLUSTER-A", 6, 40, 512, 2, ("Dell Inc.", "PowerEdge R750"), ("8.0.2", "22380479"), "Intel(R) Xeon(R) Gold 6338 CPU @ 2.00GHz"),
    ("PROD-CLUSTER-B", 4, 32, 384, 2, ("Dell Inc.", "PowerEdge R740"), ("7.0.3", "21930508"), "Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz"),
    ("DR-CLUSTER", 3, 24, 256, 2, ("HPE", "ProLiant DL380 Gen10"), ("7.0.3", "21930508"), "Intel(R) Xeon(R) Gold 5118 CPU @ 2.30GHz"),
]

# Guest OS mix (weight, guestId, configured guestFullName, Tools guestFullName, guestFamily)
GUEST_OS_PLAN = [
    (26, "windows2019srvNext_64Guest", "Microsoft Windows Server 2019 (64-bit)", "Microsoft Windows Server 2019 (64-bit)", "windowsGuest"),
    (16, "windows2022srvNext_64Guest", "Microsoft Windows Server 2022 (64-bit)", "Microsoft Windows Server 2022 (64-bit)", "windowsGuest"),
    (8, "windows9Server64Guest", "Microsoft Windows Server 2016 (64-bit)", "Microsoft Windows Server 2016 (64-bit)", "windowsGuest"),
    (4, "windows8Server64Guest", "Microsoft Windows Server 2012 R2 (64-bit)", "Microsoft Windows Server 2012 R2 (64-bit)", "windowsGuest"),
    (5, "windows9_64Guest", "Microsoft Windows 10 (64-bit)", "Microsoft Windows 10 (64-bit)", "windowsGuest"),
    (3, "windows11_64Guest", "Microsoft Windows 11 (64-bit)", "Microsoft Windows 11 (64-bit)", "windowsGuest"),
    (12, "rhel8_64Guest", "Red Hat Enterprise Linux 8 (64-bit)", "Red Hat Enterprise Linux 8.8 (Ootpa)", "linuxGuest"),
    (10, "ubuntu64Guest", "Ubuntu Linux (64-bit)", "Ubuntu 22.04.3 LTS", "linuxGuest"),
    (4, "oracleLinux8_64Guest", "Oracle Linux 8 (64-bit)", "Oracle Linux Server 8.9", "linuxGuest"),
    (4, "vmwarePhoton64Guest", "VMware Photon OS (64-bit)", "VMware Photon OS (64-bit)", "linuxGuest"),
    (3, "other4xLinux64Guest", "Other 4.x or later Linux (64-bit)", None, None),
    (2, "otherGuest64", "Other (64-bit)", None, None),
    (3, "windows2019srvNext_64Guest", "Microsoft Windows Server 2019 (64-bit)", None, None),  # tools not running → config evidence only
]


def _seeded_random(key: str) -> random.Random:
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2 ** 32)
    return random.Random(seed)


def collect_inventory(cfg: VCenterConfig) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rnd = _seeded_random(cfg.key)
    # Slow time-based drift so repeated polls show gentle organic growth in the
    # trend charts instead of static flat lines.
    drift = (time.time() / 86400.0) % 30  # 0..30 "day" cycle

    host_rows: List[Dict[str, Any]] = []
    vm_rows: List[Dict[str, Any]] = []

    os_weights = [g[0] for g in GUEST_OS_PLAN]
    for cluster, host_count, cores_per_host, ram_per_host_gb, sockets, (vendor, model), (esxi_ver, esxi_build), cpu_model in CLUSTER_PLAN:
        for hi in range(host_count):
            host_name = f"{cfg.key}-{cluster}-esx{hi + 1:02d}"
            host_rows.append({
                "cluster": cluster,
                "host": host_name,
                "cores": float(cores_per_host),
                "cpuMhz": 2400.0,
                "ramGB": float(ram_per_host_gb),
                "cpuUsagePctRaw": round(20 + rnd.random() * 40, 1),
                "ramUsagePctRaw": round(25 + rnd.random() * 45, 1),
                "status": "connected",
                "vendor": vendor, "model": model, "esxiVersion": esxi_ver, "esxiBuild": esxi_build,
                "cpuModel": cpu_model, "cpuSockets": sockets, "cpuThreads": cores_per_host * 2,
                "clusterHa": True, "clusterDrs": cluster != "DR-CLUSTER",
            })

        vm_count = int(host_count * rnd.uniform(6, 10))
        for vi in range(vm_count):
            host_name = f"{cfg.key}-{cluster}-esx{rnd.randint(1, host_count):02d}"
            is_on = rnd.random() > 0.08
            vcpu = rnd.choice([2, 2, 4, 4, 4, 8, 8, 16])
            ram_gb = rnd.choice([4, 8, 8, 16, 16, 32, 64])
            # Allocation (vCPU/RAM) stays fixed per VM like a real vCenter — any change
            # there is a genuine resize. The slow drift only nudges *usage* so trend
            # charts still move gently between polls.
            growth = 1 + (drift / 30.0) * 0.15  # up to +15% usage growth over the cycle
            cpu_usage = max(0.5, rnd.gauss(18, 14) * growth) if is_on else 0
            idle_days = round(rnd.uniform(0, 75), 1) if is_on and cpu_usage < 8 else None
            power_off_days = round(rnd.uniform(1, 180), 1) if not is_on else None
            prov_gb = round(ram_gb * rnd.uniform(2, 6), 1)
            vm_name = f"{cluster.lower()}-vm-{vi + 1:03d}"
            _, guest_id, guest_full, tools_full, tools_family = rnd.choices(GUEST_OS_PLAN, weights=os_weights)[0]
            tools_running = is_on and tools_full is not None
            if guest_id == "vmwarePhoton64Guest":
                vm_name = f"{cluster.lower()}-{rnd.choice(['vcsa', 'vrops', 'nsx-mgr', 'vrli'])}-{vi + 1:02d}"
            vm_rows.append({
                "cluster": cluster,
                "host": host_name,
                "vm": vm_name,
                # Stable per VM (not per cluster/host) like vCenter's instanceUuid.
                "instanceUuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{cfg.key}/{vm_name}")),
                "powerState": "Powered On" if is_on else "Powered Off",
                "isPoweredOn": is_on,
                "ipAddress": f"10.{rnd.randint(10,60)}.{rnd.randint(0,255)}.{rnd.randint(2,254)}",
                "vcpu": float(vcpu),
                "ramGB": float(ram_gb),
                "diskProvGB": prov_gb,
                "diskUsedGB": round(prov_gb * rnd.uniform(0.3, 0.85), 1),
                "datastore": f"DS-{cluster}-{rnd.randint(1,3):02d}",
                "cpuUsagePctRaw": round(cpu_usage, 1) if is_on else 0.0,
                "ramUsagePctRaw": round(min(98.0, max(2, rnd.gauss(35, 15) * growth)), 1) if is_on else 0.0,
                "lastActivityDays": idle_days,
                "powerOffDays": power_off_days,
                "guestId": guest_id,
                "guestFullName": guest_full,
                "toolsGuestFullName": tools_full if tools_running else None,
                "toolsGuestFamily": tools_family if tools_running else None,
                "toolsStatus": "guestToolsRunning" if tools_running else ("guestToolsNotRunning" if is_on else "guestToolsNotRunning"),
                "guestHostname": (vm_name.upper() if tools_running else None),
            })

    return host_rows, vm_rows
