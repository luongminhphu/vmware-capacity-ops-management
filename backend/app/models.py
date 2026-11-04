import datetime as dt

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow():
    return dt.datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class SnapshotRun(Base):
    """One collection event: either a live vCenter query or a manual file import."""
    __tablename__ = "snapshot_runs"

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=True, index=True)  # null when source == 'import'
    source = Column(String(16), nullable=False, default="live")  # 'live' | 'import'
    status = Column(String(16), nullable=False, default="ok")  # 'ok' | 'error'
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)

    hosts = relationship("HostSnapshot", back_populates="run", cascade="all, delete-orphan")
    vms = relationship("VmSnapshot", back_populates="run", cascade="all, delete-orphan")
    clusters = relationship("ClusterAggregate", back_populates="run", cascade="all, delete-orphan")
    findings = relationship("ComplianceFindingSnapshot", back_populates="run", cascade="all, delete-orphan")


class HostSnapshot(Base):
    __tablename__ = "host_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    cluster = Column(String(128), nullable=False, default="UNKNOWN")
    host = Column(String(255), nullable=False)
    cores = Column(Float, default=0)
    cpu_mhz = Column(Float, default=0)
    ram_gb = Column(Float, default=0)
    cpu_usage_pct_raw = Column(Float, nullable=True)
    ram_usage_pct_raw = Column(Float, nullable=True)
    status = Column(String(64), default="Connected")
    # --- Inventory & License Assessment (hardware facts, read-only from vCenter) ---
    vendor = Column(String(128), nullable=True)
    model = Column(String(128), nullable=True)
    esxi_version = Column(String(64), nullable=True)
    esxi_build = Column(String(64), nullable=True)
    cpu_model = Column(String(255), nullable=True)
    cpu_sockets = Column(Integer, nullable=True)      # physical processors (numCpuPkgs)
    cpu_threads = Column(Integer, nullable=True)      # logical threads (numCpuThreads) — NOT licensable cores
    cluster_ha = Column(Boolean, nullable=True)       # vSphere HA enabled on the parent cluster
    cluster_drs = Column(Boolean, nullable=True)      # DRS enabled on the parent cluster
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="hosts")


class VmSnapshot(Base):
    __tablename__ = "vm_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    cluster = Column(String(128), nullable=False, default="UNKNOWN")
    host = Column(String(255), nullable=True)
    vm = Column(String(255), nullable=False)
    # vCenter instanceUuid: stable across rename / vMotion / cross-cluster
    # migration, so history can tell a moved VM from a deleted + created one.
    instance_uuid = Column(String(64), nullable=True, index=True)
    power_state = Column(String(32), default="Powered On")
    is_powered_on = Column(Boolean, default=True)
    ip_address = Column(String(64), nullable=True)
    vcpu = Column(Float, default=0)
    ram_gb = Column(Float, default=0)
    disk_prov_gb = Column(Float, default=0)
    disk_used_gb = Column(Float, default=0)
    datastore = Column(String(255), default="UNKNOWN")
    cpu_usage_pct_raw = Column(Float, nullable=True)
    ram_usage_pct_raw = Column(Float, nullable=True)
    last_activity_days = Column(Float, nullable=True)
    power_off_days = Column(Float, nullable=True)
    # --- Guest OS evidence for OS classification (kept raw; classification is derived) ---
    guest_id = Column(String(64), nullable=True)            # config.guestId (configured OS type)
    guest_full_name = Column(String(255), nullable=True)    # config.guestFullName (configured)
    tools_guest_full_name = Column(String(255), nullable=True)  # guest.guestFullName (reported by VMware Tools)
    tools_guest_family = Column(String(64), nullable=True)  # guest.guestFamily (windowsGuest / linuxGuest / otherGuestFamily)
    tools_status = Column(String(32), nullable=True)        # guest.toolsRunningStatus
    guest_hostname = Column(String(255), nullable=True)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="vms")


