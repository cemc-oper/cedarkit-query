# Finite translation configuration contracts

`RULES` is a read-only registry of nine configuration contracts.
`validate_rule(value, backend=..., responsibility=..., path=...)` returns a
canonical rule object or QueryError with a precise path and registered reason.
Each object has rule and optional config only. Absent config and `{}` are
equivalent where no config is needed; null and unknown keys are illegal.

| Rule | Backend / responsibility | Config |
|---|---|---|
| grib.fixed_surfaces | grib2 / vertical | none |
| grib.time | grib2 / time | none |
| grib.member | grib2 / member | none |
| grib.statistics | grib2 / statistics | none |
| cmadaas.vertical_table | cmadaas / vertical | required table reference |
| cmadaas.time_fields | cmadaas / time | none |
| cmadaas.member_field | cmadaas / member | optional table reference |
| cmadaas.statistics_fields | cmadaas / statistics | none |
| common.value_unit | grib2 or cmadaas / unit | optional source_unit |

No request_field/response_field/encoding/width, first_is_bottom, free offset,
factor, order, depends_on, supports_range, ignore_errors, or trust_request
option is accepted. The Q22 later confirmations supersede the older example
configurations using levelCode/layerCode and arbitrary request/response keys.

Layer tables accept code and complete joint level-type/level forms; member
number tables contain native.code and canonical.member, leaving role mapping
to context. Common value-unit source_unit is an explicit original-value unit,
not inferred from the parameter's target unit. Missing config cannot silently
choose a source unit. Actual returned units must be checked by I06, not
resolved by a resource rule priority.

Unknown rules, invalid config, inappropriate duty/backend, missing tables,
conflicting rows and overlapping contradictory mappings have distinct
invalid_config reasons: unknown_rule, invalid_rule_config, rule_not_applicable,
missing_table, conflicting_table_rows, ambiguous_mapping. Actual translation
coverage, native metadata interpretation, and runtime capability are separate.

`GRIB_TEMPLATE_FAMILIES` records confirmed **contract targets**, not supported
products: general 4.0/4.8, individual member 4.1/4.11, ensemble-derived 4.2/4.12,
probability 4.5/4.9, quantile 4.6/4.10. I06 must check edition/producer/table
versions, reference-time meaning, units, member attributes, all statistical
period descriptors and fixed definitions. The basic time-statistic path's
numberOfTimeRange=1 and typeOfTimeIncrement=2 are conditions to verify, not a
reason to silently simplify complex products. GRIB1/local templates and any
mandatory product extensions still require actual finite implementation and
independent evidence.

I02 neither decodes GRIB nor registers a concrete, evidence-free CMADaaS
interface as supported. I04 binds actual products to exact interface contracts;
I06 implements and verifies response/request_association/definition facts and
source_unit checks. I09 links real-product evidence. Passing these config
validators alone cannot produce a supported=true or business passed flag.
