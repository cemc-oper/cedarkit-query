"""Boundary and identity tests for pure query normalization."""

import copy
import json
from pathlib import Path

import pytest

from cedarkit_query import (
    AliasSnapshot,
    Binding,
    DataReference,
    QueryError,
    Transform,
    binding_identity,
    expand_request,
    normalize_request,
    query_identity,
    resource_identity,
    transform_identity,
)

NORMALIZATION_VECTORS = json.loads((Path(__file__).parent / "vectors/v1/normalization.json").read_text())


def request(**query):
    return {"api_version": "cedarkit.request/v1", "action": "locate",
            "query": {"api_version": "cedarkit.query/v1", "domain": "grid",
                      "dataset": "example.grid", "parameter": "cedarkit.t",
                      "reference_time": "2026-08-30T00:00:00Z", "lead_time": "PT24H"} | query}


@pytest.mark.parametrize("case", NORMALIZATION_VECTORS, ids=lambda case: case["name"])
def test_language_neutral_normalization_vectors(case):
    prepared = normalize_request(case["input"], catalog_snapshot=case.get("catalog_snapshot"),
                                 vocabulary_snapshot=case.get("vocabulary_snapshot"))
    assert prepared.normalized_request.query.to_dict() == case["normalized_query"]
    assert len(expand_request(prepared).expanded_requests) == case["expanded_count"]
    assert query_identity(prepared) == case["query_id"]


def test_time_duration_and_valid_time_are_canonical_and_redundant():
    a = request(reference_time="2026-08-30T08:00:00+08:00", lead_time="P1D",
                valid_time="2026-08-31T08:00:00+08:00")
    b = request()
    prepared = normalize_request(a)
    assert prepared.raw_request.query.valid_time == "2026-08-31T08:00:00+08:00"
    assert prepared.normalized_request.query.reference_time == "2026-08-30T00:00:00Z"
    assert prepared.normalized_request.query.lead_time == "PT86400S"
    assert "valid_time" not in prepared.normalized_request.query.to_dict()
    assert query_identity(prepared) == query_identity(b)
    assert a["query"]["reference_time"] == "2026-08-30T08:00:00+08:00"


def test_vertical_units_and_set_order_are_identity_equivalent():
    a = request(vertical={"type": "isobaric", "unit": "hPa", "values": [850, 500, 850]})
    b = request(vertical={"type": "isobaric", "unit": "Pa", "values": [50000, 85000]})
    assert normalize_request(a).normalized_request.query.vertical["unit"] == "Pa"
    assert query_identity(a) == query_identity(b)
    assert len(expand_request(a).expanded_requests) == 2


def test_statistic_window_does_not_follow_lead_time():
    instant = request(statistic={"kind": "instant"})
    accum = request(statistic={"kind": "accum", "window": "P1D"})
    normalized = normalize_request(accum).normalized_request
    assert normalized.query.statistic["window"] == "PT86400S"
    assert query_identity(instant) != query_identity(accum)


def test_alias_resolution_requires_explicit_snapshots_and_records_versions():
    item = request(dataset={"namespace": "catalog", "name": "gfs"},
                   parameter={"namespace": "cmadaas", "name": "TEM"})
    catalog = AliasSnapshot("catalog-a", {"catalog": {"gfs": "example.grid"}})
    vocabulary = AliasSnapshot("vocab-a", {"cmadaas": {"TEM": "cedarkit.t"}})
    prepared = normalize_request(item, catalog_snapshot=catalog, vocabulary_snapshot=vocabulary)
    assert (prepared.catalog_version, prepared.vocabulary_version) == ("catalog-a", "vocab-a")
    assert query_identity(prepared) == query_identity(request())
    with pytest.raises(QueryError) as caught:
        normalize_request(item)
    assert (caught.value.code, caught.value.path) == ("unknown_dataset", "/query/dataset")
    changed_version = AliasSnapshot("catalog-b", {"catalog": {"gfs": "example.grid"}})
    assert query_identity(item, catalog_snapshot=changed_version, vocabulary_snapshot=vocabulary) == query_identity(prepared)


