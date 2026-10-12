"""Check source/wheel contents and import a wheel outside the checkout."""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDIST = ROOT / "dist" / "cedarkit_query-0.1.0.tar.gz"
WHEEL = ROOT / "dist" / "cedarkit_query-0.1.0-py3-none-any.whl"
KINDS = ("request", "request-list", "query", "binding", "transform", "data-reference", "response", "error")

with tarfile.open(SDIST) as archive:
    names = archive.getnames()
    assert sum("/schemas/" in name and name.endswith(".json") for name in names) == 8
    assert sum("/tests/vectors/v1/" in name and name.endswith(".json") for name in names) == 2
    assert any(name.endswith("/docs/protocol-v1.md") for name in names)
    assert any(name.endswith("/scripts/build_schemas.py") for name in names)
with zipfile.ZipFile(WHEEL) as archive:
    names = archive.namelist()
    assert sum(name.startswith("cedarkit_query/schemas/") and name.endswith(".json") for name in names) == 8

with tempfile.TemporaryDirectory(prefix="cedarkit-query-wheel-") as temporary:
    checkout_free = Path(temporary)
    interpreter = checkout_free / ".venv/bin/python"
    subprocess.run(["uv", "venv", "--python", "3.11", str(checkout_free / ".venv")],
                   check=True, capture_output=True)
    subprocess.run(["uv", "pip", "install", "--python", str(interpreter), str(WHEEL)],
                   check=True, capture_output=True)
    check = '''
import importlib.metadata
import sys
from cedarkit_query import get_schema, load_request
assert importlib.metadata.version("cedarkit-query") == "0.1.0"
for kind in ("request", "request-list", "query", "binding", "transform", "data-reference", "response", "error"):
    assert get_schema(kind)["$id"] == f"urn:cedarkit:query:{kind}:v1"
request = load_request({"api_version": "cedarkit.request/v1", "action": "validate",
                        "query": {"api_version": "cedarkit.query/v1", "domain": "grid", "dataset": "example.grid"}})
assert request.query.dataset == "example.grid"
assert not {"reki", "pandas", "xarray", "eccodes"} & set(sys.modules)
print(sys.version.split()[0], "external wheel import and 8 packaged schemas: OK")
'''
    subprocess.run([str(interpreter), "-I", "-c", check], cwd=checkout_free, check=True)

print("source distribution, wheel resources, lightweight import: OK")
