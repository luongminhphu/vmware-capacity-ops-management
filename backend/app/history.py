"""Snapshot history: CPU/RAM change over time per vCenter / cluster, and the
list of VMs added / removed inside a look-back window.

Everything here is derived from the persisted snapshot tables
(ClusterAggregate for capacity, VmSnapshot for VM inventory) so it works the
same for live vCenter polls and manual imports (vcenter_key = None, shown as
"Import thủ công"). The same functions feed the web dashboard endpoints and
the Executive / Technical PDF reports so numbers always agree.
"""
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import capacity
from .models import ClusterAggregate, SnapshotRun, VmSnapshot
from .timeutil import to_iso_utc, to_vn_date

ALLOWED_MONTHS = (3, 6, 12)
IMPORT_LABEL = "Import thủ công"


def normalize_months(value: Optional[int]) -> int:
    try:
        m = int(value) if value is not None else 12
    except (TypeError, ValueError):
        m = 12
    return m if m in ALLOWED_MONTHS else 12


def window_start(months: int, now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.utcnow()
    # Calendar-ish months (30.44 days) keep "3 tháng" meaning ~1 quarter.
    return now - dt.timedelta(days=round(months * 30.44))


def _finite(x: Optional[float]) -> bool:
    return x is not None and x == x and x not in (float("inf"), float("-inf"))


def vcenter_label(key: Optional[str]) -> str:
    """Display name for a vCenter key (configured name, falling back to the key)."""
    if not key:
        return IMPORT_LABEL
    try:
        from .config import get_settings
        for cfg in get_settings().vcenters():
            if cfg.key == key:
                return cfg.name or key
    except Exception:  # pragma: no cover - settings unavailable in odd contexts
        pass
    return key


def _group_key(row: ClusterAggregate, group_by: str, single_vcenter: bool = False,
               cluster: Optional[str] = None) -> Tuple[str, str]:
    """Group key/label. Cluster labels carry the vCenter name only when more
    than one vCenter is in scope (each cluster = one project team, so the
    cluster name alone is what management recognises). ``cluster`` lets the
    caller pass the alias-resolved (current) cluster name."""
    vc = row.vcenter_key or "import"
    vc_label = vcenter_label(row.vcenter_key)
    name = cluster if cluster is not None else row.cluster
    if group_by == "cluster":
        return f"{vc}::{name}", (name if single_vcenter else f"{name} ({vc_label})")
    return vc, vc_label


def _norm_clusters(clusters: Optional[List[str]]) -> Optional[List[str]]:
    if clusters is None:
        return None
    return [c.strip() for c in clusters if c and c.strip()]


def _pct(used: float, total: float, oc_max: float) -> Optional[float]:
    if total <= 0 or oc_max <= 0:
        return None
    return round(used / total / oc_max * 100.0, 1)


def get_resource_history(db: Session, vcenter_keys: Optional[List[str]], months: int,
                         group_by: str, policy: capacity.Policy,
                         include_import: bool = True,
                         clusters: Optional[List[str]] = None) -> Dict[str, Any]:
    """Daily CPU/RAM series per group (vCenter or cluster) plus an org-wide
    "Toàn bộ" series and a first-vs-last delta summary per group.

    For each (group, cluster, VN-calendar day) only the latest run of that day
    is kept so multiple polls per day do not double count."""
    months = normalize_months(months)
    group_by = "cluster" if group_by == "cluster" else "vcenter"
    since = window_start(months)
    q = db.query(ClusterAggregate).join(SnapshotRun, SnapshotRun.id == ClusterAggregate.run_id) \
        .filter(ClusterAggregate.captured_at >= since, SnapshotRun.status == "ok")
    if vcenter_keys is not None:
        q = q.filter(ClusterAggregate.vcenter_key.in_(vcenter_keys))
    elif not include_import:
        q = q.filter(ClusterAggregate.vcenter_key.isnot(None))
    clusters = _norm_clusters(clusters)
    rows = q.order_by(ClusterAggregate.captured_at.asc(), ClusterAggregate.run_id.asc()).all()
    # A renamed cluster keeps one continuous series under its current name.
    aliases = cluster_aliases_for_window(db, vcenter_keys, since, include_import)
    renamed = [{"vcenterKey": vc, "from": old, "to": new} for (vc, old), new in sorted(aliases.items())]

    def _cluster_name(r: ClusterAggregate) -> str:
        return aliases.get((r.vcenter_key or "import", r.cluster), r.cluster)

    if clusters is not None:
        # Cluster scope (project-team scope): only these clusters feed both the
        # per-group series and the "total" series (matched on current names).
        wanted = set(clusters)
        rows = [r for r in rows if _cluster_name(r) in wanted]
    single_vcenter = len({r.vcenter_key for r in rows}) <= 1

    # (group_key, day, vcenter, cluster) -> latest row that day
    latest: Dict[Tuple[str, str, str, str], ClusterAggregate] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        cl = _cluster_name(r)
        gkey, glabel = _group_key(r, group_by, single_vcenter, cl)
        labels[gkey] = glabel
        day = to_vn_date(r.captured_at).isoformat()
        latest[(gkey, day, r.vcenter_key or "import", cl)] = r

    # Aggregate per group/day and org-wide per day.
    per_group: Dict[str, Dict[str, Dict[str, float]]] = {}
    overall: Dict[str, Dict[str, float]] = {}

    def _bucket(store: Dict[str, Dict[str, float]], day: str) -> Dict[str, float]:
        return store.setdefault(day, {"usedVcpu": 0.0, "totalLogicalCores": 0.0, "usedRamGB": 0.0,
                                      "totalRamGB": 0.0, "hostCount": 0, "vmCount": 0})

    for (gkey, day, _vc, _cl), r in latest.items():
        for store in (per_group.setdefault(gkey, {}), overall):
            b = _bucket(store, day)
            b["usedVcpu"] += r.used_vcpu or 0
            b["totalLogicalCores"] += r.total_logical_cores or 0
            b["usedRamGB"] += r.used_ram_gb or 0
            b["totalRamGB"] += r.total_ram_gb or 0
            b["hostCount"] += r.host_count or 0
            b["vmCount"] += r.vm_count or 0

    cpu_oc, ram_oc = policy.cpu_overcommit_max, policy.ram_overcommit_max

    def _series(store: Dict[str, Dict[str, float]]) -> List[Dict[str, Any]]:
        out = []
        for day in sorted(store):
            b = store[day]
            out.append({
                "date": day,
                "usedVcpu": round(b["usedVcpu"], 1), "totalLogicalCores": round(b["totalLogicalCores"], 1),
                "usedRamGB": round(b["usedRamGB"], 1), "totalRamGB": round(b["totalRamGB"], 1),
                "hostCount": int(b["hostCount"]), "vmCount": int(b["vmCount"]),
                "cpuPct": _pct(b["usedVcpu"], b["totalLogicalCores"], cpu_oc),
                "ramPct": _pct(b["usedRamGB"], b["totalRamGB"], ram_oc),
            })
        return out

    def _summary(key: str, label: str, series: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not series:
            return {"key": key, "label": label, "points": 0}
        first, last = series[0], series[-1]

        def _delta(field: str) -> Optional[float]:
            a, b = first.get(field), last.get(field)
            if a is None or b is None:
                return None
            return round(b - a, 1)

        peak_cpu = max((p["cpuPct"] for p in series if p["cpuPct"] is not None), default=None)
        peak_ram = max((p["ramPct"] for p in series if p["ramPct"] is not None), default=None)
        return {
            "key": key, "label": label, "points": len(series),
            "firstDate": first["date"], "lastDate": last["date"],
            "first": first, "last": last,
            "delta": {f: _delta(f) for f in ("cpuPct", "ramPct", "usedVcpu", "usedRamGB",
                                             "totalLogicalCores", "totalRamGB", "hostCount", "vmCount")},
            "peakCpuPct": peak_cpu, "peakRamPct": peak_ram,
        }

    groups = []
    for gkey in sorted(per_group, key=lambda k: labels.get(k, k)):
        s = _series(per_group[gkey])
        groups.append({"key": gkey, "label": labels.get(gkey, gkey), "series": s,
                       "summary": _summary(gkey, labels.get(gkey, gkey), s)})
    overall_series = _series(overall)
    overall_label = "Tổng các cluster đã chọn" if clusters is not None else "Toàn bộ"
    return {
        "months": months, "groupBy": group_by, "clusters": clusters,
        "renamedClusters": renamed,
        "since": to_iso_utc(since), "until": to_iso_utc(dt.datetime.utcnow()),
        "available": len(overall_series) >= 2,
        "pointCount": len(overall_series),
        "overall": {"key": "all", "label": overall_label, "series": overall_series,
                    "summary": _summary("all", "Toàn bộ", overall_series)},
        "groups": groups,
    }


def _vm_row(v: VmSnapshot) -> Dict[str, Any]:
    return {
        "vcenterKey": v.vcenter_key or "import",
        "vcenterLabel": vcenter_label(v.vcenter_key),
        "cluster": v.cluster, "host": v.host, "vm": v.vm,
        "instanceUuid": v.instance_uuid,
        "powerState": v.power_state, "isPoweredOn": bool(v.is_powered_on),
        "vcpu": v.vcpu or 0, "ramGB": v.ram_gb or 0, "diskProvGB": v.disk_prov_gb or 0,
    }


# Minimum overlap (Jaccard) of host names / VM identities for a cluster or host
# that vanished and one that appeared to be treated as the same object renamed.
RENAME_MATCH_MIN = 0.5


def _ident(v: VmSnapshot) -> Tuple[str, str]:
    """Identity used to match one VM across snapshots: vCenter instanceUuid when
    captured (survives rename / vMotion / cross-cluster migration), otherwise
    the VM name (legacy snapshots and imports without a UUID column)."""
    return ("u", v.instance_uuid) if v.instance_uuid else ("n", v.vm)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _match_renamed(base_sets: Dict[str, set], cur_sets: Dict[str, set]) -> List[Tuple[str, str, float]]:
    """Greedy best-overlap pairing of names present only at baseline with names
    present only now. Returns (old, new, overlap) triples above the threshold."""
    gone = [k for k in base_sets if k not in cur_sets]
    fresh = [k for k in cur_sets if k not in base_sets]
    cands = sorted(((_jaccard(base_sets[o], cur_sets[n]), o, n) for o in gone for n in fresh
                    if _jaccard(base_sets[o], cur_sets[n]) >= RENAME_MATCH_MIN), reverse=True)
    used_o, used_n, out = set(), set(), []
    for score, o, n in cands:
        if o in used_o or n in used_n:
            continue
        used_o.add(o); used_n.add(n)
        out.append((o, n, round(score, 2)))
    return out


def detect_renames(first_run: SnapshotRun, last_run: SnapshotRun) -> Dict[str, Any]:
    """Cluster / host renames between two runs of the same vCenter.

    * Cluster rename: a cluster present only at baseline and one present only
      now that share >= 50 % of their ESXi host names (or, when the runs carry
      no host inventory, >= 50 % of their VM identities).
    * Host rename: inside one (aliased) cluster, a host present only at
      baseline and one present only now that share >= 50 % of VM identities.

    Renamed objects are aliased to their *current* name so that neither the
    VM appendix nor the CPU/RAM trend shows a phantom remove + add."""
    def _host_sets(run: SnapshotRun) -> Dict[str, set]:
        out: Dict[str, set] = {}
        for h in run.hosts:
            out.setdefault(h.cluster or "—", set()).add(h.host)
        return out

    def _vm_sets(run: SnapshotRun) -> Dict[str, set]:
        out: Dict[str, set] = {}
        for v in run.vms:
            out.setdefault(v.cluster or "—", set()).add(_ident(v))
        return out

    base_h, cur_h = _host_sets(first_run), _host_sets(last_run)
    cluster_pairs = _match_renamed(base_h, cur_h) if base_h and cur_h else []
    method = "host"
    if not cluster_pairs:
        cluster_pairs = _match_renamed(_vm_sets(first_run), _vm_sets(last_run))
        method = "vm"
    cluster_alias = {o: n for o, n, _s in cluster_pairs}
    clusters = [{"from": o, "to": n, "overlap": s, "matchedBy": method} for o, n, s in cluster_pairs]

    # Host renames per (aliased) cluster, by VM identity overlap.
    base_hv: Dict[str, Dict[str, set]] = {}
    cur_hv: Dict[str, Dict[str, set]] = {}
    for v in first_run.vms:
        cl = cluster_alias.get(v.cluster or "—", v.cluster or "—")
        base_hv.setdefault(cl, {}).setdefault(v.host or "—", set()).add(_ident(v))
    for v in last_run.vms:
        cur_hv.setdefault(v.cluster or "—", {}).setdefault(v.host or "—", set()).add(_ident(v))
    host_alias: Dict[Tuple[str, str], str] = {}
    hosts = []
    for cl in base_hv:
        if cl not in cur_hv:
            continue
        for o, n, sc in _match_renamed(base_hv[cl], cur_hv[cl]):
            host_alias[(cl, o)] = n
            hosts.append({"cluster": cl, "from": o, "to": n, "overlap": sc})
    return {"clusterAlias": cluster_alias, "hostAlias": host_alias, "clusters": clusters, "hosts": hosts}


def _window_runs(db: Session, key: Optional[str], since: dt.datetime) -> Tuple[Optional[SnapshotRun], Optional[SnapshotRun]]:
    q = db.query(SnapshotRun).filter(SnapshotRun.status == "ok", SnapshotRun.finished_at >= since)
    q = q.filter(SnapshotRun.vcenter_key.is_(None)) if key is None else q.filter(SnapshotRun.vcenter_key == key)
    first_run = q.order_by(SnapshotRun.finished_at.asc(), SnapshotRun.id.asc()).first()
    last_run = q.order_by(SnapshotRun.finished_at.desc(), SnapshotRun.id.desc()).first()
    return first_run, last_run


def cluster_aliases_for_window(db: Session, vcenter_keys: Optional[List[str]], since: dt.datetime,
                               include_import: bool = True) -> Dict[Tuple[str, str], str]:
    """(vcenter_key or 'import', old cluster) -> current cluster name for every
    cluster rename detected between the first and last run of the window."""
    if vcenter_keys is not None:
        scopes: List[Optional[str]] = list(vcenter_keys)
    else:
        scopes = [r[0] for r in db.query(SnapshotRun.vcenter_key)
                  .filter(SnapshotRun.vcenter_key.isnot(None)).distinct().all()]
        if include_import:
            scopes.append(None)
    out: Dict[Tuple[str, str], str] = {}
    for key in scopes:
        first_run, last_run = _window_runs(db, key, since)
        if not first_run or not last_run or first_run.id == last_run.id:
            continue
        for old, new in detect_renames(first_run, last_run)["clusterAlias"].items():
            out[(key or "import", old)] = new
    return out


def get_vm_changes(db: Session, vcenter_keys: Optional[List[str]], months: int,
                   include_import: bool = True,
                   clusters: Optional[List[str]] = None,
                   hosts: Optional[List[str]] = None) -> Dict[str, Any]:
    """VM inventory changes inside the window, compared per cluster (one
    project team) and optionally narrowed to a set of hosts.

    Baseline = earliest successful run inside the window; current = latest
    successful run (per vCenter). VMs are matched across the two runs by
    identity — vCenter ``instanceUuid`` when captured, VM name otherwise — so
    the following are recognised and **excluded** from "added / removed"
    (they do not create or free any resource):

    * host-to-host vMotion inside a cluster           -> ``moved`` (kind=host)
    * migration from cluster A to cluster B           -> ``moved`` (kind=cluster)
    * cluster rename / host rename (see detect_renames) -> ``renamedClusters`` / ``renamedHosts``
    * VM rename (same instanceUuid, new name; or, without UUIDs, same
      cluster + same IP + same vCPU/RAM)               -> ``renamedVms``

    Only a VM whose identity is absent from the whole vCenter at baseline is
    "added", and only one absent from the whole vCenter now is "removed".
    A matched VM whose vCPU/RAM changed is reported separately as ``resized``
    because that *does* change the resource footprint."""
    months = normalize_months(months)
    since = window_start(months)
    clusters = _norm_clusters(clusters)
    hosts = _norm_clusters(hosts)
    cluster_set = set(clusters) if clusters is not None else None
    host_set = set(hosts) if hosts is not None else None

    def _in_scope(cluster: Optional[str], host: Optional[str]) -> bool:
        if cluster_set is not None and cluster not in cluster_set:
            return False
        if host_set is not None and host not in host_set:
            return False
        return True

    if vcenter_keys is not None:
        scopes: List[Optional[str]] = list(vcenter_keys)
    else:
        scopes = [r[0] for r in db.query(SnapshotRun.vcenter_key)
                  .filter(SnapshotRun.vcenter_key.isnot(None)).distinct().all()]
        if include_import:
            scopes.append(None)

    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []
    renamed_vms: List[Dict[str, Any]] = []
    resized: List[Dict[str, Any]] = []
    renamed_clusters: List[Dict[str, Any]] = []
    renamed_hosts: List[Dict[str, Any]] = []
    per_scope: List[Dict[str, Any]] = []
    per_cluster: Dict[Tuple[str, str], Dict[str, Any]] = {}
    per_host: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def _host_bucket(vc_key: Optional[str], cluster: str, host: Optional[str]) -> Dict[str, Any]:
        k = (vc_key or "import", cluster or "—", host or "—")
        return per_host.setdefault(k, {
            "vcenterKey": k[0], "vcenterLabel": vcenter_label(vc_key), "cluster": k[1], "host": k[2],
            "baselineVmCount": 0, "currentVmCount": 0, "added": 0, "removed": 0, "movedIn": 0, "movedOut": 0,
            "addedVcpu": 0.0, "addedRamGB": 0.0, "removedVcpu": 0.0, "removedRamGB": 0.0,
        })

    def _cluster_bucket(vc_key: Optional[str], cluster: str) -> Dict[str, Any]:
        k = (vc_key or "import", cluster or "—")
        return per_cluster.setdefault(k, {
            "vcenterKey": k[0], "vcenterLabel": vcenter_label(vc_key), "cluster": k[1], "renamedFrom": None,
            "baselineVmCount": 0, "currentVmCount": 0,
            "added": 0, "removed": 0, "movedIn": 0, "movedOut": 0, "renamed": 0, "resized": 0,
            "addedVcpu": 0.0, "addedRamGB": 0.0, "removedVcpu": 0.0, "removedRamGB": 0.0,
            "resizedVcpuDelta": 0.0, "resizedRamDelta": 0.0,
        })

    for key in scopes:
        first_run, last_run = _window_runs(db, key, since)
        if not first_run or not last_run or first_run.id == last_run.id:
            per_scope.append({"vcenterKey": key or "import", "vcenterLabel": vcenter_label(key),
                              "available": False, "runs": 1 if first_run else 0})
            continue
        vc_id = key or "import"
        base_list: List[VmSnapshot] = list(first_run.vms)
        cur_list: List[VmSnapshot] = list(last_run.vms)

        # 1) Cluster / host renames -> alias baseline names to current names.
        ren = detect_renames(first_run, last_run)
        cl_alias, h_alias = ren["clusterAlias"], ren["hostAlias"]
        for r in ren["clusters"]:
            renamed_clusters.append(dict(r, vcenterKey=vc_id, vcenterLabel=vcenter_label(key)))
        for r in ren["hosts"]:
            renamed_hosts.append(dict(r, vcenterKey=vc_id, vcenterLabel=vcenter_label(key)))

        def _b_cluster(v: VmSnapshot) -> str:
            return cl_alias.get(v.cluster or "—", v.cluster or "—")

        def _b_host(v: VmSnapshot) -> Optional[str]:
            return h_alias.get((_b_cluster(v), v.host or "—"), v.host)

        # 2) Pair baseline VMs with current VMs by identity (whole vCenter, unfiltered).
        with_uuid = sum(1 for v in base_list + cur_list if v.instance_uuid)
        identity = "uuid" if with_uuid == len(base_list) + len(cur_list) and with_uuid else ("name" if not with_uuid else "mixed")
        cur_by_uuid = {c.instance_uuid: c for c in cur_list if c.instance_uuid}
        cur_by_name: Dict[str, List[VmSnapshot]] = {}
        for c in cur_list:
            cur_by_name.setdefault(c.vm, []).append(c)
        used: set = set()
        pairs: List[Tuple[VmSnapshot, VmSnapshot]] = []
        unmatched_b: List[VmSnapshot] = []
        for b in base_list:
            c = cur_by_uuid.get(b.instance_uuid) if b.instance_uuid else None
            if c is None:
                # Name fallback only when at least one side has no UUID: two different
                # UUIDs with the same name are a real delete + re-create.
                cands = [x for x in cur_by_name.get(b.vm, []) if id(x) not in used and not (x.instance_uuid and b.instance_uuid)]
                cands.sort(key=lambda x: 0 if (x.cluster or "—") == _b_cluster(b) else 1)
                c = cands[0] if cands else None
            if c is None or id(c) in used:
                unmatched_b.append(b)
            else:
                used.add(id(c))
                pairs.append((b, c))
        unmatched_c = [c for c in cur_list if id(c) not in used]

        # 3) Without UUIDs, a VM renamed in place looks like remove + add: pair the
        #    leftovers when cluster, IP, vCPU and RAM all match (IP must be set).
        if unmatched_b and unmatched_c:
            def _fp(v: VmSnapshot, cl: str) -> Optional[Tuple[str, str, float, float]]:
                ip = (v.ip_address or "").strip()
                return (cl, ip, float(v.vcpu or 0), float(v.ram_gb or 0)) if ip else None
            fp_cur: Dict[Tuple[str, str, float, float], List[VmSnapshot]] = {}
            for c in unmatched_c:
                if c.instance_uuid:
                    continue
                fp = _fp(c, c.cluster or "—")
                if fp:
                    fp_cur.setdefault(fp, []).append(c)
            still_b = []
            for b in unmatched_b:
                fp = None if b.instance_uuid else _fp(b, _b_cluster(b))
                lst = fp_cur.get(fp) if fp else None
                if lst:
                    c = lst.pop(0)
                    used.add(id(c))
                    pairs.append((b, c))
                    renamed_vms.append({
                        "vcenterKey": vc_id, "vcenterLabel": vcenter_label(key), "cluster": c.cluster, "host": c.host,
                        "fromName": b.vm, "toName": c.vm, "vcpu": c.vcpu or 0, "ramGB": c.ram_gb or 0,
                        "isPoweredOn": bool(c.is_powered_on), "powerState": c.power_state,
                        "matchedBy": "heuristic", "ipAddress": c.ip_address,
                        "inScope": _in_scope(c.cluster or "—", c.host),
                    })
                else:
                    still_b.append(b)
            unmatched_b = still_b
            unmatched_c = [c for c in cur_list if id(c) not in used]

        # 4) Baseline / current membership per (aliased) cluster & host inside the scope.
        for b in base_list:
            cl, ho = _b_cluster(b), _b_host(b)
            if _in_scope(cl, ho):
                _cluster_bucket(key, cl)["baselineVmCount"] += 1
                _host_bucket(key, cl, ho)["baselineVmCount"] += 1
        for c in cur_list:
            cl = c.cluster or "—"
            if _in_scope(cl, c.host):
                _cluster_bucket(key, cl)["currentVmCount"] += 1
                _host_bucket(key, cl, c.host)["currentVmCount"] += 1
        for r in ren["clusters"]:
            if _in_scope(r["to"], None) or host_set is None:
                _cluster_bucket(key, r["to"])["renamedFrom"] = r["from"]

        # 5) Classify matched pairs: cluster move / host move / rename / resize.
        for b, c in pairs:
            b_cl, b_ho = _b_cluster(b), _b_host(b)
            c_cl, c_ho = c.cluster or "—", c.host
            row_base = {
                "vcenterKey": vc_id, "vcenterLabel": vcenter_label(key), "vm": c.vm, "instanceUuid": c.instance_uuid,
                "vcpu": c.vcpu or 0, "ramGB": c.ram_gb or 0, "isPoweredOn": bool(c.is_powered_on), "powerState": c.power_state,
            }
            if b_cl != c_cl:
                if _in_scope(b_cl, b_ho) or _in_scope(c_cl, c_ho):
                    moved.append(dict(row_base, kind="cluster", cluster=c_cl, host=c_ho,
                                      fromCluster=b_cl, toCluster=c_cl, fromHost=b_ho, toHost=c_ho))
                    if _in_scope(c_cl, c_ho):
                        _cluster_bucket(key, c_cl)["movedIn"] += 1
                        _host_bucket(key, c_cl, c_ho)["movedIn"] += 1
                    if _in_scope(b_cl, b_ho):
                        _cluster_bucket(key, b_cl)["movedOut"] += 1
                        _host_bucket(key, b_cl, b_ho)["movedOut"] += 1
            elif (b_ho or "—") != (c_ho or "—"):
                # vMotion inside the cluster: invisible at cluster level, listed only
                # when the report is narrowed by host (where it changes membership).
                if host_set is not None and (_in_scope(b_cl, b_ho) or _in_scope(c_cl, c_ho)):
                    moved.append(dict(row_base, kind="host", cluster=c_cl, host=c_ho,
                                      fromCluster=b_cl, toCluster=c_cl, fromHost=b_ho, toHost=c_ho))
                    if _in_scope(c_cl, c_ho):
                        _host_bucket(key, c_cl, c_ho)["movedIn"] += 1
                    if _in_scope(b_cl, b_ho):
                        _host_bucket(key, b_cl, b_ho)["movedOut"] += 1
            if b.vm != c.vm and _in_scope(c_cl, c_ho):
                renamed_vms.append(dict(row_base, cluster=c_cl, host=c_ho, fromName=b.vm, toName=c.vm,
                                        matchedBy="uuid", ipAddress=c.ip_address, inScope=True))
                _cluster_bucket(key, c_cl)["renamed"] += 1
            d_vcpu = float(c.vcpu or 0) - float(b.vcpu or 0)
            d_ram = float(c.ram_gb or 0) - float(b.ram_gb or 0)
            if (abs(d_vcpu) >= 0.5 or abs(d_ram) >= 0.5) and _in_scope(c_cl, c_ho):
                resized.append(dict(row_base, cluster=c_cl, host=c_ho,
                                    vcpuBefore=b.vcpu or 0, vcpuAfter=c.vcpu or 0, ramBefore=b.ram_gb or 0, ramAfter=c.ram_gb or 0,
                                    deltaVcpu=round(d_vcpu, 1), deltaRamGB=round(d_ram, 1)))
                cb = _cluster_bucket(key, c_cl)
                cb["resized"] += 1; cb["resizedVcpuDelta"] += d_vcpu; cb["resizedRamDelta"] += d_ram
        for r in renamed_vms:
            if r["vcenterKey"] == vc_id and r["matchedBy"] == "heuristic" and r["inScope"]:
                _cluster_bucket(key, r["cluster"] or "—")["renamed"] += 1

        # 6) Truly added / removed (identity absent from the whole vCenter on the other side).
        seen_name_q = db.query(VmSnapshot.vm, func.min(VmSnapshot.captured_at), func.max(VmSnapshot.captured_at)) \
            .filter(VmSnapshot.captured_at >= since)
        seen_name_q = seen_name_q.filter(VmSnapshot.vcenter_key.is_(None)) if key is None else seen_name_q.filter(VmSnapshot.vcenter_key == key)
        seen_by_name = {n: (mn, mx) for n, mn, mx in seen_name_q.group_by(VmSnapshot.vm).all()}
        seen_uuid_q = db.query(VmSnapshot.instance_uuid, func.min(VmSnapshot.captured_at), func.max(VmSnapshot.captured_at)) \
            .filter(VmSnapshot.captured_at >= since, VmSnapshot.instance_uuid.isnot(None))
        seen_uuid_q = seen_uuid_q.filter(VmSnapshot.vcenter_key.is_(None)) if key is None else seen_uuid_q.filter(VmSnapshot.vcenter_key == key)
        seen_by_uuid = {u: (mn, mx) for u, mn, mx in seen_uuid_q.group_by(VmSnapshot.instance_uuid).all()}

        def _seen(v: VmSnapshot) -> Tuple[Any, Any]:
            if v.instance_uuid and v.instance_uuid in seen_by_uuid:
                return seen_by_uuid[v.instance_uuid]
            return seen_by_name.get(v.vm, (v.captured_at, v.captured_at))

        for c in sorted(unmatched_c, key=lambda x: (x.cluster or "—", x.vm)):
            cl = c.cluster or "—"
            if not _in_scope(cl, c.host):
                continue
            row = _vm_row(c)
            row["firstSeen"] = to_iso_utc(_seen(c)[0])
            added.append(row)
            b_ = _cluster_bucket(key, cl)
            b_["added"] += 1; b_["addedVcpu"] += row["vcpu"]; b_["addedRamGB"] += row["ramGB"]
            hb = _host_bucket(key, cl, c.host)
            hb["added"] += 1; hb["addedVcpu"] += row["vcpu"]; hb["addedRamGB"] += row["ramGB"]
        for b in sorted(unmatched_b, key=lambda x: (_b_cluster(x), x.vm)):
            cl, ho = _b_cluster(b), _b_host(b)
            if not _in_scope(cl, ho):
                continue
            row = _vm_row(b)
            row["cluster"], row["host"] = cl, ho  # report under the current (aliased) names
            row["lastSeen"] = to_iso_utc(_seen(b)[1])
            removed.append(row)
            b_ = _cluster_bucket(key, cl)
            b_["removed"] += 1; b_["removedVcpu"] += row["vcpu"]; b_["removedRamGB"] += row["ramGB"]
            hb = _host_bucket(key, cl, ho)
            hb["removed"] += 1; hb["removedVcpu"] += row["vcpu"]; hb["removedRamGB"] += row["ramGB"]

        per_scope.append({
            "vcenterKey": vc_id, "vcenterLabel": vcenter_label(key), "available": True,
            "identity": identity,
            "baselineAt": to_iso_utc(first_run.finished_at), "currentAt": to_iso_utc(last_run.finished_at),
            "baselineVmCount": sum(1 for b in base_list if _in_scope(_b_cluster(b), _b_host(b))),
            "currentVmCount": sum(1 for c in cur_list if _in_scope(c.cluster or "—", c.host)),
            "added": sum(1 for r in added if r["vcenterKey"] == vc_id),
            "removed": sum(1 for r in removed if r["vcenterKey"] == vc_id),
            "moved": sum(1 for r in moved if r["vcenterKey"] == vc_id),
            "renamedVms": sum(1 for r in renamed_vms if r["vcenterKey"] == vc_id and r["inScope"]),
            "resized": sum(1 for r in resized if r["vcenterKey"] == vc_id),
        })

    renamed_vms = [r for r in renamed_vms if r.get("inScope")]
    for r in renamed_vms:
        r.pop("inScope", None)

    # Cluster first, then VM name — the reader scans by project team.
    def _sort(rows: List[Dict[str, Any]]) -> None:
        rows.sort(key=lambda r: ((r.get("cluster") or "—"), r["vcenterLabel"], r.get("vm") or r.get("toName") or ""))
    for lst in (added, removed, moved, renamed_vms, resized):
        _sort(lst)
    cluster_rows = sorted(per_cluster.values(), key=lambda b: (b["cluster"], b["vcenterLabel"]))
    host_rows = sorted(per_host.values(), key=lambda b: (b["cluster"], b["host"], b["vcenterLabel"]))
    for b in cluster_rows + host_rows:
        for f in ("addedVcpu", "addedRamGB", "removedVcpu", "removedRamGB", "resizedVcpuDelta", "resizedRamDelta"):
            if f in b:
                b[f] = round(b[f], 0)
        b["netVm"] = b["currentVmCount"] - b["baselineVmCount"]

    def _totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        return {"count": len(rows), "vcpu": round(sum(r["vcpu"] for r in rows), 0),
                "ramGB": round(sum(r["ramGB"] for r in rows), 0),
                "poweredOn": sum(1 for r in rows if r["isPoweredOn"])}

    moved_totals = dict(_totals(moved), cluster=sum(1 for r in moved if r["kind"] == "cluster"),
                        host=sum(1 for r in moved if r["kind"] == "host"))
    resized_totals = {
        "count": len(resized),
        "deltaVcpu": round(sum(r["deltaVcpu"] for r in resized), 0),
        "deltaRamGB": round(sum(r["deltaRamGB"] for r in resized), 0),
        "up": sum(1 for r in resized if r["deltaVcpu"] > 0 or r["deltaRamGB"] > 0),
        "down": sum(1 for r in resized if r["deltaVcpu"] < 0 or r["deltaRamGB"] < 0),
    }
    excluded = len(moved) + len(renamed_vms) + len(renamed_clusters) + len(renamed_hosts)
    return {
        "months": months, "since": to_iso_utc(since), "clusters": clusters, "hosts": hosts,
        "available": any(s.get("available") for s in per_scope),
        "identity": ("uuid" if all(s.get("identity") == "uuid" for s in per_scope if s.get("available"))
                     else ("name" if all(s.get("identity") == "name" for s in per_scope if s.get("available")) else "mixed")),
        "scopes": per_scope, "clusterSummary": cluster_rows,
        # Only meaningful (and only rendered) when the scope is narrowed by host.
        "hostSummary": host_rows if hosts is not None else [],
        "added": added, "removed": removed,
        "moved": moved, "renamedVms": renamed_vms, "resized": resized,
        "renamedClusters": renamed_clusters, "renamedHosts": renamed_hosts,
        "addedTotals": _totals(added), "removedTotals": _totals(removed),
        "movedTotals": moved_totals, "resizedTotals": resized_totals,
        "renamedTotals": {"vms": len(renamed_vms), "clusters": len(renamed_clusters), "hosts": len(renamed_hosts)},
        # Rows that the naive name-per-cluster comparison would have reported as add/remove.
        "excludedFalsePositives": excluded,
    }


def downsample(series: List[Dict[str, Any]], max_points: int = 60) -> List[Dict[str, Any]]:
    """Thin a daily series for compact PDF charts, always keeping the last point."""
    if len(series) <= max_points:
        return series
    step = len(series) / float(max_points)
    picked = [series[int(i * step)] for i in range(max_points)]
    if picked[-1] is not series[-1]:
        picked.append(series[-1])
    return picked
