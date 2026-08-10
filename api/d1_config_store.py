"""D1-backed non-secret application configuration."""

from __future__ import annotations

import os
from typing import Any

from core.worker_bridge import WorkerBridgeClient

from .config_store import ConfigStore, SETTING_SPECS, _SPEC_BY_KEY


class D1ConfigStore:
    path = "d1://AUTOREACH_DB"

    def __init__(self, bridge_url: str | None = None) -> None:
        self._bridge = WorkerBridgeClient(bridge_url)
        self._configured = False
        self._values: dict[str, str] = {}
        self._reload()

    @property
    def configured(self) -> bool:
        return self._configured

    def initialize(self, values: dict[str, Any] | None = None) -> None:
        if self.configured:
            raise ValueError("AutoReach has already been configured")
        normalized = self._normalize_values(values or {})
        result = self._bridge.call("config-state", "initialize", {"values": normalized})
        if not result.get("initialized"):
            raise ValueError("AutoReach has already been configured")
        self._reload()

    def update(self, values: dict[str, Any]) -> None:
        normalized = self._normalize_values(values)
        if normalized:
            self._bridge.call("config-state", "update", {"values": normalized})
            self._reload()

    def get(self, key: str) -> str:
        spec = _SPEC_BY_KEY[key]
        return self._values.get(key, spec.default)

    def get_bool(self, key: str) -> bool:
        return self.get(key) == "true"

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def public_settings(self) -> dict[str, Any]:
        items = []
        for spec in SETTING_SPECS:
            value = self.get(spec.key)
            items.append({
                "key": spec.key, "label": spec.label, "group": spec.group, "kind": spec.kind,
                "value": "" if spec.kind == "secret" else ConfigStore._public_value(spec, value),
                "configured": bool(os.getenv(spec.env_name or "")) if spec.kind == "secret" else True,
                "help": spec.help, "minimum": spec.minimum, "options": list(spec.options),
            })
        return {"database_path": self.path, "items": items}

    def apply_to_process(self, *, preserve_secret_environment: bool = False) -> None:
        for spec in SETTING_SPECS:
            if not spec.env_name or spec.kind == "secret":
                continue
            os.environ[spec.env_name] = self.get(spec.key)
        os.environ["AGENT4_SIMULATE"] = self.get("simulate")
        os.environ["AGENT5_SIMULATE"] = self.get("simulate")
        os.environ["AGENT5_ENABLED"] = self.get("reply_handling_enabled")

    def close(self) -> None:
        return None

    def _reload(self) -> None:
        value = self._bridge.call("config-state", "load", {})
        self._configured = bool(value.get("configured"))
        self._values = {
            str(key): str(item) for key, item in dict(value.get("values", {})).items()
        }

    @staticmethod
    def _normalize_values(values: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in values.items():
            spec = _SPEC_BY_KEY.get(key)
            if spec is None:
                raise ValueError(f"Unknown setting: {key}")
            if spec.kind == "secret":
                if value is not None and str(value) != "":
                    raise ValueError(f"Configure {spec.label} through Worker Secrets")
                continue
            normalized[key] = ConfigStore._normalize(spec, value)
        return normalized
