def synthesize_gate(trend: str, sentiment: str, cfg: dict | None = None) -> dict:
    from ..config_loader import load_config

    regime = (cfg if cfg is not None else load_config().get("regime") or {})
    matrix = regime.get("matrix") or {}
    level = (matrix.get(trend) or {}).get(sentiment) or "defensive"
    caps = regime.get("position_cap") or {}
    policies = regime.get("pool_policy") or {}
    return {
        "gate_level": level,
        "position_cap": float(caps.get(level, 0.35)),
        "pool_policy": policies.get(level, "shrink"),
    }
