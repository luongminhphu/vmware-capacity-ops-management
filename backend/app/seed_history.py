"""
Dev/demo helper: back-fill synthetic snapshot history for DEMO vCenters so the
"Biến động tài nguyên" charts, VM added/removed appendix and trend forecast
have something to show before months of real polling have accumulated.

Only vCenters configured with VCENTER_i_DEMO=true are touched; real vCenter
history is never fabricated.

Usage (inside the app container):
    python -m app.seed_history --months 12 --step-days 7
"""
import argparse
import datetime as dt
import random

from . import capacity
from .collectors.scheduler import _policy_from_settings
from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import ClusterAggregate, HostSnapshot, SnapshotRun, VmSnapshot
from .vcenter import demo_generator


def _safe(x):
    return None if x is None or x != x else x


def _persist(db, key, hosts, vms, when, policy):
    run = SnapshotRun(vcenter_key=key, source="live", status="ok", started_at=when, finished_at=when)
    db.add(run)
    db.flush()
    for h in hosts:
        db.add(HostSnapshot(run_id=run.id, vcenter_key=key, cluster=h["cluster"], host=h["host"],
                            cores=h["cores"], cpu_mhz=h.get("cpuMhz", 0), ram_gb=h["ramGB"],
                            cpu_usage_pct_raw=h.get("cpuUsagePctRaw"), ram_usage_pct_raw=h.get("ramUsagePctRaw"),
                            status=h.get("status", "connected"), captured_at=when))
    for v in vms:
        db.add(VmSnapshot(run_id=run.id, vcenter_key=key, cluster=v["cluster"], host=v.get("host"), vm=v["vm"],
                          instance_uuid=v.get("instanceUuid"), power_state=v["powerState"], is_powered_on=v["isPoweredOn"], ip_address=v.get("ipAddress"),
                          vcpu=v["vcpu"], ram_gb=v["ramGB"], disk_prov_gb=v["diskProvGB"], disk_used_gb=v["diskUsedGB"],
                          datastore=v["datastore"], cpu_usage_pct_raw=v.get("cpuUsagePctRaw"),
                          ram_usage_pct_raw=v.get("ramUsagePctRaw"), captured_at=when))
    for c in capacity.build_cluster_rows(hosts, vms, policy):
        m = c["model"]
        db.add(ClusterAggregate(run_id=run.id, vcenter_key=key, cluster=c["name"], host_count=c["hosts"], vm_count=c["vms"],
                                total_cores=m["totalCores"], total_logical_cores=m["totalLogicalCores"],
                                total_ram_gb=m["totalRamGB"], used_vcpu=m["usedVcpu"], used_ram_gb=m["usedRamGB"],
                                prov_storage_gb=m["provStorageGB"], used_storage_gb=m["usedStorageGB"],
                                cpu_overcommit=_safe(m["cpuOvercommit"]), ram_overcommit=_safe(m["ramOvercommit"]),
                                cpu_pct=_safe(m["cpuPct"]), ram_pct=_safe(m["ramPct"]), captured_at=when))
    db.commit()


def main():
    ap = argparse.ArgumentParser(description="Back-fill synthetic snapshot history for DEMO vCenters")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--step-days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    settings = get_settings()
    policy = _policy_from_settings(settings)
    Base.metadata.create_all(bind=engine)
    rng = random.Random(args.seed)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    total_days = round(args.months * 30.44)
    db = SessionLocal()
    try:
        for cfg in settings.vcenters():
            if not cfg.demo:
                print(f"skip {cfg.key}: not a demo vCenter")
                continue
            hosts, vms = demo_generator.collect_inventory(cfg)
            # Older snapshots: fewer VMs (growth), a few VMs that later got
            # decommissioned, and one cluster that gained a host mid-window.
            retired = [dict(v, vm=f"{v['vm']}-old", instanceUuid=f"{v['instanceUuid']}-old")
                       for v in rng.sample(vms, max(3, len(vms) // 20))]
            first_cluster = hosts[0]["cluster"] if hosts else None
            # "False positive" scenarios the appendix must NOT count as add/remove:
            # the same VMs (same instanceUuid) lived elsewhere / under another name
            # in the older half of the window (age > 0.45, i.e. visible in the 6- and
            # 12-month windows). Plus a few genuine resizes.
            clusters = sorted({h["cluster"] for h in hosts})
            by_cluster = {c: [v for v in vms if v["cluster"] == c] for c in clusters}
            cl_a, cl_b, cl_dr = clusters[1], clusters[2], clusters[0]  # PROD-CLUSTER-A, PROD-CLUSTER-B, DR-CLUSTER
            hosts_a = [h["host"] for h in hosts if h["cluster"] == cl_a]
            moved_x = {v["vm"] for v in by_cluster[cl_b][:3]}          # B <- A cross-cluster migration
            moved_h = {v["vm"] for v in by_cluster[cl_a][3:7]}         # vMotion inside cluster A
            renamed = {v["vm"] for v in by_cluster[cl_a][8:10]}        # VM rename (same UUID)
            resized = {v["vm"] for v in by_cluster[cl_b][4:7]}         # genuine resize
            legacy_dr = f"{cl_dr}-OLD"                                 # cluster rename DR-CLUSTER-OLD -> DR-CLUSTER

            def _back_in_time(v, age):
                v = dict(v)
                if age > 0.45:
                    if v["vm"] in moved_x:
                        v["cluster"], v["host"] = cl_a, hosts_a[0]
                    elif v["vm"] in moved_h:
                        v["host"] = hosts_a[-1] if v["host"] != hosts_a[-1] else hosts_a[0]
                    elif v["vm"] in renamed:
                        v["vm"] = v["vm"].replace("-vm-", "-legacy-")
                    elif v["vm"] in resized:
                        v["vcpu"], v["ramGB"] = max(1.0, v["vcpu"] / 2), max(2.0, v["ramGB"] / 2)
                if age > 0.47 and v["cluster"] == cl_dr:
                    v["cluster"] = legacy_dr
                return v

            n_runs = 0
            for back in range(total_days, 0, -args.step_days):
                when = now - dt.timedelta(days=back, hours=rng.randint(0, 5))
                age = back / float(total_days)  # 1.0 = oldest
                keep = int(len(vms) * (1.0 - 0.28 * age))
                snap_vms = [_back_in_time(v, age) for v in vms[:keep]]
                # retired VMs exist in the older half of the window only
                if age > 0.45:
                    snap_vms = snap_vms + [_back_in_time(v, age) for v in retired]
                snap_hosts = hosts
                if age > 0.6 and first_cluster:
                    snap_hosts = [h for h in hosts if not (h["cluster"] == first_cluster and h is hosts[0])]
                if age > 0.47:
                    snap_hosts = [dict(h, cluster=legacy_dr) if h["cluster"] == cl_dr else h for h in snap_hosts]
                _persist(db, cfg.key, snap_hosts, snap_vms, when, policy)
                n_runs += 1
            print(f"{cfg.key}: seeded {n_runs} historical runs over {args.months} months")
    finally:
        db.close()


if __name__ == "__main__":
    main()
