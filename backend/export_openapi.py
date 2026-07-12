"""Export the FastAPI OpenAPI schema to a JSON file.

Usage:
    uv run python -m backend.export_openapi frontend/generated/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.server import app
from domain.orchestration import (
    CaptureGuidancePayload,
    OrchestrationDonePayload,
    OrchestrationProgressPayload,
    OrchestrationResultPayload,
    OrchestrationStatusPayload,
)


def main() -> None:
    """Write the application OpenAPI schema to the requested file path."""
    output_arg = sys.argv[1] if len(sys.argv) > 1 else "frontend/generated/openapi.json"
    output_path = Path(output_arg)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    schema = app.openapi()
    components = schema.setdefault("components", {})
    schema_bucket = components.setdefault("schemas", {})
    for model in (
        CaptureGuidancePayload,
        OrchestrationStatusPayload,
        OrchestrationProgressPayload,
        OrchestrationResultPayload,
        OrchestrationDonePayload,
    ):
        schema_bucket[model.__name__] = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )

    output_path.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote OpenAPI schema to {output_path}")


if __name__ == "__main__":
    main()
