from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from cedarkit_query import (
    CompletenessError,
    ErrorValue,
    Query,
    QueryError,
    QueryExplanation,
    QueryReport,
    Request,
    SourceError,
    get_schema,
    load_value,
)


def request(query=None):
    return {
        "api_version": "cedarkit.request/v1",
        "source": {"kind": "file", "path": "data.grib2"},
        "queries": [{} if query is None else query],
    }


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"parameter": ["t", "cedarkit.t"]},
        {"native": {"grib": {"edition": 2, "parameterNumber": [0, 1]}}},
        {"layer": {"bottom": {"type": "surface"}, "top": {"type": "atmosphere_top"}}},
        {"level_type": "pressure", "level": {"range": {"start": 500, "end": 850}}},
        {"time": "2026-10-27T08:00:00.123456789+08:00", "step": "PT0.5S"},
        {"member": [0, 30], "member_role": ["control", "perturbation"]},
    ],
)
def test_three_input_forms_accept_same_query(data):
    q = Query(data)
    assert load_value(json.dumps(data), "query") == q
    assert load_value(yaml.safe_dump(data), "query") == q
    assert Query(**data) == q


@pytest.mark.parametrize(
    "data,path",
    [
        ({"parameter": None}, "/parameter"),
        ({"parameter": []}, "/parameter"),
        ({"parameter": {"discipline": 0}}, "/parameter"),
        ({"parameter": " t"}, "/parameter"),
        ({"level": 1, "layer": {"bottom": 0, "top": 1}}, ""),
        ({"member": True}, "/member"),
        ({"member": 1.0}, "/member"),
        ({"member": -1}, "/member"),
        ({"with_index": False}, "/with_index"),
        ({"api_version": "cedarkit.query/v1"}, "/api_version"),
        ({"step": {"range": {"start": "PT6H", "end": "PT0H"}}}, "/step/range/end"),
        ({"level_type": "pressure", "level": 850, "level_unit": "m"}, "/level_unit"),
        ({"level_type": "surface", "level": 0}, "/level"),
        ({"level_type": "model_level", "level": 1.1}, "/level"),
        ({"level_type": "model_level", "level_unit": "m"}, "/level_unit"),
        (
            {
                "layer": {
                    "bottom": {"type": "surface", "value": 0},
                    "top": {"type": "atmosphere_top"},
                }
            },
            "/layer/0/bottom",
        ),
        (
            {"level_type": "depth_below_land", "layer": {"bottom": 0.1, "top": 0.4}},
            "/layer",
        ),
    ],
)
def test_three_input_forms_reject_same_query(data, path):
    errors = []
    for value in (data, json.dumps(data), yaml.safe_dump(data)):
        with pytest.raises(QueryError) as error:
            load_value(value, "query")
        errors.append((error.value.code, error.value.path))
    assert errors == [("invalid_query", path)] * 3


@pytest.mark.parametrize(
    "text,format",
    [
        ('{"parameter":"t","parameter":"u"}', "json"),
        ("parameter: t\nparameter: u\n", "yaml"),
        ("native:\n  grib:\n    edition: 2\n    edition: 1\n", "yaml"),
        ("level: .nan", "yaml"),
        ('{"level":NaN}', "json"),
        ("a: &a {a: *a}", "yaml"),
        ("!!python/object:builtins.object {}", "yaml"),
    ],
)
def test_duplicate_unsafe_and_nonfinite_input(text, format):
    with pytest.raises(QueryError):
        load_value(text, "query", format=format)


def test_immutable_values_and_independent_copy():
    raw = {"parameter": ["t", "u"]}
    q = Query(raw)
    raw["parameter"].append("v")
    assert q.parameter == ("t", "u")
    copy = q.to_dict()
    copy["parameter"].append("v")
    assert q.parameter == ("t", "u")
    with pytest.raises((AttributeError, TypeError)):
        q.parameter[0] = "v"
    with pytest.raises(AttributeError):
        q.parameter = "v"


@pytest.mark.parametrize(
    "change,code,path",
    [
        ({"action": "read"}, "invalid_request", "/action"),
        ({"queries": []}, "invalid_request", "/queries"),
        ({"query": {}}, "invalid_request", "/queries"),
        ({"api_version": "cedarkit.request/v2"}, "unsupported_version", "/api_version"),
        ({"api_version": None}, "invalid_request", "/api_version"),
        (
            {"context": {"execution": {"max_expansion": True}}},
            "invalid_config",
            "/context/execution/max_expansion",
        ),
        (
            {"context": {"execution": {"max_expansion": 0}}},
            "invalid_config",
            "/context/execution/max_expansion",
        ),
        (
            {"source": {"kind": "file", "path": "x", "directory": ".", "glob": "*"}},
            "invalid_config",
            None,
        ),
        ({"source": {"kind": "file", "path": []}}, "invalid_config", "/source/path"),
        ({"source": {"kind": "file", "directory": "."}}, "invalid_config", None),
        (
            {"source": {"kind": "cmadaas", "dataset": "x", "client": {}}},
            "invalid_config",
            "/source/client",
        ),
    ],
)
def test_request_rejects_old_or_invalid_structure(change, code, path):
    data = request()
    data.update(change)
    if "query" in change:
        data.pop("queries")
    with pytest.raises(QueryError) as error:
        Request.from_dict(data)
    assert error.value.code == code
    if path is not None:
        assert error.value.path == path


