# Restricted grid query text syntax v1

`parse_text_request(text)` returns one public `Request`; `parse_text_requests(text)` returns a `RequestList`. Both validate through the same v1 Schema as Python, JSON, and YAML input. Text parsing does not normalize values, resolve aliases, expand combinations, inspect files, or execute a provider. Pass the result to `normalize_request` or `expand_request` for those pure steps.

## Lines and fields

One physical line contains `ACTION key=value key=value ...`. Actions are `VALIDATE`, `PLAN`, `LIST`, `LOCATE`, and `READ`, in all uppercase or all lowercase. Keys are lowercase and case sensitive. The only allowed keys are:

| Query | Policy |
|---|---|
| `dataset`, `parameter`, `reference_time`, `lead_time`, `vertical.type`, `vertical.unit`, `vertical.values`, `statistic.kind`, `statistic.window`, `member`, `native_grid.id` | `policy.cardinality`, `policy.missing` |

`dataset` is always required. `PLAN`, `LOCATE`, and `READ` also require `parameter`, `reference_time`, and `lead_time`. The value model checks the remaining conditions, including vertical unit/value pairing and statistic windows. Every line constructs its own request. For multiple requests, put a line containing only `---` between request lines; optional whitespace around that separator and blank lines are ignored. `parse_text_request` rejects separators and multiple nonblank lines. `parse_text_requests` accepts one or more request lines and preserves their order and pairing.

```text
LOCATE dataset=example.grid parameter=cedarkit.t reference_time=2026-08-30T08:00:00+08:00 lead_time=P1D vertical.type=surface
---
LOCATE dataset=example.grid parameter=cedarkit.h reference_time=2026-08-30T00:00:00Z lead_time=PT12H vertical.type=isobaric vertical.unit=hPa vertical.values=[500,850]
```

## Values

- An unquoted atom uses only ASCII letters, digits, `_ . : + / -` and contains no whitespace. A value containing whitespace, a comma, `=`, a quote, or another special character must be one JSON double-quoted string. JSON escapes such as `\"` and `\u00e9` work inside quoted strings; backslash escapes outside quotes do not.
- `parameter`, `reference_time`, `lead_time`, `vertical.values`, and `member` may use `[item,item]` lists. Items may be quoted; commas inside quoted items are data. Empty lists, empty items, nested lists and unquoted list whitespace fail.
- `reference_time`, `lead_time`, and `vertical.values` may use `start..stop/step`. This becomes a structured range with `closed:"both"`; other endpoint modes use JSON/YAML/Python input. All three parts are required. Time and duration syntax is checked by the shared model and pure normalization layer.
- `vertical.values` parses decimal numbers, including a sign, fraction or exponent. Text numbers above magnitude `1e308`, non-finite conversions and nonzero values that underflow to zero fail. `member` uses `role:id` with an explicit `control` or `perturbation` role; quote the whole value when the ID contains special characters. Other fields are strings, including timestamps and durations.
- Text syntax has no aliases as `{namespace,name}` objects, coverage, vertical bounds, transforms, options, or arbitrary expressions. Use the structured interface for those values. No environment expansion, SQL, Python evaluation or cross-request inheritance occurs.

Duplicate or unknown keys and malformed syntax raise `QueryError` with `code="invalid_query"`, a JSON Pointer `path`, and one-based `context.line`/`context.column`. In a `RequestList`, a field path begins with `/requests/<index>`. Model validation errors retain their model error code/path and receive the text position. [Machine-readable text vectors](../tests/vectors/v1/text.json) provide positive and negative examples.
