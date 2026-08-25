from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AcquisitionSource(BaseModel):
    """Configuration for a plant data source.

    V1.1 intentionally allows read-only sources only. Any configuration that
    requests write capability is rejected at model validation time.
    """

    name: str
    adapter_type: Literal["watched_folder", "historian", "database", "opcua", "api"]
    mapping_profile: str
    read_only: bool = True
    enabled: bool = True
    poll_seconds: int = Field(default=60, ge=5, le=86400)
    config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_read_only(self) -> "AcquisitionSource":
        if self.read_only is not True:
            raise ValueError("CIP Intelligence acquisition sources must be read-only.")
        if self.adapter_type == "watched_folder":
            folder = str(self.config.get("folder", "")).strip()
            if not folder:
                raise ValueError("watched_folder source requires config.folder")
            patterns = self.config.get("patterns", ["*.csv"])
            if not isinstance(patterns, list) or not patterns:
                raise ValueError("watched_folder config.patterns must be a non-empty list")
            settle_seconds = int(self.config.get("settle_seconds", 10))
            if settle_seconds < 0:
                raise ValueError("settle_seconds must be >= 0")
        return self


class AcquisitionCandidate(BaseModel):
    source_name: str
    source_ref: str
    filename: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = Field(default_factory=dict)


class AcquisitionJob(BaseModel):
    id: str
    source_name: str
    source_ref: str
    filename: str
    mapping_profile: str
    status: Literal["DISCOVERED", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"] = "DISCOVERED"
    attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ingestion_id: str | None = None
    duplicate: bool | None = None
    error: str | None = None
    metadata: dict = Field(default_factory=dict)
