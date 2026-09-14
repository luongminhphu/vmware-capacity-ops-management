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
from typing import Any, Dict, List, Tuple

from ..config import VCenterConfig

CLUSTER_PLAN = [
    ("PROD-CLUSTER-A", 6, 40, 512),
    ("PROD-CLUSTER-B", 4, 32, 384),
    ("DR-CLUSTER", 3, 24, 256),
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

    for cluster, host_count, cores_per_host, ram_per_host_gb in CLUSTER_PLAN:
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
            })

        vm_count = int(host_count * rnd.uniform(6, 10))
        for vi in range(vm_count):
            host_name = f"{cfg.key}-{cluster}-esx{rnd.randint(1, host_count):02d}"
            is_on = rnd.random() > 0.08
            vcpu = rnd.choice([2, 2, 4, 4, 4, 8, 8, 16])
            ram_gb = rnd.choice([4, 8, 8, 16, 16, 32, 64])
            growth = 1 + (drift / 30.0) * 0.15  # up to +15% allocation growth over the cycle
            cpu_usage = max(0.5, rnd.gauss(18, 14)) if is_on else 0
            idle_days = round(rnd.uniform(0, 75), 1) if is_on and cpu_usage < 8 else None
            power_off_days = round(rnd.uniform(1, 180), 1) if not is_on else None
            prov_gb = round(ram_gb * rnd.uniform(2, 6), 1)
            vm_rows.append({
                "cluster": cluster,
                "host": host_name,
                "vm": f"{cluster.lower()}-vm-{vi + 1:03d}",
                "powerState": "Powered On" if is_on else "Powered Off",
                "isPoweredOn": is_on,
                "ipAddress": f"10.{rnd.randint(10,60)}.{rnd.randint(0,255)}.{rnd.randint(2,254)}",
                "vcpu": float(round(vcpu * growth)),
                "ramGB": float(round(ram_gb * growth, 1)),
                "diskProvGB": prov_gb,
                "diskUsedGB": round(prov_gb * rnd.uniform(0.3, 0.85), 1),
                "datastore": f"DS-{cluster}-{rnd.randint(1,3):02d}",
                "cpuUsagePctRaw": round(cpu_usage, 1) if is_on else 0.0,
                "ramUsagePctRaw": round(max(2, rnd.gauss(35, 15)), 1) if is_on else 0.0,
                "lastActivityDays": idle_days,
                "powerOffDays": power_off_days,
            })

    return host_rows, vm_rows
