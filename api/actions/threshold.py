import json
from pathlib import Path
from engine.threshold_config import METRIC_THRESHOLD_MAP

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def apply_threshold(metric: str, suggested: dict, config_dir: Path = CONFIG_DIR) -> None:
    if metric not in METRIC_THRESHOLD_MAP:
        raise ValueError(f"unknown metric: {metric}")

    domain, sub_key, key_map = METRIC_THRESHOLD_MAP[metric]
    path = config_dir / f"thresholds_{domain}.json"

    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    for role, json_key in key_map.items():
        value = suggested.get(role)
        if value is not None:
            cfg.setdefault(sub_key, {})[json_key] = value

    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
