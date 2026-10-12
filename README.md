# cedarkit-query

Low dependency, language-neutral value models and JSON Schema resources for the CedarKit grid query protocol v1.

The current package implements strict structure validation, JSON/YAML/Python and restricted text loading, immutable protocol values, serialization, pure normalization, bounded expansion and independent identities. It does not load catalogs or access data. See [protocol-v1.md](docs/protocol-v1.md), [normalization-v1.md](docs/normalization-v1.md), and [text-v1.md](docs/text-v1.md) for the contract and current implementation status.

```bash
uv sync --group test
uv run pytest
uv build
```