def test_unknown_and_ambiguous_aliases_fail_without_guessing():
    item = request(parameter={"namespace": "cmadaas", "name": "PRE"})
    with pytest.raises(QueryError) as missing:
        normalize_request(item, vocabulary_snapshot=AliasSnapshot("v", {}))
    assert missing.value.code == "unknown_parameter"
    with pytest.raises(QueryError) as ambiguous:
        normalize_request(item, vocabulary_snapshot=AliasSnapshot(
            "v", {"cmadaas": {"PRE": ("cedarkit.p1", "cedarkit.p2")}}))
    assert ambiguous.value.code == "ambiguous_mapping"


@pytest.mark.parametrize("closed,expected", [
    ("both", ["PT0S", "PT21600S", "PT43200S"]),
    ("left", ["PT0S", "PT21600S"]),
    ("right", ["PT21600S", "PT43200S"]),
    ("neither", ["PT21600S"]),
])
def test_range_endpoint_rules(closed, expected):
    item = request(lead_time={"start": "PT0H", "stop": "PT12H", "step": "PT6H", "closed": closed})
    normalized = normalize_request(item).normalized_request.query.to_dict()
    assert normalized["lead_time"] == (expected[0] if len(expected) == 1 else expected)


def test_explicit_list_and_range_have_same_identity():
    ranged = request(lead_time={"start": "PT0H", "stop": "PT12H", "step": "PT6H", "closed": "both"})
    listed = request(lead_time=["PT12H", "PT0S", "PT6H", "PT6H"])
    assert query_identity(ranged) == query_identity(listed)


def test_reference_time_and_numeric_level_ranges_use_same_endpoint_rules():
    item = request(
        reference_time={"start": "2026-08-30T08:00:00+08:00",
                        "stop": "2026-08-30T12:00:00Z", "step": "PT6H", "closed": "right"},
        vertical={"type": "isobaric", "unit": "hPa",
                  "values": {"start": 500, "stop": 700, "step": 100, "closed": "both"}},
    )
    normalized = normalize_request(item).normalized_request.query.to_dict()
    assert normalized["reference_time"] == ["2026-08-30T06:00:00Z", "2026-08-30T12:00:00Z"]
    assert normalized["vertical"]["values"] == [50000, 60000, 70000]
    assert len(expand_request(item).expanded_requests) == 6


def test_cartesian_query_and_paired_request_list_differ():
    cartesian = request(parameter=["cedarkit.t", "cedarkit.h"], lead_time=["PT0H", "PT6H"])
    paired = {"api_version": "cedarkit.request-list/v1", "requests": [
        request(parameter="cedarkit.t", lead_time="PT0H"),
        request(parameter="cedarkit.h", lead_time="PT6H"),
    ]}
    product = expand_request(cartesian)
    pairs = expand_request(paired)
    assert len(product.expanded_requests) == 4
    assert len(pairs.expanded_requests) == 2
    assert [(item.query.parameter, item.query.lead_time) for item in pairs.expanded_requests] == [
        ("cedarkit.t", "PT0S"), ("cedarkit.h", "PT21600S")]


def test_expand_bounds_before_any_provider_access():
    item = request(parameter=["p1", "p2"])
    item["options"] = {"max_expansion": 1}
    with pytest.raises(QueryError) as caught:
        expand_request(item)
    assert caught.value.code == "expansion_limit"
    huge = request(lead_time={"start": "PT0S", "stop": "PT4096S", "step": "PT1S", "closed": "both"})
    with pytest.raises(QueryError) as caught:
        normalize_request(huge)
    assert caught.value.code == "expansion_limit"


