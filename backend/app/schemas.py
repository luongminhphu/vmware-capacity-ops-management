from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    """Serialized with camelCase aliases (displayName) to match every other
    API response in this app — the frontend reads `user.displayName`."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: str
    display_name: Optional[str] = None


class ImportPayload(BaseModel):
    """Client already parsed & normalized the CSV/JSON on the frontend (reusing the
    existing normalizeAndValidate logic) — the backend just persists it as a
    SnapshotRun so imported data also feeds the historical trend charts."""
    hosts: list = []
    vms: list = []
    label: Optional[str] = "Import thủ công"


class SettingsUpdate(BaseModel):
    cpu_overcommit_max: Optional[float] = None
    ram_overcommit_max: Optional[float] = None
    ht_factor: Optional[float] = None
    idle_days: Optional[int] = None
