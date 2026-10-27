"""Check this package's sdist/wheel and isolated import; no cross-package harness."""

from __future__ import annotations

import argparse
import email
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
KINDS = (
    "common",
    "query",
    "context",
    "request",
    "report",
    "explanation",
    "error",
    "parameters",
    "mappings",
    "catalog",
)
CHECK = """
import importlib.metadata
from pathlib import Path
import sys
import cedarkit_query as cq
assert Path(cq.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert importlib.metadata.version("cedarkit-query") == sys.argv[1]
for kind in ("common", "query", "context", "request", "report", "explanation", "error", "parameters", "mappings", "catalog"):
    assert cq.get_schema(kind)["$id"] == f"urn:cedarkit:query:{kind}:v1"
request = cq.Request.from_dict({"api_version": "cedarkit.request/v1", "source": {"kind": "file", "path": "absent.grib2"}, "queries": [{}]})
assert request.completeness is None
assert cq.normalize_query({"level_type":"pressure", "level":85000, "level_unit":"Pa"}).level == 850
assert not {"reki", "pandas", "xarray", "eccodes", "nuwe_cmadaas"} & set(sys.modules)
for old in ("RequestList", "Binding", "Response", "Transform", "parse_text_request"):
    assert not hasattr(cq, old)
print(sys.version.split()[0], "isolated wheel import and 10 packaged schemas: OK")
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--python", default="3.11")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    wheels = list(args.dist.glob("*.whl"))
    sdists = list(args.dist.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, (
        "provide a directory with one exact candidate pair"
    )
    wheel, sdist = wheels[0].resolve(), sdists[0].resolve()
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert sum("/schemas/" in n and n.endswith(".json") for n in names) == 10
        assert (
            sum("/tests/vectors/v1/" in n and n.endswith(".json") for n in names) == 3
        )
        assert sum("/examples/" in n and n.endswith(".json") for n in names) == 4
        for name in (
            "resources-v1.md",
            "rules-v1.md",
            "protocol-v1.md",
            "normalization-v1.md",
        ):
            assert any(n.endswith("/docs/" + name) for n in names)
        assert any(n.endswith("/scripts/build_schemas.py") for n in names)
        assert not any(
            n.endswith("/text.py") or n.endswith("/text.json") for n in names
        )
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert {
            n.split("/")[-1] for n in names if "/schemas/" in n and n.endswith(".json")
        } == {k + "-v1.schema.json" for k in KINDS}
        metadata = email.message_from_bytes(
            archive.read(next(n for n in names if n.endswith("/METADATA")))
        )
        assert metadata["Requires-Python"] == ">=3.11"
        assert metadata.get_all("Requires-Dist") == ["jsonschema>=4.20", "PyYAML>=6"]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    offline = ["--offline"] if args.offline else []
    with tempfile.TemporaryDirectory(prefix="cedarkit-query-wheel-") as temporary:
        workspace = Path(temporary)
        venv = workspace / ".venv"
        interpreter = venv / "bin/python"
        subprocess.run(
            ["uv", "venv", *offline, "--python", args.python, str(venv)],
            check=True,
            env=env,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                *offline,
                "--python",
                str(interpreter),
                str(wheel),
            ],
            check=True,
            env=env,
        )
        subprocess.run(
            ["uv", "pip", "check", "--python", str(interpreter)], check=True, env=env
        )
        subprocess.run(
            [str(interpreter), "-I", "-c", CHECK, metadata["Version"]],
            cwd=workspace,
            check=True,
            env=env,
        )
    print("single-package sdist, wheel resources, lightweight import: OK")


if __name__ == "__main__":
    main()
