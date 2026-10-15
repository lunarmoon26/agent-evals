"""Regenerate public schemas from the canonical typed contracts."""

import json
from pathlib import Path

from agent_evals.cli import SCHEMAS


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "schemas"
    target.mkdir(exist_ok=True)
    for name, model in SCHEMAS.items():
        (target / f"{name}.v1.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        )


if __name__ == "__main__":
    main()