def test_request_paths_no_discovery_or_external_context_read(tmp_path, monkeypatch):
    data = request()
    data["source"] = {
        "kind": "file",
        "directory": "./data/../grib",
        "glob": ["*.grb", "**/*.grb2"],
    }
    data["context"] = {
        "parameter_registry": {"path": "./resources/p.json"},
        "mappings": {"cmadaas": {"builtin": "default"}},
        "execution": {"max_expansion": 7},
    }
    r = Request.from_dict(data, base_dir=tmp_path)
    assert r.source["directory"] == str(tmp_path / "grib")
    assert r.source["glob"] == ("*.grb", "**/*.grb2")
    assert r.context["parameter_registry"]["path"] == str(tmp_path / "resources/p.json")
    assert r.completeness is None and "completeness" not in r.to_dict()
    for ext in ["json", "yaml", "yml"]:
        dest = tmp_path / f"job.{ext}"
        r.dump(dest)
        assert Request.load(dest) == r
    data["context"] = "absent/context.yaml"
    r = Request.from_dict(data, base_dir=tmp_path)
    assert r.context == str(tmp_path / "absent/context.yaml")
    original = Path.cwd()
    monkeypatch.chdir(tmp_path)
    current = Request.from_dict(request())
    monkeypatch.chdir(original)
    assert current.source["path"] == str(tmp_path / "data.grib2")
    assert (
        "read" not in dir(current)
        and "execute" not in dir(current)
        and "validate" not in dir(current)
    )


def test_external_client_injected_later_is_not_serialized():
    r = Request.from_dict(
        {
            "api_version": "cedarkit.request/v1",
            "source": {"kind": "cmadaas", "dataset": "example"},
            "queries": [{}],
        }
    )
    assert "config" not in r.source
    assert "client" not in r.to_dict()["source"]


def test_error_families_serialization_and_report(report):
    q = QueryError(
        "invalid_query",
        "/queries/0",
        "bad input",
        {"origins": [{"step_index": 0, "query_index": 0}]},
    )
    s = SourceError("timeout", message="timed out")
    assert not isinstance(s, QueryError) and isinstance(s, RuntimeError)
    for error in (q, s):
        assert "report" not in error.to_dict()
        assert ErrorValue(error.to_dict()).code == error.code
    with pytest.raises(TypeError):
        q.context["new"] = 1
    clone = q.to_dict()
    clone["context"]["origins"][0]["query_index"] = 9
    assert q.context["origins"][0]["query_index"] == 0
    report["overall"]["status"] = "missing"
    report["overall"]["satisfied_count"] = 0
    report["overall"]["missing"] = [
        {"selection": {"parameter": "t"}, "query_indexes": [0]}
    ]
    report["queries"][0]["summary"] = deepcopy(report["overall"])
    exc = CompletenessError("missing_fields", report=report)
    assert isinstance(exc, QueryError) and isinstance(exc.report, QueryReport)
    assert isinstance(exc.to_dict()["report"], dict)
    assert ErrorValue(exc.to_dict()).report["schema_version"] == 1
    changed = exc.to_dict()
    changed["report"]["overall"]["field_count"] = 99
    assert exc.report.overall["field_count"] == 1
    with pytest.raises(AttributeError):
        exc.report = None
    with pytest.raises(ValueError):
        QueryError("missing_fields")
    with pytest.raises(QueryError):
        ErrorValue({**q.to_dict(), "report": report})


@pytest.mark.parametrize(
    "reason",
    [
        "missing_native_metadata",
        "unmapped_native_value",
        "invalid_vertical_metadata",
        "inconsistent_native_metadata",
        "fixed_definition_mismatch",
        "invalid_time_metadata",
        "invalid_member_metadata",
        "invalid_statistics_metadata",
        "unsupported_native_encoding",
    ],
)
def test_registered_metadata_reasons(reason):
    error = SourceError("metadata_interpretation_failed", context={"reason": reason})
    assert ErrorValue(error.to_dict()).context["reason"] == reason


