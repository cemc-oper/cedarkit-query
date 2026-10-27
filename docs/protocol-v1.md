# Stage 1.1 protocol v1

This is the current source contract. The old pre-release v1 schema and parser
are replaced; there is no runtime detection, conversion, deprecated alias, or
second execution format.

## Query

`Query(mapping)` and `Query(**conditions)` accept the same fields. Mixing the
two forms is an error. Query and its nested containers are read-only; its
`to_dict()` copies data. The stored value is the declaration; canonical values
are obtained explicitly with `normalize_query` or the resource-aware
`prepare_queries`.

| Field | Shape/meaning |
|---|---|
| `parameter` | Nonblank name, registered alias, stable ID, or nonempty list; never a native triple |
| `level_type` | `pressure`, `height_above_ground`, `height_above_sea`, `depth_below_land`, `model_level`, or named `surface`, `atmosphere_top`, `entire_atmosphere`, `mean_sea_level` |
| `level` | Number, nonempty list, or numeric range |
| `level_unit` | Shared vertical input unit; pressure defaults hPa, heights/depths m; no unit for indices or named levels |
| `layer` | One complete `{bottom, top}` object or a nonempty list of complete pairs |
| `time`, `valid_time` | Explicit-timezone ISO timestamps, lists, or ranges |
| `step` | Nonnegative fixed ISO duration, list, or range |
| `member` | Strict nonnegative integer, list, or range |
| `member_role` | `control` / `perturbation`, or nonempty list |
| `native` | `{grib: {key: scalar/list/range, ...}}`, additional constraints with no invented business ID |

Range syntax is `{range: {start, end, every?}}`. Bounds are inclusive and
ascending; `every` must be positive. A non-aligned end is not forcibly added.
Without `every`, the range filters existing values and never invents values.
Level and layer are mutually exclusive. Layer endpoints are numbers inheriting
the shared type/unit, or `{type, value?, unit?}` objects. Numeric endpoints need
a type from the declaration or effective definition. Named endpoints carry
neither value nor unit. Outer type and unit are constraints, not overrides;
convertible but different units cannot contradict an explicit shared unit.

The vocabulary describes canonical conditions, not a claim that each backend
supports every type. Source/native-key capability and product-specific model
level definitions remain I04/I06 responsibilities. Parameter aliases and IDs
are resolved only from explicitly supplied, validated definitions; names are
case-sensitive. Partial conditions and `{}` do not select a default parameter,
latest cycle, zero lead, or control member.

## Request, source, and paths

Required Request fields are `api_version: cedarkit.request/v1`, `source`, and
nonempty `queries`. Optional `context` is a mapping or a context-file path;
optional `completeness` is `available` or `required`. Omission remains omission
in serialization and appears as `None` at the Python completeness property.
No action, rename, client, arbitrary overrides, top-level max_expansion, or
single `query` shorthand is accepted.

- file source: `kind: file`, optional dataset, and either literal `path` (one
  file or a nonempty ordered file list), or `directory` plus `glob` (string or
  nonempty pattern list). Patterns do not occur in `path` discovery semantics.
- local source: `kind: local`, `dataset`, `root`.
- service source: `kind: cmadaas`, `dataset`, optional connection `config` path.
  Without config a later executor must receive an external client. Connection
  validation and config/client exclusivity happen at source construction, not
  during declaration loading. Credentials and clients are never fields here.

`Request.load` bases relative paths on the request file. `from_dict` bases them
on explicit `base_dir`, or the construction-time cwd. `to_dict`/`dump` use
absolute paths, preserving targets when exported elsewhere. Path normalization
is lexical and does not resolve symlinks, discover files, or check existence.
Patterns and business identifiers are unchanged. A referenced context stays a
reference. The I04 loader can parse that context with
`load_value(text, "context", base_dir=context_path.parent)`; its resource paths
then use the context document's own base, not the request file or later cwd.

Context accepts only `scope`, `parameter_registry`, `catalog`, `mappings`,
`member_roles`, and `execution`. Scope is another Query constraint. Resource
references require exactly one of `path` and `builtin`, plus optional exact
string `version` and 64-lowercase-hex `sha256`. Only local resource paths are
accepted. Mapping references are keyed by `grib2` / `cmadaas`. Execution accepts
only completeness and positive-integer max_expansion. Models do not insert
implicit defaults; the default expansion limit constant is 10000. Member-role
keys are canonical nonnegative integer strings.

