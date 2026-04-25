"""Config loader for strategy layer — single entry point for YAML access."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "config"))


def load_yaml(name: str) -> dict[str, Any]:
    """Load config/<name>.yaml. Returns {} if file is missing."""
    path = _CONFIG_DIR / name
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping, got {type(data).__name__}")
    return data


@lru_cache(maxsize=8)
def _cached(name: str) -> dict[str, Any]:
    return load_yaml(name)


def validator_config() -> dict[str, Any]:
    return _cached("validator.yaml")


def sizer_config(gate: str = "gate1") -> dict[str, Any]:
    return _cached(f"sizer.{gate}.yaml")


def risk_gate_config() -> dict[str, Any]:
    return _cached("risk_gate.yaml")


def main_config() -> dict[str, Any]:
    return _cached("main.yaml")


def clear_cache() -> None:
    """Drop cached configs — used in tests when swapping CONFIG_DIR."""
    _cached.cache_clear()
