"""
Read-only vCenter inventory collector using pyVmomi (vSphere Web Services SDK —
supported against vCenter 6.7/7.x/8.x). Only ever issues property-collector
READS through the ContainerView API; never calls any mutating method, matching
the read-only `infra.mon@vsphere.local` service account this app is designed
for.

Output rows use the SAME camelCase field names as the frontend's
appState.hosts / appState.vms so they can be persisted and served back to the
UI with zero translation layer (see app/capacity.py docstring).
"""
import atexit
import ssl
from typing import Any, Dict, List, Tuple

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim

from ..config import VCenterConfig


class VCenterConnectionError(RuntimeError):
    pass


def _connect(cfg: VCenterConfig):
    context = None
    if not cfg.verify_ssl:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        si = SmartConnect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.username,
            pwd=cfg.password,
            sslContext=context,
        )
    except Exception as exc:  # noqa: BLE001 - surface a friendly, actionable error
        raise VCenterConnectionError(
            f"Không kết nối được vCenter '{cfg.name}' ({cfg.host}): {exc}"
        ) from exc
    atexit.register(Disconnect, si)
    return si


def _get_all_objs(content, vim_type):
    container = content.viewManager.CreateContainerView(content.rootFolder, [vim_type], True)
    try:
        return list(container.view)
    finally:
        container.Destroy()


def _mb_to_gb(mb: float) -> float:
    return (mb or 0) / 1024.0


def _cluster_name_of(host) -> str:
    parent = host.parent
    if isinstance(parent, vim.ClusterComputeResource):
        return parent.name
    return parent.name if parent is not None else "UNKNOWN"


def _datastore_name(vm) -> str:
    try:
        ds = vm.datastore
        if ds and len(ds) > 0:
            return ds[0].name
    except Exception:  # noqa: BLE001
        pass
    return "UNKNOWN"


def collect_inventory(cfg: VCenterConfig) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Connect to one vCenter and return (host_rows, vm_rows) in the app's
    normalized row shape. Raises VCenterConnectionError on failure."""
    si = _connect(cfg)
    try:
        content = si.RetrieveContent()
        host_systems = _get_all_objs(content, vim.HostSystem)
        virtual_machines = _get_all_objs(content, vim.VirtualMachine)

        host_rows: List[Dict[str, Any]] = []
        for h in host_systems:
            summary = h.summary
            hw = summary.hardware
            qs = summary.quickStats
            cores = hw.numCpuCores if hw else 0
            cpu_mhz_per_core = hw.cpuMhz if hw else 0
            ram_gb = _mb_to_gb(hw.memorySize / (1024 * 1024)) if hw and hw.memorySize else 0
            # quickStats gives instantaneous MHz/MB usage — used as a live proxy for
            # CPU/RAM usage percentage; long-running trend accuracy improves as the
            # scheduler accumulates history in Postgres.
            total_mhz = (cores or 0) * (cpu_mhz_per_core or 0)
            cpu_usage_pct = (qs.overallCpuUsage / total_mhz * 100) if qs and total_mhz else None
            ram_usage_pct = (qs.overallMemoryUsage / (ram_gb * 1024) * 100) if qs and ram_gb else None
            host_rows.append({
                "cluster": _cluster_name_of(h),
                "host": h.name,
                "cores": float(cores or 0),
                "cpuMhz": float(cpu_mhz_per_core or 0),
                "ramGB": float(ram_gb or 0),
                "cpuUsagePctRaw": cpu_usage_pct,
                "ramUsagePctRaw": ram_usage_pct,
                "status": str(summary.runtime.connectionState) if summary.runtime else "connected",
            })

        vm_rows: List[Dict[str, Any]] = []
        for vm in virtual_machines:
            summary = vm.summary
            cfgd = summary.config
            qs = summary.quickStats
            storage = summary.storage
            runtime = summary.runtime
            is_on = runtime.powerState == vim.VirtualMachinePowerState.poweredOn
            vcpu = cfgd.numCpu if cfgd else 0
            ram_gb = _mb_to_gb(cfgd.memorySizeMB) if cfgd else 0
            committed_gb = _mb_to_gb((storage.committed or 0) / (1024 * 1024)) if storage else 0
            uncommitted_gb = _mb_to_gb((storage.uncommitted or 0) / (1024 * 1024)) if storage else 0
            cpu_usage_pct = (qs.overallCpuUsage / (vcpu * 2000) * 100) if qs and vcpu else None  # rough MHz/core assumption fallback
            ram_usage_pct = (qs.hostMemoryUsage / (ram_gb * 1024) * 100) if qs and ram_gb else None
            host_name = None
            try:
                host_name = runtime.host.name if runtime.host else None
            except Exception:  # noqa: BLE001
                host_name = None
            vm_rows.append({
                "cluster": _cluster_name_of(runtime.host) if runtime.host else "UNKNOWN",
                "host": host_name,
                "vm": summary.config.name if cfgd else vm.name,
                "powerState": "Powered On" if is_on else "Powered Off",
                "isPoweredOn": is_on,
                "ipAddress": summary.guest.ipAddress if summary.guest else None,
                "vcpu": float(vcpu or 0),
                "ramGB": float(ram_gb or 0),
                "diskProvGB": float(committed_gb + uncommitted_gb),
                "diskUsedGB": float(committed_gb),
                "datastore": _datastore_name(vm),
                "cpuUsagePctRaw": cpu_usage_pct,
                "ramUsagePctRaw": ram_usage_pct,
                # lastActivityDays / powerOffDays are NOT derivable from a single
                # vCenter snapshot — the scheduler fills these in from our own
                # polling history (see collectors/scheduler.py::_enrich_activity).
                "lastActivityDays": None,
                "powerOffDays": None,
            })

        return host_rows, vm_rows
    finally:
        try:
            Disconnect(si)
        except Exception:  # noqa: BLE001
            pass
