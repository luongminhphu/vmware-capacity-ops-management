"""
Central configuration for VMware Capacity Ops backend.

All secrets (vCenter passwords, JWT secret, admin bootstrap password) come from
environment variables / .env — never hardcoded and never committed to git.
"""
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings parses `.env` into typed Settings fields only — it does NOT
# inject the file into os.environ. The vCenter block below reads VCENTER_i_*
# directly from os.environ (they aren't declared as typed Settings fields, since
# the count is dynamic), so load .env into the real process environment too.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class VCenterConfig:
    def __init__(self, key: str, name: str, host: str, port: int, username: str,
                 password: str, verify_ssl: bool, demo: bool = False):
        self.key = key
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.demo = demo


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "VMware Capacity Ops Management"
    environment: str = Field(default="production")

    database_url: str = Field(default="postgresql+psycopg2://vmware_app:devpassword@localhost:5432/vmware_capacity_ops")

    jwt_secret: str = Field(default="change-me-in-.env-please")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12  # 12h session

    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="")  # required on first boot if no users exist

    cookie_secure: bool = Field(default=False)  # set True when served over HTTPS

    poll_interval_minutes: int = Field(default=60)
    enable_scheduler: bool = Field(default=True)

    # Compliance / overcommit default thresholds (mirrors the original client-side policy)
    cpu_overcommit_max: float = 6.0
    ram_overcommit_max: float = 1.5
    ht_factor: float = 2.0
    idle_days: int = 30

    # Up to 4 vCenters configured via env vars VCENTER_1_* .. VCENTER_4_*
    vcenter_count: int = Field(default=4)

    cors_origins: str = Field(default="*")

    def cors_origin_list(self) -> List[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def vcenters(self) -> List[VCenterConfig]:
        result = []
        for i in range(1, self.vcenter_count + 1):
            prefix = f"VCENTER_{i}_"
            host = os.environ.get(prefix + "HOST")
            if not host:
                continue
            demo = os.environ.get(prefix + "DEMO", "false").lower() in ("1", "true", "yes")
            result.append(VCenterConfig(
                key=os.environ.get(prefix + "KEY", f"vc{i}"),
                name=os.environ.get(prefix + "NAME", host),
                host=host,
                port=int(os.environ.get(prefix + "PORT", "443")),
                username=os.environ.get(prefix + "USERNAME", "infra.mon@vsphere.local"),
                password=os.environ.get(prefix + "PASSWORD", ""),
                verify_ssl=os.environ.get(prefix + "VERIFY_SSL", "false").lower() in ("1", "true", "yes"),
                demo=demo,
            ))
        return result


@lru_cache()
def get_settings() -> Settings:
    return Settings()
