"""Loads config/models.toml and config/budget.toml. Never reads keys (those are env-only, models/ only)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODELS_TOML = REPO / "config" / "models.toml"
BUDGET_TOML = REPO / "config" / "budget.toml"
MIN_OUTPUT_TOKENS = 16  # Responses API: max_output_tokens >= 16; it bounds reasoning + visible output (D-55)
# D-61: the documented reasoning.effort values (https://dev.meta.ai/docs/protocols/responses). "none" is
# HTTP 400 for Muse Spark and is never sent; an unset key means the field is not sent at all.
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    input_uusd_per_mtok: int
    output_uusd_per_mtok: int
    source: str
    checked: str
    cached_input_uusd_per_mtok: int | None = None  # None: cached input tokens are priced as input tokens


@dataclass(frozen=True)
class ModelConfig:
    id: str
    endpoint: str
    max_output_tokens: int
    timeout_s: int
    price: Price | None  # None = unset: the ledger refuses to reserve (fails closed)
    reasoning_effort: str | None = (
        None  # D-61: one of REASONING_EFFORTS, sent in every request; None = not sent
    )


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
    pcached = None
    if "price_cached_input_uusd_per_mtok" in d:
        pcached = _int(d, "price_cached_input_uusd_per_mtok", 1, 10**9)
        if pcached > pin:
            raise ConfigError("price_cached_input_uusd_per_mtok must not exceed the input price")
    return Price(pin, pout, src, checked, pcached)


def _reasoning_effort(d: dict) -> str | None:
    if "reasoning_effort" not in d:
        return None
    v = d["reasoning_effort"]
    if type(v) is not str or v not in REASONING_EFFORTS:
        raise ConfigError(f'reasoning_effort must be one of {", ".join(REASONING_EFFORTS)} (never "none")')
    return v


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
        max_output_tokens=_int(m, "max_output_tokens", MIN_OUTPUT_TOKENS, 65536),
        timeout_s=_int(m, "timeout_s", 1, 600),
        price=_price(m),
        reasoning_effort=_reasoning_effort(m),
    )


@dataclass(frozen=True)
class VisionConfig:
    """``[vision]`` (T-16): the Muse Spark image call of the vision witness. Same fields as ``ModelConfig``
    plus ``image_tokens_reserve``, the input tokens reserved per image before every call."""

    id: str
    endpoint: str
    max_output_tokens: int
    timeout_s: int
    price: Price | None
    reasoning_effort: str | None
    image_tokens_reserve: int


def load_vision_config(path: Path = MODELS_TOML) -> VisionConfig:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    v = d.get("vision")
    if type(v) is not dict:
        raise ConfigError("[vision] table missing")
    mid, endpoint = v.get("id"), v.get("endpoint")
    if type(mid) is not str or not mid or mid.endswith("-contributor"):
        raise ConfigError("vision model id missing or not allowed")
    if type(endpoint) is not str or not endpoint.startswith("https://"):
        raise ConfigError("vision endpoint must be https")
    return VisionConfig(
        id=mid,
        endpoint=endpoint,
        max_output_tokens=_int(v, "max_output_tokens", MIN_OUTPUT_TOKENS, 65536),
        timeout_s=_int(v, "timeout_s", 1, 600),
        price=_price(v),
        reasoning_effort=_reasoning_effort(v),
        image_tokens_reserve=_int(v, "image_tokens_reserve", 1, 100_000),
    )


def load_project_cap(path: Path = BUDGET_TOML) -> int:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    return _int(d, "project_cap_uusd", 0, 10**12)