def test_zero_or_one_rejects_multiple_expanded_fields():
    item = request(parameter=["p1", "p2"])
    item["policy"] = {"cardinality": "zero_or_one", "missing": "partial"}
    with pytest.raises(QueryError) as caught:
        expand_request(item)
    assert caught.value.path == "/policy/cardinality"


def test_invalid_times_windows_units_and_bounds_fail():
    examples = [
        (request(valid_time="2026-08-30T12:00:00Z"), "/query/valid_time"),
        (request(vertical={"type": "isobaric", "unit": "m", "values": 500}), "/query/vertical/unit"),
        (request(vertical={"type": "isobaric", "unit": "hPa", "bounds": [{"lower": 850, "upper": 500}]}),
         "/query/vertical/bounds/0"),
        (request(coverage={"crs": "EPSG:4326", "bbox": [140, 10, 70, 60], "relation": "intersects"}),
         "/query/coverage/bbox"),
    ]
    for item, path in examples:
        with pytest.raises(QueryError) as caught:
            normalize_request(item)
        assert caught.value.path == path


def test_transform_binding_and_resource_identities_are_independent():
    query = request()
    crop = Transform({"api_version": "cedarkit.transform/v1", "operations": [
        {"kind": "crop", "bbox": [70, 10, 140, 60]}, {"kind": "unit", "target": "K"}]})
    reversed_transform = Transform({"api_version": "cedarkit.transform/v1", "operations": [
        {"kind": "unit", "target": "K"}, {"kind": "crop", "bbox": [70, 10, 140, 60]}]})
    assert transform_identity(crop) != transform_identity(reversed_transform)
    first = Binding({"api_version": "cedarkit.binding/v1", "dataset": "example.grid", "kind": "local",
                     "source_id": "s", "catalog_version": "c", "locator": {"resolver": "file-pattern",
                                                                        "root": "/a", "pattern": "*.grib2"}})
    second = Binding({**first.to_dict(), "locator": {"resolver": "file-pattern", "root": "/b", "pattern": "*.grib2"}})
    assert binding_identity(first) != binding_identity(second)
    reference = DataReference({"api_version": "cedarkit.data-reference/v1", "dataset": "example.grid",
                               "field_identity": {"parameter": "cedarkit.t", "reference_time": "2026-08-30T00:00:00Z",
                                                  "lead_time": "PT24H"},
                               "binding_id": binding_identity(first),
                               "resource": {"kind": "local", "locator": "/a/x.grib2",
                                            "fingerprint": {"size": 12, "mtime_ns": 1, "device": 2, "inode": 3}},
                               "verification": "path_exists"})
    changed = reference.to_dict()
    changed["resource"]["fingerprint"]["mtime_ns"] = 2
    assert resource_identity(reference) != resource_identity(DataReference(changed))
    assert query_identity(query) == query_identity(copy.deepcopy(query))
    assert query_identity(query["query"]) == query_identity(query)
    altered_request = copy.deepcopy(query)
    altered_request["action"] = "read"
    altered_request["policy"] = {"cardinality": "all"}
    altered_request["transforms"] = crop.to_dict()
    assert query_identity(altered_request) == query_identity(query)


def test_remote_resource_needs_version_before_stable_identity():
    remote = {"api_version": "cedarkit.data-reference/v1", "dataset": "example.grid",
              "field_identity": {"parameter": "cedarkit.t", "reference_time": "2026-08-30T00:00:00Z",
                                 "lead_time": "PT24H"},
              "binding_id": "sha256:" + "a" * 64,
              "resource": {"kind": "cmadaas", "locator": "CODE"},
              "verification": "remote_unverified"}
    with pytest.raises(QueryError) as caught:
        resource_identity(DataReference(remote))
    assert (caught.value.code, caught.value.path) == ("availability_unknown", "/resource/version")
    versioned = copy.deepcopy(remote)
    versioned["resource"]["version"] = "response-1"
    assert resource_identity(DataReference(versioned)).startswith("sha256:")
