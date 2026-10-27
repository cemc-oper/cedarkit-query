"""Check/canonicalize the authoritative packaged JSON Schemas (no old generator)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1] / "src" / "cedarkit_query" / "schemas"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check without rewriting")
    args = parser.parse_args()
    documents = {}
    files = sorted(ROOT.glob("*-v1.schema.json"))
    for path in files:
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] not in documents, schema["$id"]
        documents[schema["$id"]] = schema
    assert len(documents) == 10, "expected exactly the ten current contract schemas"

    def refs(node, document):
        if isinstance(node, list):
            for item in node:
                refs(item, document)
        elif isinstance(node, dict):
            if "$ref" in node:
                uri, _, fragment = node["$ref"].partition("#")
                target = documents[uri or document]
                if fragment:
                    assert fragment.startswith("/"), fragment
                    for key in fragment[1:].split("/"):
                        target = target[key.replace("~1", "/").replace("~0", "~")]
            for value in node.values():
                refs(value, document)

    for path in files:
        schema = json.loads(path.read_text())
        refs(schema, schema["$id"])
        text = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            assert path.read_text() == text, f"noncanonical JSON: {path}"
        else:
            path.write_text(text)
    print("10 authoritative schemas: valid, canonical, and closed local references")


if __name__ == "__main__":
    main()