## Errors

`QueryError` is a ValueError. `CompletenessError` subclasses QueryError;
`SourceError` is an independent RuntimeError. All serialize to
`api_version`, `code`, JSON-Pointer `path`, `message`, and deeply read-only
`context`. Copies returned by `to_dict()` are independent. Root path is `""`;
source record positions and resource documents belong in context, not invented
response/output input paths. Callers must supply necessary, safe diagnostics,
not raw credentials, signed URLs, responses, arrays, tracebacks, or clients.
The package does not catch and reclassify arbitrary backend exceptions.

Query codes are invalid_request, unsupported_version, invalid_config,
invalid_query, constraint_conflict, unsupported, expansion_limit_exceeded,
no_match, multiple_matches, duplicate_field, incompatible_grid,
multiple_variables, missing_fields, completeness_undetermined. The final two
require CompletenessError and a report with the corresponding missing/unchecked
status; ordinary QueryError cannot use them without a report.

Source codes are resource_not_found, permission_denied, authentication_failed,
timeout, decode_failed, io_error, service_error,
metadata_interpretation_failed, incomplete_response. Metadata interpretation
requires one of missing_native_metadata, unmapped_native_value,
invalid_vertical_metadata, inconsistent_native_metadata,
fixed_definition_mismatch, **invalid_time_metadata, invalid_member_metadata,
invalid_statistics_metadata, unsupported_native_encoding**. The four final
reasons implement the latest Q22 confirmation. Incomplete responses require
response_truncated or pagination_incomplete. Normal first-match stopping is not
a source failure.

Ordinary errors omit report entirely. CompletenessError stores a read-only
QueryReport and serializes it as a JSON **object** at top-level report; the
report is not duplicated into context. Exception chains remain Python-only.
Unknown format versions fail explicitly; missing/invalid envelope fields are
not reinterpreted as an older format.

## Report and explanation

QueryReport has schema_version=1, completeness, context, overall, queries,
and translations. Each report Query has contiguous index, query, summary,
and sorted/unique `{step_index, query_index}` origins. Summary fields are
status (`complete/missing/unchecked`), expected_count, satisfied_count,
field_count, missing, reasons. Unknown expected/satisfied counts are both null.
Counts describe the checked scope, not output shape. Known missing identities
use `{selection, query_indexes}`; unchecked reasons use code/message/indexes.
Only expected_set_unknown and selection_not_exhausted are initial summary
reason codes. Overall status aggregates subqueries; counts are not summed.

`QueryReport.to_json/from_json/to_dict` do no retrieval. Unknown versions fail.
A Dataset or applicable low-frequency DataArray will carry its JSON string in
cedarkit_query_report when I05/I08 implement that adapter. This package neither
imports xarray nor manufactures such output. Report containers are immutable;
external xarray attrs will still be mutable snapshots.

QueryExplanation contains effective_queries, context, steps, analysis. Each
step has index/input_queries/effective_queries/changes; changes use normalized,
constrained, derived, narrowed, pruned, merged. Analysis contains optional-known
counts (null for unknown), passed/pending checks, and translations. It cannot
carry execution states, actual field counts, missing fields, or completeness.
It is diagnostic data, not a replayable Request.

Both translation collections contain exactly one item per effective Query,
with query_index, coverage, branches, checks. Empty branches require pending
coverage and a nonblank explanation. Branches have local index, backend,
scope, candidate_filter, residual, basis. Residual reuses canonical Query fields
plus resource statistics; it is never a second public selector grammar.
Execution reports additionally require execution status
not_started/stopped_early/exhausted; the initial early-stop reason is
first_match_selected. Exhausted cannot claim that early-stop reason.

Basis kinds are mapping/definition/builtin, or request_association. Resource
references use `{kind: parameters|mappings|catalog, id: ...}` and must resolve
in context.definitions; rows additionally require table and resource. A request
association basis uses an exact `{id, version}` contract and fields, without
pretending the response independently supplied those fields. Controlled
translation check codes are context_resolved, return_metadata_sufficient,
complete_retrieval_supported, request_association_verified. Their passed state
never proves products exist or all candidates have been retrieved.
