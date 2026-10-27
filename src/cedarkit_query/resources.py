"""In-memory resource checks. Database edits and backend execution live elsewhere."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ._json import canonical, freeze, plain, thaw
from .errors import QueryError
from .normalize import (
    _contains,
    _count,
    _intersect,
    _normalize,
    convert_quantity,
    duration_seconds,
    duration_text,
    unit_info,
    unit_name,
)
from .validation import _error

RULES = freeze(
    {
        "grib.fixed_surfaces": {
            "backend": "grib2",
            "responsibility": "vertical",
            "config": [],
        },
        "grib.time": {"backend": "grib2", "responsibility": "time", "config": []},
        "grib.member": {"backend": "grib2", "responsibility": "member", "config": []},
        "grib.statistics": {
            "backend": "grib2",
            "responsibility": "statistics",
            "config": [],
        },
        "cmadaas.vertical_table": {
            "backend": "cmadaas",
            "responsibility": "vertical",
            "config": ["table"],
        },
        "cmadaas.time_fields": {
            "backend": "cmadaas",
            "responsibility": "time",
            "config": [],
        },
        "cmadaas.member_field": {
            "backend": "cmadaas",
            "responsibility": "member",
            "config": ["table"],
        },
        "cmadaas.statistics_fields": {
            "backend": "cmadaas",
            "responsibility": "statistics",
            "config": [],
        },
        "common.value_unit": {
            "backend": "common",
            "responsibility": "unit",
            "config": ["source_unit"],
        },
    }
)


def validate_rule(rule, *, backend, responsibility, path=""):
    """Only configuration validity, never a claim of runtime encoding support."""
    if (
        not isinstance(rule, Mapping)
        or set(rule) - {"rule", "config"}
        or "rule" not in rule
    ):
        _error(
            path,
            "rule must contain rule and an allowed config",
            "invalid_config",
            reason="invalid_rule_config",
        )
    name = rule["rule"]
    if not isinstance(name, str) or name not in RULES:
        _error(path + "/rule", "unknown rule", "invalid_config", reason="unknown_rule")
    spec = RULES[name]
    if (
        backend not in {"grib2", "cmadaas"}
        or spec["backend"] not in {"common", backend}
        or spec["responsibility"] != responsibility
    ):
        _error(
            path + "/rule",
            "rule is not applicable to this backend/responsibility",
            "invalid_config",
            reason="rule_not_applicable",
        )
    config = rule.get("config", {})
    if (
        not isinstance(config, Mapping)
        or set(config) - set(spec["config"])
        or any(
            not isinstance(v, str) or not v.strip() or v != v.strip()
            for v in config.values()
        )
    ):
        _error(
            path + "/config",
            "invalid rule configuration",
            "invalid_config",
            reason="invalid_rule_config",
        )
    if name == "cmadaas.vertical_table" and "table" not in config:
        _error(
            path + "/config/table",
            "vertical rule requires a table",
            "invalid_config",
            reason="invalid_rule_config",
        )
    return {"rule": name, **({"config": dict(config)} if config else {})}


def preflight_rules(data):
    if not isinstance(data, dict) or not isinstance(data.get("mappings"), list):
        return
    for i, mapping in enumerate(data["mappings"]):
        if not isinstance(mapping, dict) or not isinstance(mapping.get("native"), dict):
            continue
        translate = mapping["native"].get("translate", {})
        if isinstance(translate, dict):
            for duty, rule in translate.items():
                validate_rule(
                    rule,
                    backend=mapping.get("backend"),
                    responsibility=duty,
                    path=f"/mappings/{i}/native/translate/{duty}",
                )


def _unique(records, key, path):
    result = {}
    for i, item in enumerate(records):
        value = item[key]
        if value in result:
            _error(f"{path}/{i}/{key}", f"duplicate {key}: {value}")
        result[value] = item
    return result


def _quantity(q, path):
    unit = unit_name(q["unit"])
    dimension, _, _ = unit_info(unit)
    target = {"pressure": "Pa", "length": "m", "temperature": "K", "ratio": "1"}.get(
        dimension, unit
    )
    return {
        "value": convert_quantity(q["value"], unit, target, path),
        "unit": target,
        **({"inclusive": q["inclusive"]} if "inclusive" in q else {}),
    }


def normalize_statistics(stats, path="", *, complete=False):
    from .model import validate_fragment

    stats = validate_fragment(stats, "statistics", path=path)
    if "quantile" in stats:
        from .normalize import json_number

        stats["quantile"] = json_number(stats["quantile"])
    if "period" in stats and stats["period"]["kind"] == "trailing":
        seconds = duration_seconds(
            stats["period"]["duration"], path + "/period/duration"
        )
        if seconds <= 0:
            _error(path + "/period/duration", "trailing duration must be positive")
        stats["period"]["duration"] = duration_text(seconds)
    if "event" in stats:
        event = stats["event"]
        for key in ("threshold", "lower", "upper"):
            if key in event:
                event[key] = _quantity(event[key], path + "/event/" + key)
        if event["operator"] == "between":
            lower, upper = event["lower"], event["upper"]
            if lower["unit"] != upper["unit"] or lower["value"] >= upper["value"]:
                _error(
                    path + "/event",
                    "event lower must be less than upper in compatible units",
                )
    declared_over, operation = stats.get("over"), stats.get("operation")
    possible = {"time", "ensemble"} if declared_over is None else {declared_over}
    if operation in {"instantaneous", "accumulation"}:
        possible &= {"time"}
    if operation in {"standard_deviation", "probability", "quantile"}:
        possible &= {"ensemble"}
    if "period" in stats:
        possible &= {"time"}
    if {"of", "population", "event", "quantile"} & set(stats):
        possible &= {"ensemble"}
    method_hint = stats.get("method", {})
    if method_hint.get("kind") in {"sample_mean", "interval_weighted_mean"}:
        possible &= {"time"}
    if method_hint.get("kind") in {"member_fraction", "empirical_inverse_cdf"} or {
        "weighting",
        "ddof",
    } & set(method_hint):
        possible &= {"ensemble"}
    if not possible:
        _error(path, "local statistical constraints are already contradictory")
    over = next(iter(possible)) if len(possible) == 1 else None
    forbidden = set()
    if operation is not None and operation != "probability":
        forbidden.add("event")
    if operation is not None and operation != "quantile":
        forbidden.add("quantile")
    if stats.get("period", {}).get(
        "kind"
    ) == "since_initialization" and operation not in {None, "accumulation"}:
        _error(path + "/period", "since_initialization is an accumulation period")
    if over == "time":
        if operation in {"standard_deviation", "probability", "quantile"}:
            _error(path + "/operation", "operation is not a time statistic")
        forbidden |= {"of", "population", "event", "quantile"}
        if operation == "instantaneous":
            forbidden |= {"period", "method"}
        elif operation in {"accumulation", "minimum", "maximum"}:
            forbidden.add("method")
    elif over == "ensemble":
        if operation in {"instantaneous", "accumulation"}:
            _error(path + "/operation", "operation is not an ensemble statistic")
        forbidden = {"period"}
        if operation != "probability" and operation is not None:
            forbidden.add("event")
        if operation != "quantile" and operation is not None:
            forbidden.add("quantile")
        if operation in {"minimum", "maximum"}:
            forbidden.add("method")
    if forbidden & set(stats):
        key = sorted(forbidden & set(stats))[0]
        _error(path + "/" + key, "field is forbidden for this statistic")
    method = stats.get("method")
    if method:
        mk = method.get("kind")
        allowed = (
            {"kind", "denominator"}
            if mk == "member_fraction"
            else {"kind", "weighting"}
            if mk == "empirical_inverse_cdf"
            else {"kind"}
            if mk in {"sample_mean", "interval_weighted_mean"}
            else {"weighting", "ddof"}
        )
        if set(method) - allowed:
            _error(
                path + "/method", "method contains fields outside its finite contract"
            )
        if over == "time" and (
            operation not in {None, "mean"}
            or mk not in {"sample_mean", "interval_weighted_mean"}
        ):
            _error(path + "/method", "time mean requires an appropriate time method")
        if over == "ensemble" and operation is not None:
            expected = {
                "mean": None,
                "standard_deviation": None,
                "probability": "member_fraction",
                "quantile": "empirical_inverse_cdf",
            }.get(operation)
            if mk != expected or (operation == "mean" and "ddof" in method):
                _error(path + "/method", "method is not applicable to this operation")
    if complete:
        required = {"over", "operation"}
        if over == "time" and operation != "instantaneous":
            required.add("period")
        if over == "ensemble":
            required |= {"of", "population"}
            if operation in {"mean", "standard_deviation", "probability", "quantile"}:
                required.add("method")
            if operation == "probability":
                required.add("event")
            if operation == "quantile":
                required.add("quantile")
        missing = required - set(stats)
        if missing:
            _error(
                path + "/" + sorted(missing)[0],
                "concrete statistic lacks required definition",
                "invalid_config",
                reason="missing_context",
            )
        if method:
            needed = (
                {"kind"}
                if over == "time"
                else {"weighting"}
                if operation == "mean"
                else {"weighting", "ddof"}
                if operation == "standard_deviation"
                else {"kind", "denominator"}
                if operation == "probability"
                else {"kind", "weighting"}
            )
            if needed - set(method):
                _error(
                    path + "/method",
                    "concrete method is incomplete",
                    "invalid_config",
                    reason="missing_context",
                )
    return stats


def _merge(parent, child, path):
    result = plain(parent)
    for key, value in child.items():
        if key not in result:
            result[key] = plain(value)
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = _merge(result[key], value, path + "/" + key)
        elif canonical(result[key]) != canonical(value):
            _error(path + "/" + key, "child/context cannot override a fixed definition")
    return result


def normalize_fixed(fixed, path="", *, allow_unbound=True):
    from .model import validate_fragment

    data = validate_fragment(fixed, "fixed", path=path)
    stats = data.pop("statistics", None)
    if "level" in data and "layer" in data:
        _error(path, "effective level and layer are mutually exclusive")
    result = _normalize(data, path, allow_unbound=allow_unbound)
    if stats is not None:
        result["statistics"] = normalize_statistics(stats, path + "/statistics")
    if result.get("statistics", {}).get("over") == "ensemble" and {
        "member",
        "member_role",
    } & set(result):
        _error(path, "ensemble statistic cannot carry an individual member")
    return result


def merge_fixed(parent, child, path=""):
    # Supply only a missing type, never silently adopt a parent's input unit.
    p, c = plain(parent), plain(child)
    for key in ("level_type",):
        if key not in c and key in p:
            c[key] = p[key]
    return normalize_fixed(_merge(normalize_fixed(p), normalize_fixed(c), path), path)


def _effective_parameters(resource, *, retired_ids=()):
    records = resource["parameters"]
    index = _unique(records, "parameter_id", "/parameters")
    resolved = {}
    visiting = set()
    retired = set(retired_ids)

    def visit(pid):
        if pid in visiting:
            _error("/parameters", "parent/of dependency cycle")
        if pid not in index:
            _error("/parameters", "missing parameter reference: " + pid)
        if pid in resolved:
            return resolved[pid]
        if pid in retired:
            _error("/parameters", "active reference to a retired parameter ID")
        visiting.add(pid)
        p = index[pid]
        result = plain(p)
        parent = visit(p["parent"]) if "parent" in p else None
        fixed = p.get("fixed_conditions", {})
        if parent:
            fixed = merge_fixed(
                parent.get("fixed_conditions", {}),
                fixed,
                "/parameters/" + pid + "/fixed_conditions",
            )
            for key in ("unit", "description", "description_cn"):
                if key not in result and key in parent:
                    result[key] = parent[key]
            if (
                "unit" in p
                and "unit" in parent
                and unit_info(p["unit"])[0] != unit_info(parent["unit"])[0]
            ):
                _error(
                    "/parameters/" + pid + "/unit",
                    "child changes the physical quantity",
                )
        else:
            fixed = normalize_fixed(fixed, "/parameters/" + pid + "/fixed_conditions")
        result["fixed_conditions"] = fixed
        if "unit" in result:
            result["unit"] = unit_name(result["unit"])
        stats = fixed.get("statistics", {})
        if "of" in stats:
            target = visit(stats["of"]["parameter_id"])
            target_fixed = {
                k: v for k, v in target["fixed_conditions"].items() if k != "statistics"
            }
            outer_fixed = {k: v for k, v in fixed.items() if k != "statistics"}
            if _intersect(target_fixed, outer_fixed) is None:
                _error(
                    "/parameters/" + pid + "/fixed_conditions/of",
                    "statistical object has incompatible fixed conditions",
                )
            if "event" in stats and "unit" in target:
                event = stats["event"]
                for key in ("threshold", "lower", "upper"):
                    if key in event:
                        convert_quantity(
                            event[key]["value"], event[key]["unit"], target["unit"]
                        )
        visiting.remove(pid)
        resolved[pid] = result
        return result

    for pid, p in index.items():
        aliases = p.get("aliases", [])
        if p["name"] in aliases:
            _error(
                "/parameters/" + pid + "/aliases", "alias repeats the canonical name"
            )
        for name in [p["name"], *aliases]:
            if name in index and name != pid:
                _error(
                    "/parameters/" + pid + "/name",
                    "name/alias occupies another parameter ID",
                )
        visit(pid)
    return resolved


def _table(table, path):
    _unique(table["rows"], "id", path + "/rows")
    native = {}
    for i, row in enumerate(table["rows"]):
        rp = f"{path}/rows/{i}"
        n = row["native"]
        c = row["canonical"]
        expected = {"code"} if table["key_kind"] == "code" else {"level_type", "level"}
        if set(n) != expected:
            _error(rp + "/native", "native key does not match key_kind")
        # canonical is either an entire vertical condition or a member number.
        if set(c) == {"member"}:
            normalized = c
        elif set(c) <= {"level_type", "level", "level_unit", "layer"} and (
            "level_type" in c or "layer" in c
        ):
            normalized = normalize_fixed(c, rp + "/canonical", allow_unbound=False)
            if (
                "layer" not in normalized
                and normalized.get("level_type")
                not in {
                    "surface",
                    "atmosphere_top",
                    "entire_atmosphere",
                    "mean_sea_level",
                }
                and "level" not in normalized
            ):
                _error(
                    rp + "/canonical/level",
                    "complete numeric vertical condition required",
                )
        else:
            _error(
                rp + "/canonical",
                "table row must have a complete vertical condition or only member",
            )
        key = canonical(n)
        if key in native and canonical(native[key]) != canonical(normalized):
            _error(
                rp + "/native",
                "same native key has conflicting canonical interpretations",
                "invalid_config",
                reason="conflicting_table_rows",
            )
        native[key] = normalized


def validate_resource(data, kind):
    if kind == "parameters":
        _effective_parameters(data)
    elif kind == "mappings":
        _unique(data["mappings"], "id", "/mappings")
        tables = _unique(data["tables"], "id", "/tables")
        for i, t in enumerate(data["tables"]):
            _normalize(t["applies_to"], f"/tables/{i}/applies_to", allow_unbound=True)
            _table(t, f"/tables/{i}")
        for i, m in enumerate(data["mappings"]):
            path = f"/mappings/{i}"
            if m["backend"] != data["backend"]:
                _error(path + "/backend", "mapping and resource backends differ")
            if "parameter" in m["when"] or "native" in m["when"]:
                _error(
                    path + "/when", "when contains only canonical business conditions"
                )
            _normalize(m["when"], path + "/when", allow_unbound=True)
            from .normalize import _normal_selector

            for key, value in m["native"]["match"].items():
                _normal_selector(
                    value,
                    "number" if isinstance(value, dict) else "literal",
                    path + "/native/match/" + key,
                )
            _normalize(m["applies_to"], path + "/applies_to", allow_unbound=True)
            for duty, rule in m["native"].get("translate", {}).items():
                normalized = validate_rule(
                    rule,
                    backend=m["backend"],
                    responsibility=duty,
                    path=path + "/native/translate/" + duty,
                )
                tid = normalized.get("config", {}).get("table")
                if tid is not None:
                    if tid not in tables:
                        _error(
                            path + "/native/translate/" + duty + "/config/table",
                            "missing shared table",
                            "invalid_config",
                            reason="missing_table",
                        )
                    for row in tables[tid]["rows"]:
                        if duty == "member" and set(row["canonical"]) != {"member"}:
                            _error(
                                path + "/native/translate/member",
                                "member table cannot repeat roles or vertical conditions",
                            )
                        if duty == "vertical" and "member" in row["canonical"]:
                            _error(
                                path + "/native/translate/vertical",
                                "vertical table cannot contain member rows",
                            )
    else:
        _unique(data["datasets"], "id", "/datasets")
        for di, d in enumerate(data["datasets"]):
            path = f"/datasets/{di}"
            seen = set()
            if d.get("execution", {}).get("max_expansion", 1) <= 0:
                _error(
                    path + "/execution/max_expansion",
                    "positive expansion limit required",
                )
            for pi, p in enumerate(d["products"]):
                pp = f"{path}/products/{pi}"
                key = (p["id"], p["version"])
                if key in seen:
                    _error(pp, "duplicate product ID/version")
                seen.add(key)
                _normalize(p["scope"], pp + "/scope", allow_unbound=True)
                for qi, q in enumerate(p["provides"]):
                    _normalize(q, f"{pp}/provides/{qi}", allow_unbound=True)
                _unique(p["populations"], "id", pp + "/populations")
                _unique(p["sources"], "id", pp + "/sources")
                _unique(p["methods"], "parameter_id", pp + "/methods")
                for pop in p["populations"]:
                    members = pop["members"]
                    if isinstance(members, dict):
                        r = members["range"]
                        if r["start"] > r["end"]:
                            _error(pp + "/populations", "population range is empty")
                    count = _count(members, "number")
                    if (
                        pop["missing"]["policy"] == "use_available"
                        and pop["missing"]["min_members"] > count
                    ):
                        _error(
                            pp + "/populations", "min_members exceeds the population"
                        )
                for source in p["sources"]:
                    if source["kind"] == "cmadaas":
                        try:
                            ZoneInfo(source["time_zone"])
                        except (ZoneInfoNotFoundError, ValueError) as exc:
                            raise QueryError(
                                "invalid_config",
                                pp + "/sources/time_zone",
                                "unknown IANA time zone",
                            ) from exc


@dataclass(frozen=True)
class ResourceSet:
    """Validated in-memory inputs; it does not find or load resources."""

    parameters: Mapping
    mappings: tuple
    catalog: Mapping | None
    effective: Mapping

    def parameter(self, name, *, candidates=None, conditions=None):
        index = self.effective
        if name in index:
            if candidates is not None and name not in candidates:
                _error(
                    "/parameter",
                    "parameter is outside the explicitly supplied domain",
                    "constraint_conflict",
                    reason="fixed_condition_conflict",
                )
            return thaw(index[name])
        matches = [
            p
            for p in index.values()
            if (candidates is None or p["parameter_id"] in candidates)
            and (name == p["name"] or name in p.get("aliases", ()))
        ]
        if len(matches) > 1 and conditions is not None:
            from .normalize import constrain_query

            compatible = []
            for candidate in matches:
                fixed = thaw(candidate.get("fixed_conditions", {}))
                if fixed.get("statistics", {}).get("over") == "ensemble" and {
                    "member",
                    "member_role",
                } & set(conditions):
                    continue
                try:
                    constrain_query(
                        conditions,
                        {k: v for k, v in fixed.items() if k != "statistics"},
                    )
                except QueryError as exc:
                    if exc.code in {"constraint_conflict", "invalid_query"}:
                        continue
                    raise
                compatible.append(candidate)
            if not compatible:
                _error(
                    "/parameter",
                    "no named candidate is compatible with the explicit conditions",
                    "constraint_conflict",
                    reason="fixed_condition_conflict",
                )
            matches = compatible
        if len(matches) != 1:
            raise QueryError(
                "invalid_query",
                "/parameter",
                "unknown or ambiguous parameter",
                {
                    "reason": "unknown_parameter"
                    if not matches
                    else "ambiguous_parameter",
                    "candidates": sorted(p["parameter_id"] for p in matches),
                },
            )
        return thaw(matches[0])


def _ancestor(index, parent, child):
    while "parent" in index[child]:
        child = index[child]["parent"]
        if child == parent:
            return True
    return False


def _translation_relationship(a, b, atables, btables):
    """Compare facts that finite configurations make statically decidable."""
    conflicts = False
    for duty in set(a) & set(b):
        ac = a[duty].get("config", {})
        bc = b[duty].get("config", {})
        if duty == "unit" and "source_unit" in ac and "source_unit" in bc:
            conflicts |= unit_info(ac["source_unit"]) != unit_info(bc["source_unit"])
        if "table" in ac and "table" in bc:
            at, bt = atables[ac["table"]], btables[bc["table"]]
            if _intersect(at["applies_to"], bt["applies_to"]) is None:
                return "disjoint"
            an = {
                canonical(r["native"]): normalize_fixed(r["canonical"])
                for r in at["rows"]
            }
            bn = {
                canonical(r["native"]): normalize_fixed(r["canonical"])
                for r in bt["rows"]
            }
            common = set(an) & set(bn)
            if at["key_kind"] == bt["key_kind"] and not common:
                return "disjoint"
            conflicts |= any(canonical(an[k]) != canonical(bn[k]) for k in common)
    return "conflict" if conflicts else "compatible"


def validate_resource_set(
    parameters,
    mappings=(),
    catalog=None,
    *,
    products=(),
    retired_ids=(),
    interface_contracts=None,
):
    """Validate internal references first; product-dependent completeness only on request.

    products is a sequence of exact (dataset, product, business-version) triples.
    interface_contracts, when supplied, is an explicit set of registered (id, version).
    """
    from .model import Catalog, Mappings, Parameters

    p = Parameters(parameters).to_dict()
    ms = tuple(Mappings(m).to_dict() for m in mappings)
    c = None if catalog is None else Catalog(catalog).to_dict()
    effective = _effective_parameters(p, retired_ids=retired_ids)
    seen_resources = set()
    all_mappings = []
    for resource in ms:
        key = resource["id"]
        if key in seen_resources:
            _error("/mappings", "duplicate effective mapping resource ID")
        seen_resources.add(key)
        tables = {t["id"]: t for t in resource["tables"]}
        for m in resource["mappings"]:
            if m["parameter_id"] not in effective:
                _error(
                    "/mappings/" + m["id"] + "/parameter_id",
                    "mapping refers to an absent parameter",
                )
            fixed = effective[m["parameter_id"]]["fixed_conditions"]
            conditions = {k: v for k, v in fixed.items() if k != "statistics"}
            when = _normalize(
                {"level_type": conditions["level_type"], **m["when"]}
                if "level_type" in conditions
                else m["when"],
                allow_unbound=True,
            )
            if _intersect(conditions, when) is None:
                _error(
                    "/mappings/" + m["id"] + "/when",
                    "mapping contradicts the effective parameter definition",
                )
            for duty, rule in m["native"].get("translate", {}).items():
                tid = rule.get("config", {}).get("table")
                if tid and duty == "vertical":
                    if not any(
                        _intersect(conditions, normalize_fixed(row["canonical"]))
                        is not None
                        for row in tables[tid]["rows"]
                    ):
                        _error(
                            "/mappings/" + m["id"] + "/native/translate/vertical",
                            "no table row is compatible with the fixed parameter conditions",
                        )
            all_mappings.append((m, tables))
    for i, (a, atables) in enumerate(all_mappings):
        for b, btables in all_mappings[i + 1 :]:
            if (
                a["backend"] != b["backend"]
                or _intersect(_normalize(a["applies_to"]), _normalize(b["applies_to"]))
                is None
                or _intersect(_normalize(a["when"]), _normalize(b["when"])) is None
                or _intersect(a["native"]["match"], b["native"]["match"], native=True)
                is None
            ):
                continue
            pa, pb = a["parameter_id"], b["parameter_id"]
            if (
                _intersect(
                    {
                        k: v
                        for k, v in effective[pa]["fixed_conditions"].items()
                        if k != "statistics"
                    },
                    {
                        k: v
                        for k, v in effective[pb]["fixed_conditions"].items()
                        if k != "statistics"
                    },
                )
                is None
            ):
                continue
            ta = {
                k: validate_rule(v, backend=a["backend"], responsibility=k)
                for k, v in a["native"].get("translate", {}).items()
            }
            tb = {
                k: validate_rule(v, backend=b["backend"], responsibility=k)
                for k, v in b["native"].get("translate", {}).items()
            }
            relationship = _translation_relationship(ta, tb, atables, btables)
            if relationship == "disjoint":
                continue
            if relationship == "conflict":
                _error(
                    "/mappings",
                    "overlapping mappings declare conflicting native facts",
                    "invalid_config",
                    reason="ambiguous_mapping",
                    mappings=[a["id"], b["id"]],
                )
            if pa == pb:
                continue
            if pa != pb and (
                _ancestor(effective, pa, pb) or _ancestor(effective, pb, pa)
            ):
                continue
            _error(
                "/mappings",
                "overlapping mappings have incompatible interpretations",
                "invalid_config",
                reason="ambiguous_mapping",
                mappings=[a["id"], b["id"]],
            )
    result = ResourceSet(
        freeze(p),
        tuple(freeze(m) for m in ms),
        None if c is None else freeze(c),
        freeze(effective),
    )
    for product in products:
        validate_product(result, *product, interface_contracts=interface_contracts)
    return result


def validate_product(resources, dataset, product, version, *, interface_contracts=None):
    """Resolve one exact product context, without probing any source."""
    if resources.catalog is None:
        _error(
            "/catalog",
            "product context needs a catalog",
            "invalid_config",
            reason="missing_context",
        )
    datasets = [d for d in resources.catalog["datasets"] if d["id"] == dataset]
    products = (
        []
        if not datasets
        else [
            p
            for p in datasets[0]["products"]
            if p["id"] == product and p["version"] == version
        ]
    )
    if len(products) != 1:
        _error(
            "/catalog",
            "exact product/version is absent",
            "invalid_config",
            reason="missing_context",
        )
    p = thaw(products[0])
    pops = {pop["id"]: pop for pop in p["populations"]}
    methods = {m["parameter_id"]: m["method"] for m in p["methods"]}
    if interface_contracts is not None:
        for source in p["sources"]:
            if (
                source["kind"] == "cmadaas"
                and (
                    source["interface_contract"]["id"],
                    source["interface_contract"]["version"],
                )
                not in interface_contracts
            ):
                _error(
                    "/catalog/sources/interface_contract",
                    "exact interface contract is not registered",
                    "invalid_config",
                    reason="missing_context",
                )
    selected = set()
    for query in p["provides"]:
        parameter = query.get("parameter")
        if parameter is None:
            continue
        for name in parameter if isinstance(parameter, list) else [parameter]:
            record = resources.parameter(name)
            selected.add(record["parameter_id"])
    for pid in methods:
        if pid not in resources.effective:
            _error("/catalog/methods", "method references an absent parameter")
    checked = set()

    def concrete(pid):
        if pid in checked:
            return
        checked.add(pid)
        record = thaw(resources.effective[pid])
        fixed = record.get("fixed_conditions", {})
        if "unit" not in record:
            _error(
                "/parameters/" + pid + "/unit",
                "concrete output requires an effective unit",
                "invalid_config",
                reason="missing_context",
            )
        stats = fixed.get("statistics")
        if stats:
            stats = plain(stats)
            if pid in methods:
                stats["method"] = _merge(
                    stats.get("method", {}), methods[pid], "/catalog/methods"
                )
            stats = normalize_statistics(
                stats,
                "/parameters/" + pid + "/fixed_conditions/statistics",
                complete=True,
            )
            if stats.get("over") == "ensemble":
                ref = stats["population"]["ref"]
                if ref not in pops:
                    _error(
                        "/catalog/populations",
                        "missing product-local population",
                        "invalid_config",
                        reason="missing_context",
                    )
                target = stats["of"]["parameter_id"]
                concrete(target)
                if stats["operation"] == "probability":
                    if unit_info(record["unit"])[0] != "ratio":
                        _error(
                            "/parameters/" + pid + "/unit",
                            "probability output requires a ratio/percentage unit",
                        )
                elif (
                    unit_info(record["unit"])[0]
                    != unit_info(resources.effective[target]["unit"])[0]
                ):
                    _error(
                        "/parameters/" + pid + "/unit",
                        "statistical output has incompatible physical units",
                    )

    for pid in selected:
        concrete(pid)
    prepare_queries(p["provides"], resources, scope=p["scope"])
    for resource in resources.mappings:
        for m in resource["mappings"]:
            app = thaw(m["applies_to"])
            if not all(
                k not in app or _contains(app[k], v, "literal")
                for k, v in [
                    ("dataset", dataset),
                    ("product", product),
                    ("product_version", version),
                ]
            ):
                continue
            # Product references are checked only in the requested context. A
            # partial catalog is not required to cover every mapping in the DB.
            if m["parameter_id"] not in resources.effective:
                _error("/mappings", "missing parameter")
    return freeze(p)


def prepare_queries(queries, resources, *, scope=None, parameter_ids=None):
    """Resolve explicit names and fixed definitions without consulting data files.

    parameter_ids may restrict name resolution to an I04-supplied product domain.
    The resulting branches still use the single Query value model.
    """
    from .model import Query
    from .normalize import constrain_query, normalize_query

    result = {}
    for value in queries:
        raw = Query(value).to_dict()
        names = raw.get("parameter")
        if names is None:
            q = (
                constrain_query(raw, scope)
                if scope is not None
                else normalize_query(raw)
            )
            result[canonical(q)] = q
            continue
        for name in names if isinstance(names, list) else [names]:
            p = resources.parameter(name, candidates=parameter_ids, conditions=raw)
            fixed = p.get("fixed_conditions", {})
            stats = fixed.get("statistics", {})
            if stats.get("over") == "ensemble" and {"member", "member_role"} & set(raw):
                _error(
                    "/member",
                    "individual member selection is incompatible with a statistical product",
                    "constraint_conflict",
                    reason="fixed_condition_conflict",
                )
            constraints = {k: v for k, v in fixed.items() if k != "statistics"}
            q = constrain_query({**raw, "parameter": p["parameter_id"]}, constraints)
            if scope is not None:
                q = constrain_query(q, scope)
            # A descendant may supply a more specific identity only when its
            # complete fixed meaning is already implied by the explicit Query.
            equivalent = []
            for pid, candidate in resources.effective.items():
                if pid == p["parameter_id"] or not _ancestor(
                    resources.effective, p["parameter_id"], pid
                ):
                    continue
                if parameter_ids is not None and pid not in parameter_ids:
                    continue
                cf = thaw(candidate.get("fixed_conditions", {}))
                if canonical(cf.get("statistics", {})) != canonical(stats):
                    continue
                try:
                    refined = constrain_query(
                        q, {k: v for k, v in cf.items() if k != "statistics"}
                    )
                except QueryError as exc:
                    if exc.code == "constraint_conflict":
                        continue
                    raise
                if canonical(refined) == canonical(q):
                    equivalent.append(pid)
            deepest = [
                pid
                for pid in equivalent
                if not any(
                    _ancestor(resources.effective, pid, other) for other in equivalent
                )
            ]
            if len(deepest) > 1:
                _error(
                    "/parameter",
                    "equivalent identities are ambiguous",
                    "invalid_query",
                    reason="ambiguous_parameter",
                    candidates=sorted(deepest),
                )
            if deepest:
                q = Query({**q.to_dict(), "parameter": deepest[0]})
            result[canonical(q)] = q
    return tuple(result[k] for k in sorted(result))


def effective_parameters(resource, *, retired_ids=()):
    from .model import Parameters

    return _effective_parameters(
        Parameters(resource).to_dict(), retired_ids=retired_ids
    )


# Contract targets only. This is not an adapter capability/support assertion.
GRIB_TEMPLATE_FAMILIES = freeze(
    {
        "general": [0, 8],
        "individual_member": [1, 11],
        "derived_ensemble": [2, 12],
        "probability": [5, 9],
        "quantile": [6, 10],
    }
)
