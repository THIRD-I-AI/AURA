#!/usr/bin/env bash
# Generate TypeScript types from the FastAPI OpenAPI schema.
# Usage: bash scripts/generate-api-types.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/aurabackend"
FRONTEND="$REPO_ROOT/frontend"
SCHEMA="$BACKEND/openapi.json"

echo "→ Exporting OpenAPI schema from FastAPI…"
cd "$BACKEND"
python -c "
from api_gateway.main import app
import json, pathlib
pathlib.Path('openapi.json').write_text(json.dumps(app.openapi(), indent=2))
print(f'  {len(app.openapi()[\"paths\"])} paths exported')
"

echo "→ Generating TypeScript types…"
cd "$FRONTEND"
npx openapi-typescript "$SCHEMA" -o src/types/api.generated.ts

echo "✓ Done — src/types/api.generated.ts updated"
