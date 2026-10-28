# Parameters, mappings, and independent catalogs

These concrete serialization details implement the confirmed Q18/Q21/Q22
contract. They are reusable I02 structure/validation inputs, not actual
migration results or a populated CEMC catalog. Content versions are independent
positive decimal revision **strings**, not package or business versions.
Unknown fields, null-as-clearing, whitespace IDs, duplicate scoped IDs, unsafe
numbers, and implicit string/number conversions are rejected. Empty top-level
resource lists are explicit empty resources and cause no fallback.

## Parameter resource

Required root: `api_version: cedarkit.parameters/v1`, `id`, `version`,
`parameters`. A parameter requires parameter_id/name; optional fields are
aliases, unit, description, description_cn, parent, fixed_conditions.
Aliases do not inherit; a local alias cannot repeat its own canonical name or
occupy another parameter's ID. Duplicate short names across parameters are
allowed, with ambiguity resolved by explicit domain/conditions rather than
record order. Retired IDs can be supplied through validate_resource_set's
retired_ids; maintaining their database occupancy remains I03's responsibility.

Fixed conditions allow only level_type/level/level_unit/layer/member/member_role
and statistics. Values are scalar or one complete layer, never selector lists
or ranges. Statistics allow over, operation, period, of, population, method,
event, quantile; an empty statistics object is illegal. Scope/time/step/native
and data-access configuration are not fixed parameter fields.

Parent is a single local parameter ID. Effective constraints merge only by
adding restrictions or repeating equivalent normalized values. They cannot
clear or replace a fixed value, switch level to layer, change a window or
operation, or change a fixed event threshold. Unit, description, and explanation
can inherit; changing units cannot change the physical quantity. Parent and
of.parameter_id edges form one acyclic dependency graph. Of is an object
reference for interpretation, not inheritance or computation. Object vertical
conditions must be compatible; object time statistics are not flattened into
outer ensemble statistics.

Time operations: instantaneous (no period), accumulation, mean, minimum,
maximum. Non-instantaneous concrete products require a period. Trailing uses
`{kind: trailing, duration: PT24H}` with positive duration. Since-initialization
uses `{kind: since_initialization}` for accumulation, with no duration. A local
child may provide only period when its parent supplies over/operation.

Ensemble operations: mean, standard_deviation, minimum, maximum, probability,
quantile. They use of `{parameter_id}`, population `{ref}`, and applicable
finite method/event/quantile definitions, without individual member/role or an
outer period. Probability and quantile do not mix. Probability output is a
ratio/percentage; other ensemble outputs are physically compatible with their
objects. Event units belong to the object quantity, independently of the
probability output unit.

Methods are operation-specific: equal weighting for mean; equal weighting and
ddof 0/1 for standard deviation; member_fraction with explicit expected_members
or valid_members denominator; empirical_inverse_cdf with equal weighting for
quantiles; sample_mean / interval_weighted_mean for time means. No arbitrary
method.ref, weights, expression, pipeline, or formula is accepted. Other real
methods require a future finite contract extension with evidence.

Events are gt/ge/lt/le plus threshold `{value, unit}`, or between plus lower
and upper `{value, unit, inclusive}`. Endpoints must be physically compatible
and strictly ordered after shared unit conversion. Quantile is a finite number
in [0,1], not percent text, boolean, or 90. Local constraints already known to
be contradictory fail before inheritance; broad/local definitions may remain
incomplete until explicit product validation.

## Mapping resource

Root: api_version=cedarkit.mappings/v1, id, version, backend (grib2/cmadaas),
mappings, tables. Each mapping requires id, parameter_id, backend, applies_to,
when, native, evidence. Backend must match the resource. Applies_to accepts
exact dataset/product/product_version scalars or lists and a canonical time
selector; no glob names or guessed version ordering. `{}` means deliberately
unrestricted applicability, not an unknown product range.

When reuses canonical Query conditions, excluding parameter/native; identity
is already the mapping parameter_id. Native requires match (backend predicates)
and optional translate. Match permits typed scalars/lists and numeric ranges;
actual native key/domain capability is checked by I06. Translate is an object
with vertical/time/member/statistics/unit slots, each at most one finite rule.
Evidence is a list of nonblank reference strings (IDs, document paths or
locators). An empty list is not verification. No external evidence is read.

