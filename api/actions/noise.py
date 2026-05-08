import json
import re
from datetime import datetime
from pathlib import Path

_NOISE_RULES_PATH = Path(__file__).parent.parent.parent / "config" / "noise_rules.json"


def append_noise_rule(
    term: str,
    description: str,
    suggested_phrases: list[str],
    noise_path: Path = _NOISE_RULES_PATH,
) -> None:
    if noise_path.exists():
        with open(noise_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"_comment": "已确认的日志噪音规则", "rules": []}

    existing_ids = [r.get("id", "") for r in data.get("rules", [])]
    used_nums = [int(m.group(1)) for rid in existing_ids if (m := re.match(r"noise_(\d+)$", rid))]
    next_num = max(used_nums, default=0) + 1

    data.setdefault("rules", []).append({
        "id": f"noise_{next_num:03d}",
        "description": description,
        "must_phrases": suggested_phrases,
        "confirmed_at": datetime.now().strftime("%Y-%m-%d"),
        "confirmed_by": "operator",
    })

    with open(noise_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
