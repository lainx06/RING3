"""Loads config/models.toml and config/budget.toml. Never reads keys (those are env-only, models/ only)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODELS_TOML = REPO / "config" / "models.toml"
BUDGET_TOML = REPO / "config" / "budget.toml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    input_uusd_per_mtok: int
    output_uusd_per_mtok: int
    source: str
    checked: str


@dataclass(frozen=True)
class ModelConfig:
    id: str
    endpoint: str
    max_output_tokens: int
    timeout_s: int
    price: Price | None  # None = unset / [UNVERIFIED]: the ledger refuses to reserve


def _int(d: dict, key: str, lo: int, hi: int) -> int:
    v = d.get(key)
    if type(v) is not int or not lo <= v <= hi:
        raise ConfigError(f"{key} must be an integer in {lo}..{hi}")
    return v


def _price(d: dict) -> Price | None:
    keys = ("price_input_uusd_per_mtok", "price_output_uusd_per_mtok", "price_source", "price_checked")
    present = [k for k in keys if k in d]
    if not present:
        return None
    if len(present) != len(keys):
        raise ConfigError("a price needs both prices, price_source and price_checked")
    src, checked = d["price_source"], d["price_checked"]
    if type(src) is not str or not src.startswith("https://") or "<" in src:
        raise ConfigError("price_source must be the https URL of the official pricing page")
    if type(checked) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
        raise ConfigError("price_checked must be YYYY-MM-DD")
    pin = _int(d, "price_input_uusd_per_mtok", 1, 10**9)
    pout = _int(d, "price_output_uusd_per_mtok", 1, 10**9)
    return Price(pin, pout, src, checked)


def load_model_config(path: Path = MODELS_TOML) -> ModelConfig:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    m = d.get("model")
    if type(m) is not dict:
        raise ConfigError("[model] table missing")
    mid, endpoint = m.get("id"), m.get("endpoint")
    if type(mid) is not str or not mid or mid.endswith("-contributor"):  # D-06: no -contributor models
        raise ConfigError("model id missing or not allowed")
    if type(endpoint) is not str or not endpoint.startswith("https://"):
        raise ConfigError("endpoint must be https")
    return ModelConfig(
        id=mid,
        endpoint=endpoint,
        max_output_tokens=_int(m, "max_output_tokens", 1, 65536),
        timeout_s=_int(m, "timeout_s", 1, 600),
        price=_price(m),
    )


def load_project_cap(path: Path = BUDGET_TOML) -> int:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    return _int(d, "project_cap_uusd", 0, 10**12)
