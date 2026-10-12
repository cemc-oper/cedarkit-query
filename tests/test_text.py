"""Restricted text parser and structured entry-point parity."""

import json
from pathlib import Path

import pytest
import yaml

from cedarkit_query import (
    QueryError, Request, RequestList, expand_request, load_request, normalize_request,
    parse_text_request, parse_text_requests, query_identity,
)

VECTORS = json.loads((Path(__file__).parent / "vectors/v1/text.json").read_text())


@pytest.mark.parametrize("case", VECTORS["valid"], ids=lambda case: case["name"])
def test_valid_text_vectors_share_the_structured_model(case):
    parsed = parse_text_request(case["text"])
    structured = load_request(case["request"])
    encoded = load_request(json.dumps(case["request"]), format="json")
    from_yaml = load_request(yaml.safe_dump(case["request"]), format="yaml")
    assert isinstance(parsed, Request)
    assert parsed == structured == encoded == from_yaml
    assert normalize_request(parsed).normalized_request == normalize_request(structured).normalized_request
    assert query_identity(parsed) == query_identity(structured)


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda case: case["name"])
def test_invalid_text_vectors_report_field_and_position(case):
    with pytest.raises(QueryError) as caught:
        parse_text_request(case["text"])
    assert caught.value.code == "invalid_query"
    assert caught.value.path == case["path"]
    assert caught.value.context == {"line": case["line"], "column": case["column"]}


def test_request_list_preserves_pairs_and_has_no_inheritance():
    first = "PLAN dataset=grid.a parameter=cedarkit.t reference_time=2026-08-30T00:00:00Z lead_time=PT6H"
    second = "PLAN dataset=grid.b parameter=cedarkit.h reference_time=2026-08-30T12:00:00Z lead_time=PT12H"
    result = parse_text_requests(f"{first}\n---\n{second}")
    assert isinstance(result, RequestList)
    assert result.requests == (parse_text_request(first), parse_text_request(second))
    assert len(expand_request(result).expanded_requests) == 2
    with pytest.raises(QueryError) as caught:
        parse_text_requests(f"{first}\n---\nPLAN parameter=cedarkit.h reference_time=2026-08-30T12:00:00Z lead_time=PT12H")
    assert caught.value.path == "/requests/1/query/dataset"
    assert caught.value.context["line"] == 3


def test_list_range_expands_cartesian_product_and_equal_explicit_list():
    ranged = parse_text_request(VECTORS["valid"][1]["text"])
    expanded = expand_request(ranged)
    assert len(expanded.expanded_requests) == 12
    explicit = parse_text_request(
        "PLAN dataset=example.grid parameter=[cedarkit.h,cedarkit.t] "
        "reference_time=2026-08-30T00:00:00Z lead_time=[PT12H,PT0S,PT6H] "
        "vertical.type=isobaric vertical.unit=Pa vertical.values=[85000,50000] policy.cardinality=all"
    )
    assert query_identity(ranged) == query_identity(explicit)


def test_reference_time_range_has_both_endpoints_and_utc_identity():
    ranged = parse_text_request(
        "PLAN dataset=x parameter=p reference_time=2026-08-30T08:00:00+08:00..2026-08-30T20:00:00+08:00/PT12H lead_time=PT0S"
    )
    explicit = parse_text_request(
        "PLAN dataset=x parameter=p reference_time=[2026-08-30T00:00:00Z,2026-08-30T12:00:00Z] lead_time=PT0S"
    )
    assert query_identity(ranged) == query_identity(explicit)
    assert len(expand_request(ranged).expanded_requests) == 2


@pytest.mark.parametrize("text,path", [
    ("PLAN dataset=x parameter=p reference_time=2026-08-30T00:00:00 lead_time=PT6H", "/query/reference_time"),
    ("PLAN dataset=x parameter=p reference_time=2026-08-30T00:00:00Z lead_time=PT0S..PT6H/PT0S", "/query/lead_time"),
    ("LIST dataset=x vertical.type=surface vertical.unit=hPa", "/query/vertical"),
    ("LIST dataset=x statistic.kind=accum", "/query/statistic/window"),
    ("LIST dataset=x member=control:", "/query/member"),
    ("LIST dataset=x policy.cardinality=first", "/policy/cardinality"),
    ("LIST dataset=x parameter=[a,]", "/query/parameter"),
    ("LIST dataset=x parameter=[a, b]", "/query/parameter"),
    ("LIST dataset=x parameter=\"bad\\q\"", "/query/parameter"),
    ("LIST dataset=x options.max_expansion=1", "/options/max_expansion"),
    ("LIST dataset=x parameter=$(whoami)", "/query/parameter"),
    ("LIST dataset=x vertical.type=modelLevel vertical.unit=1 vertical.values=1e-999", "/query/vertical/values"),
])
def test_invalid_syntax_or_model_condition(text, path):
    with pytest.raises(QueryError) as caught:
        parse_text_request(text)
    assert caught.value.path == path
    assert caught.value.context == {"line": 1, "column": caught.value.context["column"]}


@pytest.mark.parametrize("text", [
    "LIST dataset=x\nLIST dataset=y",
    "LIST dataset=x\n---\nLIST dataset=y",
    "",
])
def test_single_request_entry_rejects_multiple_or_empty_lines(text):
    with pytest.raises(QueryError):
        parse_text_request(text)


@pytest.mark.parametrize("text,line", [
    ("LIST dataset=x\nLIST dataset=y", 2),
    ("---\nLIST dataset=x", 1),
    ("LIST dataset=x\n---", 2),
    ("LIST dataset=x\n---\n---\nLIST dataset=y", 3),
])
def test_request_list_needs_separators(text, line):
    with pytest.raises(QueryError) as caught:
        parse_text_requests(text)
    assert caught.value.context["line"] == line


def test_quoted_escape_and_one_item_request_list():
    request = parse_text_request('VALIDATE dataset="grid\\\"east"')
    assert request.query.dataset == 'grid"east'
    assert parse_text_requests('VALIDATE dataset="grid\\\"east"').requests == (request,)


def test_invalid_second_list_item_points_to_its_column():
    with pytest.raises(QueryError) as caught:
        parse_text_request("LIST dataset=x member=[control:0,bad]")
    assert caught.value.path == "/query/member"
    assert caught.value.context == {"line": 1, "column": 34}
