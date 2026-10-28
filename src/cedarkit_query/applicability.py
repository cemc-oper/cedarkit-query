"""Static product/mapping relationships, without backend execution or data I/O."""

from ._json import freeze, thaw
from .errors import QueryError
from .normalize import expand_queries, intersect_scopes
from .resources import prepare_queries, validate_product

# An internal proof budget, not a public execution option or product size limit.
_STATIC_CHECK_LIMIT = 10000


def _identity_matches(applies, identity):
    return all(
        key not in applies
        or value in ((applies[key],) if isinstance(applies[key], str) else applies[key])
        for key, value in identity.items()
    )


def analyze_product_mappings(resources, dataset, product, version, *, scope=None):
    """Describe exact-parameter coverage of paired provides for supplied backends.

    covered means the supplied predicates cover the declared domain; it never
    means that native translation, interface association or source availability
    has been verified. Parent mappings are not silently inherited. Missing axes,
    continuous partitions and large domains may leave coverage undetermined.
    Resources for other products remain legal: only actual intersections count.
    """
    selected = validate_product(resources, dataset, product, version)
    bound = (
        selected["scope"]
        if scope is None
        else intersect_scopes(selected["scope"], scope)
    )
    if bound is None:
        raise QueryError(
            "constraint_conflict",
            "/context/scope",
            "product and context scopes are disjoint",
        )
    identity = {"dataset": dataset, "product": product, "product_version": version}
    rows = []
    coverage = []
    for resource in resources.mappings:
        candidates = []
        for row in resource["mappings"]:
            matches = _identity_matches(row["applies_to"], identity)
            predicate = intersect_scopes(
                row["when"],
                {"time": row["applies_to"]["time"]}
                if "time" in row["applies_to"]
                else {},
            )
            entry = {
                "resource_id": resource["id"],
                "mapping_id": row["id"],
                "backend": resource["backend"],
                "parameter_id": row["parameter_id"],
                "product_context_matches": matches,
                "applies_to": thaw(row["applies_to"]),
                "when": thaw(row["when"]),
                "provided_indexes": [],
                "translation_coverage": "pending_I06",
            }
            rows.append(entry)
            if matches and predicate is not None:
                candidates.append((row, predicate, entry))
        for index, provided in enumerate(selected["provides"]):
            for prepared in prepare_queries(
                [provided], resources, scope=selected["scope"]
            ):
                branch = intersect_scopes(prepared, bound)
                if branch is None:
                    continue
                parameter = branch.to_dict().get("parameter")
                overlaps = []
                for row, predicate, entry in candidates:
                    if parameter is not None and row["parameter_id"] == parameter:
                        overlap = intersect_scopes(branch, predicate)
                        if overlap is not None:
                            overlaps.append((row, predicate))
                            if index not in entry["provided_indexes"]:
                                entry["provided_indexes"].append(index)
                status, reason = "missing", "no_applicable_mapping"
                checked, uncovered = 0, 0
                example = None
                if parameter is None:
                    status, reason = "undetermined", "no_explicit_parameter_domain"
                elif overlaps:
                    if any(
                        intersect_scopes(branch, pred) == branch for _, pred in overlaps
                    ):
                        status, reason = "covered", "single_predicate_covers_domain"
                    else:
                        try:
                            cells = expand_queries(
                                [branch], max_expansion=_STATIC_CHECK_LIMIT
                            )
                        except QueryError as exc:
                            if exc.code != "expansion_limit_exceeded":
                                raise
                            cells = None
                            status, reason = (
                                "undetermined",
                                "static_check_budget_exceeded",
                            )
                        if cells is not None:
                            partial = False
                            for cell in cells:
                                checked += 1
                                intersections = [
                                    intersect_scopes(cell, pred) for _, pred in overlaps
                                ]
                                if not any(q == cell for q in intersections):
                                    if any(q is not None for q in intersections):
                                        partial = True
                                    else:
                                        uncovered += 1
                                        if example is None:
                                            example = cell.to_dict()
                            if uncovered:
                                status, reason = "partial", "uncovered_declared_choices"
                            elif partial:
                                status, reason = (
                                    "undetermined",
                                    "predicate_union_not_proven",
                                )
                            else:
                                status, reason = (
                                    "covered",
                                    "finite_predicate_union_covers_domain",
                                )
                coverage.append(
                    {
                        "resource_id": resource["id"],
                        "backend": resource["backend"],
                        "provided_index": index,
                        "parameter_id": parameter,
                        "static_status": status,
                        "reason": reason,
                        "mapping_ids": [r["id"] for r, _ in overlaps],
                        "checked_cell_count": checked,
                        "uncovered_cell_count": uncovered,
                        "uncovered_example": example,
                        "translation_coverage": "pending_I06",
                    }
                )
    return freeze(
        {
            "rows": rows,
            "provides": coverage,
            "mapping_resources_present": len(resources.mappings),
            "translation_coverage": "pending_I06",
            "empty_provides": not selected["provides"],
        }
    )
