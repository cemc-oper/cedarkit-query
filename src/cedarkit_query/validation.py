"""Semantic checks shared by all declaration formats."""

from .errors import METADATA_REASONS, QueryError
from .normalize import _normalize


def _error(path, message, code="invalid_config", **context):
    raise QueryError(code, path, message, context or None)


def validate_semantics(data, kind):
    if kind == "query":
        _normalize(data, allow_unbound=True)
    elif kind == "request":
        for i, query in enumerate(data["queries"]):
            _normalize(query, f"/queries/{i}", allow_unbound=True)
        if isinstance(data.get("context"), dict):
            _context(data["context"], "/context")
    elif kind == "context":
        _context(data, "")
    elif kind in {"parameters", "mappings", "catalog"}:
        from .resources import validate_resource

        try:
            validate_resource(data, kind)
        except QueryError as exc:
            if exc.code == "invalid_query":
                raise QueryError(
                    "invalid_config", exc.path, exc.message, dict(exc.context)
                ) from exc
            raise
    elif kind in {"report", "explanation"}:
        _snapshot(data, kind)
    elif kind == "error":
        code, context = data["code"], data["context"]
        if (
            code == "metadata_interpretation_failed"
            and context.get("reason") not in METADATA_REASONS
        ):
            _error("/context/reason", "registered metadata reason required")
        if code == "incomplete_response" and context.get("reason") not in {
            "response_truncated",
            "pagination_incomplete",
        }:
            _error("/context/reason", "registered incomplete-response reason required")
        if "report" in data:
            _snapshot(data["report"], "report")
            expected = "missing" if code == "missing_fields" else "unchecked"
            if data["report"]["overall"]["status"] != expected:
                _error(
                    "/report/overall/status", "error code and report status disagree"
                )


def _context(data, path):
    if "scope" in data:
        try:
            _normalize(data["scope"], path + "/scope", allow_unbound=True)
        except QueryError as exc:
            raise QueryError(
                "invalid_config", exc.path, exc.message, dict(exc.context)
            ) from exc
    refs = [(k, data[k]) for k in ("parameter_registry", "catalog") if k in data]
    refs.extend(("mappings/" + k, v) for k, v in data.get("mappings", {}).items())
    for key, ref in refs:
        if "path" in ref:
            value = ref["path"]
            if "://" in value or any(c in value for c in "*?["):
                _error(path + "/" + key + "/path", "resource must name one local file")


def _snapshot(data, kind):
    report = kind == "report"
    queries = data["queries"] if report else data["effective_queries"]
    expected_indexes = list(range(len(queries)))
    if [q["index"] for q in queries] != expected_indexes:
        _error(
            "/queries" if report else "/effective_queries",
            "indexes must be contiguous snapshot-local indexes",
        )
    for i, q in enumerate(queries):
        _normalize(q["query"], allow_unbound=True)
        origins = q["origins"]
        if list(origins) != sorted(
            origins, key=lambda o: (o["step_index"], o["query_index"])
        ):
            _error(f"/queries/{i}/origins", "origins must be unique and sorted")
    translations = data["translations"] if report else data["analysis"]["translations"]
    if sorted(t["query_index"] for t in translations) != expected_indexes:
        _error(
            "/translations",
            "exactly one translation is required for each effective Query",
        )
    definitions = {
        (d["api_version"].split("/")[0].split(".")[-1], d["id"])
        for d in data["context"].get("definitions", [])
    }
    for ti, translation in enumerate(translations):
        branches = translation["branches"]
        if not branches and translation["coverage"]["status"] != "pending":
            _error(
                f"/translations/{ti}/coverage",
                "empty branches require pending coverage",
            )
        if [b["index"] for b in branches] != list(range(len(branches))):
            _error(f"/translations/{ti}/branches", "branch indexes must be contiguous")
        for bi, branch in enumerate(branches):
            residual = dict(branch["residual"])
            residual.pop("statistics", None)
            _normalize(residual, allow_unbound=True)
            if "statistics" in branch["residual"]:
                from .resources import normalize_statistics

                normalize_statistics(
                    branch["residual"]["statistics"],
                    f"/translations/{ti}/branches/{bi}/residual/statistics",
                )
            for basis in branch["basis"]:
                if (
                    "resource" in basis
                    and (basis["resource"]["kind"], basis["resource"]["id"])
                    not in definitions
                ):
                    _error(
                        f"/translations/{ti}/branches/{bi}/basis",
                        "basis resource is absent from context.definitions",
                    )
                if "row" in basis and ("table" not in basis or "resource" not in basis):
                    _error(
                        f"/translations/{ti}/branches/{bi}/basis",
                        "table row needs table and resource references",
                    )
            if report:
                execution = branch["execution"]
                if (
                    execution["status"] in {"stopped_early", "not_started"}
                    and execution.get("reason") != "first_match_selected"
                ):
                    _error(
                        f"/translations/{ti}/branches/{bi}/execution/reason",
                        "normal early stopping requires a registered reason",
                    )
    if report:
        summaries = [data["overall"]] + [q["summary"] for q in queries]
        for summary in summaries:
            a, b = summary["expected_count"], summary["satisfied_count"]
            if (a is None) != (b is None) or (a is not None and b > a):
                _error("/overall", "expected and satisfied counts are inconsistent")
            if summary["status"] == "complete" and (
                a is None or a != b or summary["missing"] or summary["reasons"]
            ):
                _error(
                    "/overall",
                    "complete requires known equal counts and no missing fields/reasons",
                )
            if summary["status"] == "missing" and not summary["missing"]:
                _error("/overall/missing", "missing status requires missing identities")
            if summary["missing"] and summary["status"] != "missing":
                _error(
                    "/overall/status", "known missing identities require missing status"
                )
            for item in summary["missing"] + summary["reasons"]:
                if not set(item["query_indexes"]) <= set(expected_indexes):
                    _error("/overall", "summary refers to an unknown Query index")
            for item in summary["missing"]:
                _normalize(item["selection"], allow_unbound=True)
        statuses = [q["summary"]["status"] for q in queries]
        overall = (
            "missing"
            if "missing" in statuses
            else "unchecked"
            if "unchecked" in statuses
            else "complete"
        )
        if data["overall"]["status"] != overall:
            _error(
                "/overall/status",
                "overall status must aggregate all effective Query summaries",
            )
    else:
        steps = data["steps"]
        if [s["index"] for s in steps] != list(range(len(steps))):
            _error("/steps", "step indexes must be contiguous")
        for q in queries:
            for origin in q["origins"]:
                si, qi = origin["step_index"], origin["query_index"]
                if si >= len(steps) or qi >= len(steps[si]["input_queries"]):
                    _error(
                        "/effective_queries", "origin refers to an absent input Query"
                    )
        if not queries and (
            data["analysis"]["expansion_count"] not in (0, None)
            or data["analysis"]["expected_count"] not in (0, None)
        ):
            _error("/analysis", "known empty selection cannot have positive counts")
