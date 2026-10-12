# cedarkit-query

Low dependency, language-neutral value models and JSON Schema resources for the CedarKit grid query protocol v1.

The current package implements strict structure validation, JSON/YAML/Python loading, immutable protocol values, and serialization. It does not normalize selectors, resolve catalogs, access data, or parse text requests. See [protocol-v1.md](docs/protocol-v1.md) for the field contract and current implementation status.

```bash
uv sync --group test
uv run pytest
uv build
```
