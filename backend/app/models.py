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
