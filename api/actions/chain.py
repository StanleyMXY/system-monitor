import json
from pathlib import Path

_CHAINS_PATH = Path(__file__).parent.parent.parent / "config" / "causal_chains.json"


def append_causal_chain(chain: dict, chains_path: Path = _CHAINS_PATH) -> None:
    if chains_path.exists():
        with open(chains_path, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = []

    existing.append(chain)

    with open(chains_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
