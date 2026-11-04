# Shared normalization and selection algebra

All operations here are pure. They do not inspect a file, fetch a resource,
connect to a service, resolve aliases from the process environment, or infer a
product version from a name. The sole selector value is Query.

`normalize_query` returns a canonical Query; constructing a Query alone stores
a validated declaration. Lists are semantic sets, deduplicated and ordered;
a singleton list is equivalent to a scalar. Pressure normalizes to hPa,
height/depth to m. Known spelling aliases are explicit, finite mappings.
Decimal calculations avoid arbitrary tolerances; values that cannot be
represented losslessly as JSON numbers fail. Named levels and model indices
have no physical unit. Indices are nonnegative integers; physical layer
ordering is checked for pressure/depth (bottom larger) and height (top larger),
without interpreting model indices as physical heights.

Time normalizes to UTC; duration normalizes to fixed seconds, preserving up
to nanosecond precision. Unrepresentable finer precision fails without
truncation. Calendar month/year durations are not approximated as days. Two
scalar temporal fields derive the third. Lists/ranges keep their constraints
and expand only legal `valid_time = time + step` tuples; they are not three
independent Cartesian axes. Every explicit finite temporal value must
participate in a legal tuple. This is separate from whether a product exists.

```python
from cedarkit_query import normalize_query, query_identity

q = normalize_query({"level_type": "pressure", "level": 85000, "level_unit": "Pa"})
assert q.to_dict() == {"level_type": "pressure", "level": 850, "level_unit": "hPa"}
assert query_identity(q) == query_identity({"level_type": "pressure", "level": 850})
```

`query_identity(query, context=...)` hashes canonical effective discrete
conditions and the explicitly supplied semantic context. Equivalent discrete
range/list forms, scalar derived times, equivalent units, and list order share
an identity. Supply the relevant source scope and definition versions when
they contribute to identity. Native constraints remain part of the identity.

For named selections, call `prepare_queries(queries, parameters)` before
computing an effective identity or combining branches. Pass the validated
parameter index by stable ID, with inherited fixed conditions already applied.
IDs match exactly. Names and aliases match case-sensitively within the supplied
definitions, and ambiguity reports candidate IDs. The helper adds fixed
conditions, normalizes explicit units, and resolves a parent selection to a
variant when its full fixed conditions already follow from the selection.
For example, a temperature Query that explicitly selects 200 cm above ground
can have the same effective identity as `cedarkit.t2m`. Translation into
product-specific native keys is a source execution step.

`expand_queries(queries, max_expansion=10000, member_roles=None, cemc=False)`
returns unique canonical Query combinations. Whole layer pairs stay intact.
Multiple Queries form a union, without cross-Query combinations. Omitted
fields and continuous ranges remain filters, not enumerated field domains.
Consequently the result count is an explicit discrete-selection count, never
an expected/actual business-field count. CEMC default member roles require an
explicit `cemc=True` context; a supplied member mapping overrides that default.
The algebra does not impose CEMC numbering on other data systems. Number/role
conditions combine without independent member-role Cartesian axes.

The expansion limit is a positive strict integer and can be greater than the
library default. It is checked on normalized, deduplicated effective
combinations; exceeding it raises expansion_limit_exceeded with limit,
count_lower_bound, and origins. There is no truncation or unlimited sentinel.
The bound does not limit files, requests, records, grid points, memory, or
network calls. The implementation does not claim a validated capacity or
provide backend pagination/retry policies.

`intersect_queries(left, right, max_expansion=...)` returns their conjunction,
or None for legal disjoint selections. `combine_queries(steps, scope=...,
max_expansion=...)` evaluates an AND of OR lists, returns compact branches with
origins, merges identical branches, and checks each step's effective bound.
An empty effective selection is an empty branch tuple, never an illegal Query
containing an empty list. Scope and each submitted Query are validated even
when a previous step is already empty. Temporal and numeric discrete-range
intersections preserve their lattices and original paired constraints.

`constrain_query(query, fixed_or_scope)` is intentionally different from a
normal intersection: every explicit scalar/list/every choice must be
compatible with the binding. A continuous filter may contain a fixed value.
For a height scope fixed at 2 m, a selector list containing 2 and 10 m
conflicts, while a continuous 0–10 m range can narrow to 2 m. The same
constraint semantics apply to native keys.

`intersect_scopes(left, right)` combines configuration bounds without enumerating
an entire product domain. It uses the shared intersection semantics, permits a
partial vertical bound to gain its type from the other bound, and returns None
for a known disjoint conjunction. It does not execute a selection or verify every temporal tuple. Effective Query
preparation and expansion retain their normal legality and max_expansion checks.