def test_unknown_versions_and_invalid_reports(report):
    with pytest.raises(QueryError) as e:
        QueryReport({**report, "schema_version": 2})
    assert e.value.code == "unsupported_version"
    with pytest.raises(QueryError):
        QueryReport({**report, "schema_version": True})
    with pytest.raises(QueryError):
        ErrorValue(
            {
                "api_version": "cedarkit.error/v1",
                "code": "missing_fields",
                "path": "",
                "message": "missing",
                "context": {},
            }
        )
    with pytest.raises(ValueError):
        SourceError("metadata_interpretation_failed", context={"reason": "unknown"})
    with pytest.raises(QueryError):
        QueryReport({**report, "translations": []})
    report["translations"][0]["branches"] = []
    with pytest.raises(QueryError):
        QueryReport(report)
    report["translations"][0]["coverage"]["status"] = "pending"
    parsed = QueryReport(report)
    assert QueryReport.from_json(parsed.to_json()) == parsed


def test_report_branch_states_and_basis(report):
    branch = report["translations"][0]["branches"][0]
    branch["execution"] = {"status": "stopped_early", "reason": "first_match_selected"}
    report["overall"].update(
        status="unchecked",
        expected_count=None,
        satisfied_count=None,
        reasons=[
            {
                "code": "selection_not_exhausted",
                "message": "first stopped",
                "query_indexes": [0],
            }
        ],
    )
    report["queries"][0]["summary"] = deepcopy(report["overall"])
    assert QueryReport(report).overall["status"] == "unchecked"
    branch["basis"] = [
        {
            "kind": "request_association",
            "contract": {"id": "synthetic.single", "version": 1},
            "fields": ["time", "step", "level"],
        }
    ]
    assert (
        QueryReport(report).translations[0]["branches"][0]["basis"][0]["kind"]
        == "request_association"
    )
    branch["execution"]["reason"] = "timeout"
    with pytest.raises(QueryError):
        QueryReport(report)


def test_explanation_is_readonly_and_cannot_claim_execution(report):
    branch = deepcopy(report["translations"][0]["branches"][0])
    branch.pop("execution")
    origins = [{"step_index": 0, "query_index": 0}]
    effective = [{"index": 0, "query": {}, "origins": origins}]
    data = {
        "effective_queries": effective,
        "context": {},
        "steps": [
            {
                "index": 0,
                "input_queries": [{}],
                "effective_queries": effective,
                "changes": [],
            }
        ],
        "analysis": {
            "expansion_count": 1,
            "expected_count": None,
            "checks": [
                {
                    "code": "context_resolved",
                    "status": "pending",
                    "message": "needs binding",
                }
            ],
            "translations": [
                {
                    "query_index": 0,
                    "coverage": {"status": "pending", "message": "needs binding"},
                    "branches": [branch],
                    "checks": [],
                }
            ],
        },
    }
    x = QueryExplanation(data)
    x.to_dict()["analysis"]["checks"].clear()
    assert len(x.analysis["checks"]) == 1
    branch["execution"] = {"status": "exhausted"}
    with pytest.raises(QueryError):
        QueryExplanation(data)


def test_schema_resources_and_light_import_boundary():
    import subprocess, sys
    from jsonschema import Draft202012Validator

    for kind in [
        "common",
        "query",
        "context",
        "request",
        "report",
        "explanation",
        "error",
        "parameters",
        "mappings",
        "catalog",
    ]:
        schema = get_schema(kind)
        Draft202012Validator.check_schema(schema)
        schema["title"] = "changed"
        assert get_schema(kind)["title"] != "changed"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cedarkit_query,sys; assert not ({'reki','eccodes','xarray','nuwe_cmadaas','sqlite3'} & sys.modules.keys())",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    import cedarkit_query as cq

    for old in [
        "FieldQuery",
        "SourceSpec",
        "RequestList",
        "Binding",
        "Response",
        "Transform",
        "parse_text_request",
        "expand_request",
    ]:
        assert not hasattr(cq, old)


def test_external_context_uses_its_own_document_base(tmp_path):
    from cedarkit_query import load_value

    request_dir = tmp_path / "jobs"
    request_dir.mkdir()
    context_dir = tmp_path / "configuration"
    context_dir.mkdir()
    config = context_dir / "context.yaml"
    config.write_text("parameter_registry:\n  path: ../definitions/parameters.json\n")
    data = request()
    data["context"] = "../configuration/context.yaml"
    path = request_dir / "job.json"
    path.write_text(json.dumps(data))
    r = Request.load(path)
    assert r.context == str(config)
    c = load_value(config.read_text(), "context", base_dir=config.parent)
    assert c.parameter_registry["path"] == str(tmp_path / "definitions/parameters.json")


def test_null_query_is_not_the_empty_query_and_runtime_objects_are_not_serialized():
    assert Query().to_dict() == {}
    for value in [None, "null", "null\n"]:
        with pytest.raises(QueryError):
            load_value(value, "query")

    class RuntimeClient:
        called = False

        def to_dict(self):
            self.called = True
            return {}

    client = RuntimeClient()
    with pytest.raises(QueryError):
        Query(client)
    assert not client.called