Tables require id, key_kind, applies_to, nonempty rows, evidence. A row requires
id/native/canonical and optional evidence. key_kind=code uses one strict scalar
native.code; key_kind=level_type_and_level requires exactly two nonblank string
keys native.level_type/native.level. A table does not mix the two structures.
Canonical is a complete canonical vertical condition or `{member: N}`. Member
tables do not duplicate roles. Row IDs are unique; the same native key may have
equivalent canonical explanations, but conflicting explanations fail. Several
different native keys may map to one canonical condition. String forms such as
"25" and "25.0" are not universally converted to floats.

Table references are local to one mapping resource. Sharing a table with
additional unrelated rows is valid; a fixed parameter must have compatible
rows. Complementary tables/maps can provide different candidate domains, and
equivalent table content need not reuse an ID. Known conflicting explanations
for the same native key, or incompatible explicit source units in overlapping
mappings, fail. Parent/child refinement is permitted. Static checks never use
record order or priority to choose a winner; conflicts requiring actual native
facts remain I06's responsibility and cannot be called verified here.

## Catalog resource

Root: api_version=cedarkit.catalog/v1, id, version, datasets. Dataset records
require id and products; optional aliases, mappings resource references,
member_roles, and execution defaults. Each product requires:

- id and exact business version (the pair is unique inside the dataset);
- scope (fixed Query binding) and provides (paired Query domains describing
  provided business selections, not an expanded Cartesian product);
- populations and methods;
- nonempty sources and evidence references.

A population requires id/members/missing. Members are a nonempty, unique
nonnegative integer list or a finite integer range with start/end/every.
require_all has no min_members; use_available requires a positive min_members
not exceeding the population. Statistical populations are product/version
local and do not arise from discovered fields or Query member conditions.

Methods are product supplements `{parameter_id, method}` and cannot replace
fixed parameter facts. Source bindings have id/kind; file/local bindings have
no deployment path. CMADaaS bindings additionally require data_code,
interface_contract `{id, version: positive integer}`, and an explicit UTC or
valid IANA time_zone. These exact references are separate from catalog content
version and adapter package version. There is no connection endpoint, credential,
client, or arbitrary API role mapping in the catalog.

## Three validation layers and downstream functions

1. `Parameters`, `Mappings`, `Catalog` and `load_value` check structure plus
   internal semantic invariants. `effective_parameters` produces an independent
   resolved view without changing the stored local definitions.
2. `validate_resource_set(parameters, mappings=(), catalog=None, ...)` checks
   parameter references, backend consistency, table relations, and statically
   decidable mapping conflicts. No resource is located or loaded implicitly.
   It returns a deeply immutable ResourceSet.
3. Its optional `products=[(dataset, product, version), ...]`, or a subsequent
   `validate_product`, checks exact selected contexts, provided selections,
   effective units, complete statistical definitions (including of objects),
   product-local populations, and method supplements. An explicit
   `interface_contracts={(id, version), ...}` checks references against a caller-
   supplied registry. Omitting that registry does not claim adapter capability.

A DB export can validate parameters/mappings without the world's complete
catalog. Product-dependent checks apply to explicit selected products, without
assuming a catalog covers every product ever mentioned in a mapping resource.
This does not authorize publishing an incomplete mandatory stage range.

`ResourceSet.parameter(name, candidates=..., conditions=...)` resolves exact
IDs, names and aliases from these definitions. Ambiguity can be resolved by an
explicit domain and compatible canonical conditions; it never consults data
availability. `prepare_queries(queries, resources, scope=..., parameter_ids=...)`
resolves names, applies full fixed conditions to each parameter, checks member
versus ensemble-statistic conflicts, and emits canonical Query branches. An
existing descendant identity is selected only when its fixed meaning is
already implied by the full explicit selection; a wide query remains wide.
No new ID is synthesized. Further runtime product/native ambiguity, coverage,
source execution, and output interpretation belong to I04–I06.

I04 also uses `analyze_product_mappings(resources, dataset, product, version,
scope=...)` for static composition. It resolves each paired provides branch
through the shared parameter definitions, applies the effective configuration
scope, and checks exact parameter IDs, product identity selectors, applies_to.time
and when together. Parent mappings are not implicitly inherited. It retains the
original provides index rather than crossing conditions between branches.

The immutable result contains row applicability and per-provided-parameter
coverage for each explicitly supplied backend resource. `covered` is a static
predicate proof, not a native translation or availability claim. `missing` and
`partial` identify definite gaps; `undetermined` retains a continuous-union proof
gap, an unspecified parameter domain, or exhaustion of the internal 10000-cell
proof budget. Large compact domains are accepted when one predicate covers them;
this analysis budget is independent of execution max_expansion and does not
reject large configured scopes. Full native translation, interface association,
member-role interpretation and source evidence remain I06 responsibilities.
