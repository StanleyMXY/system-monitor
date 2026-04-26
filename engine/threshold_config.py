# engine/threshold_config.py
# 从 config/ 目录加载对应域的阈值 JSON，过滤元信息键
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_thresholds(domain: str) -> dict:
    path = CONFIG_DIR / f"thresholds_{domain}.json"
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    return {k: v for k, v in config.items() if not k.startswith("_")}
