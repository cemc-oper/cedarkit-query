"""Business parameter binding against an explicit in-memory definition set."""

from ._json import canonical
from .errors import QueryError
from .model import Query
from .normalize import constrain_query, normalize_query


def _resolve(name, parameters):
    if name in parameters:
        return name
    matches = sorted(
        pid
        for pid, definition in parameters.items()
        if name == definition["name"] or name in definition.get("aliases", ())
    )
    if len(matches) != 1:
        raise QueryError(
            "invalid_query",
            "/parameter",
            "ambiguous parameter" if matches else "unknown parameter",
            {
                "reason": "ambiguous_parameter" if matches else "unknown_parameter",
                "candidates": matches,
            },
        )
    return matches[0]


def _ancestors(pid, parameters):
    while (pid := parameters[pid].get("parent")) is not None:
        yield pid


def _query_conditions(fixed):
    return {key: value for key, value in fixed.items() if key != "statistics"}


def _equivalent_variant(query, pid, parameters, statistics):
    equivalent = set()
    for candidate, definition in parameters.items():
        if pid not in _ancestors(candidate, parameters):
            continue
        fixed = definition.get("fixed_conditions", {})
        if fixed.get("statistics") != statistics:
            continue
        try:
            refined = constrain_query(query, _query_conditions(fixed))
        except QueryError as exc:
            if exc.code in {"constraint_conflict", "invalid_query"}:
                continue
            raise
        if refined == query:
            equivalent.add(candidate)
    ancestors = {
        parent for candidate in equivalent for parent in _ancestors(candidate, parameters)
    }
    deepest = sorted(equivalent - ancestors)
    if len(deepest) > 1:
        raise QueryError(
            "invalid_query",
            "/parameter",
            "equivalent parameter variants are ambiguous",
            {"reason": "ambiguous_parameter", "candidates": deepest},
        )
    return deepest[0] if deepest else pid


def prepare_queries(queries, parameters):
    """Resolve names and fixed conditions before selection algebra or identity.

    Parameters is the caller's validated index by stable ID. Its records carry
    names, optional aliases and parent IDs, and effective fixed conditions
    with inheritance already applied. No definitions or data are loaded here.
    """
    prepared = {}
    for value in queries:
        raw = Query(value).to_dict()
        if "parameter" not in raw:
            query = normalize_query(raw)
            prepared[canonical(query)] = query
            continue
        names = raw["parameter"]
        for name in names if isinstance(names, list) else [names]:
            pid = _resolve(name, parameters)
            fixed = parameters[pid].get("fixed_conditions", {})
            statistics = fixed.get("statistics")
            if statistics and statistics.get("over") == "ensemble" and (
                "member" in raw or "member_role" in raw
            ):
                field = "member" if "member" in raw else "member_role"
                raise QueryError(
                    "constraint_conflict",
                    "/" + field,
                    "individual member selection conflicts with an ensemble statistic",
                    {"reason": "fixed_condition_conflict"},
                )
            bound = constrain_query({**raw, "parameter": pid}, _query_conditions(fixed))
            resolved = _equivalent_variant(bound, pid, parameters, statistics)
            result = Query({**bound.to_dict(), "parameter": resolved})
            prepared[canonical(result)] = result
    return tuple(prepared[key] for key in sorted(prepared))
