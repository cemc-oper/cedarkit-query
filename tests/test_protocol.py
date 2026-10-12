"""Language-neutral vectors and strict loading behavior."""

import copy
import json
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from cedarkit_query import (
    QueryError,
    Request,
    RequestList,
    dump_value,
    get_schema,
    load_request,
    load_value,
    validate_value,
)

VECTORS = Path(__file__).parent / "vectors" / "v1"
VALID = json.loads((VECTORS / "valid.json").read_text())
INVALID = json.loads((VECTORS / "invalid.json").read_text())
KINDS = ("request", "request-list", "query", "binding", "transform", "data-reference", "response", "error")


@pytest.mark.parametrize("kind", KINDS)
def test_packaged_schema_is_valid_and_available(kind):
    schema = get_schema(kind)
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == f"urn:cedarkit:query:{kind}:v1"
    resource = files("cedarkit_query").joinpath("schemas", f"{kind}-v1.schema.json")
    assert json.loads(resource.read_text()) == schema


@pytest.mark.parametrize("case", VALID, ids=lambda case: case["name"])
def test_valid_language_neutral_vectors(case):
    model = load_value(case["value"], case["kind"])
    assert model.to_dict() == case["value"]
    assert json.loads(dump_value(model)) == case["value"]
    assert yaml.safe_load(dump_value(model, format="yaml")) == case["value"]
    assert validate_value(model, case["kind"]) == case["value"]


@pytest.mark.parametrize("case", INVALID, ids=lambda case: case["name"])
def test_invalid_language_neutral_vectors(case):
    with pytest.raises(QueryError) as caught:
        load_value(case["value"], case["kind"])
    assert (caught.value.code, caught.value.path) == (case["code"], case["path"])


def test_json_yaml_python_share_a_request_model():
    value = VALID[1]["value"]
    from_python = load_request(value)
    from_json = load_request(json.dumps(value), format="json")
    from_yaml = load_request(yaml.safe_dump(value), format="yaml")
    assert isinstance(from_python, Request)
    assert from_python == from_json == from_yaml
    assert from_python.query.dataset == "cma_gfs_gmf_cmadaas"
    assert load_request(dump_value(from_python)).to_dict() == value


def test_request_list_preserves_pairing_and_order():
    model = load_request(VALID[2]["value"])
    assert isinstance(model, RequestList)
    assert [request.query.dataset for request in model.requests] == ["a", "b"]
    assert load_request(dump_value(model, format="yaml")).to_dict() == model.to_dict()


def test_yaml_unquoted_timestamp_stays_protocol_string():
    value = load_request("""api_version: cedarkit.request/v1
action: locate
query:
  api_version: cedarkit.query/v1
  domain: grid
  dataset: example.grid
  parameter: cedarkit.t
  reference_time: 2026-08-30T00:00:00Z
  lead_time: PT24H
""", format="yaml")
    assert value.query.reference_time == "2026-08-30T00:00:00Z"


def test_nested_input_is_copied_and_frozen():
    value = copy.deepcopy(VALID[1]["value"])
    model = load_request(value)
    value["query"]["dataset"] = "changed"
    assert model.query.dataset == "cma_gfs_gmf_cmadaas"
    with pytest.raises(TypeError):
        model._value["query"]["dataset"] = "changed"
    copied = model.to_dict()
    copied["query"]["dataset"] = "changed"
    assert model.query.dataset == "cma_gfs_gmf_cmadaas"


@pytest.mark.parametrize("source,format", [
    ('{"api_version":"cedarkit.request/v1","api_version":"cedarkit.request/v1"}', "json"),
    ("api_version: cedarkit.request/v1\napi_version: cedarkit.request/v1\n", "yaml"),
    ("!!python/object/apply:os.system ['echo forbidden']", "yaml"),
])
def test_duplicate_keys_and_unsafe_yaml_fail(source, format):
    with pytest.raises(QueryError) as caught:
        load_request(source, format=format)
    assert caught.value.code == "invalid_query"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_non_json_python_values_fail(value):
    request = copy.deepcopy(VALID[0]["value"])
    request["extra"] = value
    with pytest.raises(QueryError) as caught:
        load_request(request)
    assert caught.value.path == "/extra"


def test_cyclic_python_input_fails_without_recursing():
    request = copy.deepcopy(VALID[0]["value"])
    request["query"]["extra"] = request
    with pytest.raises(QueryError) as caught:
        load_request(request)
    assert caught.value.code == "invalid_query"


def test_nested_version_error_and_error_envelope():
    request = copy.deepcopy(VALID[0]["value"])
    request["query"]["api_version"] = "cedarkit.query/v2"
    with pytest.raises(QueryError) as caught:
        load_request(request)
    assert (caught.value.code, caught.value.path) == ("invalid_version", "/query/api_version")
    envelope = caught.value.to_dict()
    assert envelope["api_version"] == "cedarkit.error/v1"
    assert validate_value(envelope, "error") == envelope


def test_request_dispatch_does_not_hide_unknown_requests_key():
    request = copy.deepcopy(VALID[0]["value"])
    request["requests"] = []
    with pytest.raises(QueryError) as caught:
        load_request(request)
    assert caught.value.code == "invalid_query"