class ClusterAggregate(Base):
    """Precomputed per-cluster capacity model for fast trend queries (Executive view)."""
    __tablename__ = "cluster_aggregates"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    cluster = Column(String(128), nullable=False)
    host_count = Column(Integer, default=0)
    vm_count = Column(Integer, default=0)
    total_cores = Column(Float, default=0)
    total_logical_cores = Column(Float, default=0)
    total_ram_gb = Column(Float, default=0)
    used_vcpu = Column(Float, default=0)
    used_ram_gb = Column(Float, default=0)
    prov_storage_gb = Column(Float, default=0)
    used_storage_gb = Column(Float, default=0)
    cpu_overcommit = Column(Float, nullable=True)
    ram_overcommit = Column(Float, nullable=True)
    cpu_pct = Column(Float, nullable=True)
    ram_pct = Column(Float, nullable=True)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="clusters")


class ComplianceFindingSnapshot(Base):
    __tablename__ = "compliance_findings"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    severity = Column(String(16), nullable=False)  # critical | warning | info
    type = Column(String(32), nullable=False)
    type_label = Column(String(128), nullable=True)
    entity = Column(String(255), nullable=True)
    cluster = Column(String(128), nullable=True)
    metric = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="findings")


class AppSetting(Base):
    """Simple key/value store for adjustable compliance thresholds."""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)


class ReportLogo(Base):
    """Singleton row (id=1) holding the company logo used on PDF report covers.

    Stored as base64 text in Postgres (not on local disk) so it survives pod
    restarts/rescheduling on KubeSphere without needing a persistent volume.
    """
    __tablename__ = "report_logo"

    id = Column(Integer, primary_key=True, default=1)
    filename = Column(String(255), nullable=True)
    mime_type = Column(String(64), nullable=False)
    data_base64 = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=utcnow)


# =====================================================================
# Inventory & License Assessment module
# =====================================================================

class VmOsOverride(Base):
    """Manual OS classification override for one VM. Keyed by vCenter +
    instanceUuid when available (stable across rename/vMotion), falling back to
    vCenter + VM name for imported data without UUIDs."""
    __tablename__ = "vm_os_overrides"

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    instance_uuid = Column(String(64), nullable=True, index=True)
    vm = Column(String(255), nullable=False, index=True)
    category = Column(String(32), nullable=False)          # windows_server | windows_client | linux | appliance | other_unknown
    os_version = Column(String(128), nullable=True)
    os_edition = Column(String(64), nullable=True)          # e.g. Standard / Datacenter (Windows Server)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class LicenseRuleSet(Base):
    """Versioned, auditable licensing rule set. `params` is JSON text; the
    engine never hardcodes policy values — it reads them from the active row.
    Seeded defaults carry their public source URL and start as
    status='needs_verification' until a user records who verified them and when."""
    __tablename__ = "license_rule_sets"

    id = Column(Integer, primary_key=True)
    product = Column(String(32), nullable=False, index=True)    # windows_server | vmware
    key = Column(String(64), nullable=False, index=True)        # e.g. ws_core_2022, vmware_core_2024
    version = Column(Integer, nullable=False, default=1)
    name = Column(String(255), nullable=False)
    params_json = Column(Text, nullable=False, default="{}")
    source_url = Column(Text, nullable=True)
    source_note = Column(Text, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    verified_by = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="needs_verification")  # needs_verification | verified | retired
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    created_by = Column(String(64), nullable=True)


class LicenseEntitlement(Base):
    """Licenses the organisation already owns (or quotes being evaluated)."""
    __tablename__ = "license_entitlements"

    id = Column(Integer, primary_key=True)
    product = Column(String(32), nullable=False, index=True)    # ws_std | ws_dc | vsphere_std | vsphere_ent_plus | vvf | vcf | vmware_cpu_legacy
    metric = Column(String(32), nullable=False, default="core")  # core | pack2 | pack16 | cpu
    quantity = Column(Float, nullable=False, default=0)
    unit_price = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="VND")
    has_sa = Column(Boolean, nullable=False, default=False)      # Software Assurance / subscription (license mobility)
    scope_vcenter = Column(String(64), nullable=True)            # null = all vCenters
    scope_cluster = Column(String(128), nullable=True)           # null = all clusters in scope
    valid_to = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class LicenseAssessment(Base):
    """Immutable assessment snapshot: every input needed to re-run the
    computation (inventory rows, overrides, rule-set versions + params,
    entitlements) plus the result, so a reviewer can reproduce it later."""
    __tablename__ = "license_assessments"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    created_by = Column(String(64), nullable=True)
    title = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    scope_json = Column(Text, nullable=False, default="{}")
    inputs_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    result_hash = Column(String(64), nullable=True)
