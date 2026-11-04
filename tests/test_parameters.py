import json

import pytest
import yaml

from cedarkit_query import (
    Query,
    QueryError,
    Request,
    combine_queries,
    constrain_query,
    expand_queries,
    load_value,
    prepare_queries,
    query_identity,
)

TIME = {"time": "2026-11-02T12:00:00Z", "step": "PT6H"}
HEIGHT = {"level_type": "height_above_ground", "level": 2, "level_unit": "m"}
PRESSURE = {"level_type": "pressure", "level": 850, "level_unit": "hPa"}
# Explicit definitions exercise the shared binding contract. The deployment
# snapshot and its native mapping are supplied by later tasks.
PARAMETERS = {
    "cedarkit.t": {"name": "t"},
    "cedarkit.t2m": {
        "name": "t2m",
        "aliases": ["temperature-2m"],
        "parent": "cedarkit.t",
        "fixed_conditions": HEIGHT,
    },
}


@pytest.mark.parametrize(
    "conditions,canonical_conditions",
    [
        ({"parameter": "cedarkit.t2m"}, {"parameter": "cedarkit.t2m", **HEIGHT}),
        ({"parameter": "cedarkit.t", **PRESSURE}, {"parameter": "cedarkit.t", **PRESSURE}),
    ],
)
def test_gfs_query_and_request_share_canonical_identity(tmp_path, conditions, canonical_conditions):
    query = Query(**conditions, **TIME)
    data = {
        "api_version": "cedarkit.request/v1",
        "source": {"kind": "local", "dataset": "cma_gfs_gmf", "root": "./data"},
        "queries": [query.to_dict()],
    }
    direct = Request.from_dict({**data, "queries": [query]}, base_dir=tmp_path)
    expected = {
        **canonical_conditions,
        "time": "2026-11-02T12:00:00Z",
        "step": "PT21600S",
        "valid_time": "2026-11-02T18:00:00Z",
    }
    assert direct.source["root"] == str(tmp_path / "data")
    assert direct.completeness is None
    variants = [query]
    for suffix, dump in [("json", json.dumps), ("yaml", yaml.safe_dump)]:
        path = tmp_path / f"request.{suffix}"
        path.write_text(dump(data), encoding="utf-8")
        loaded = Request.load(path)
        assert loaded == direct
        variants.extend([loaded.queries[0], load_value(dump(query.to_dict()), "query")])
        direct.dump(path)
        assert Request.load(path) == direct
    for declaration in variants:
        bound, = prepare_queries([declaration], PARAMETERS)
        assert bound.to_dict() == expected
        assert query_identity(bound) == query_identity(expected)


def test_exact_names_aliases_and_implied_variant_share_identity():
    equivalent = [
        {"parameter": "cedarkit.t2m"},
        {"parameter": "t2m"},
        {"parameter": ["temperature-2m", "cedarkit.t2m"]},
        {"parameter": "t", "level_type": "height_above_ground", "level": 200, "level_unit": "cm"},
    ]
    expected = {"parameter": "cedarkit.t2m", **HEIGHT}
    for data in equivalent:
        assert prepare_queries([data], PARAMETERS) == (Query(expected),)
    assert prepare_queries(equivalent, PARAMETERS) == (Query(expected),)
    base, = prepare_queries([Query(parameter="t", **PRESSURE)], PARAMETERS)
    assert base.parameter == "cedarkit.t"
    assert query_identity(base) != query_identity(expected)


@pytest.mark.parametrize(
    "constraints,path",
    [
        ({"level": 10}, "/level"),
        ({"level": [2, 10]}, "/level"),
        ({"level_type": "pressure"}, "/level_type"),
    ],
)
def test_fixed_height_rejects_conflicting_choices_in_each_form(constraints, path):
    raw = {"parameter": ["cedarkit.t", "t2m"], **constraints}
    for data in (raw, json.dumps(raw), yaml.safe_dump(raw)):
        with pytest.raises(QueryError) as caught:
            prepare_queries([load_value(data, "query")], PARAMETERS)
        assert caught.value.to_dict() == {
            "api_version": "cedarkit.error/v1",
            "code": "constraint_conflict",
            "path": path,
            "message": "explicit choice conflicts with bound constraints",
            "context": {"reason": "fixed_condition_conflict"},
        }


def test_unknown_and_ambiguous_parameter_are_explicit():
    for name, parameters, reason, candidates in [
        ("T2M", PARAMETERS, "unknown_parameter", []),
        (
            "t2m",
            {**PARAMETERS, "other.t2m": {"name": "t2m"}},
            "ambiguous_parameter",
            ["cedarkit.t2m", "other.t2m"],
        ),
    ]:
        with pytest.raises(QueryError) as caught:
            prepare_queries([Query(parameter=name)], parameters)
        assert caught.value.code == "invalid_query"
        assert caught.value.path == "/parameter"
        assert caught.value.to_dict()["context"] == {"reason": reason, "candidates": candidates}


def test_paired_named_and_native_constraints_survive_selection_algebra():
    native = {"grib": {"parameterNumber": 0}}
    branches = prepare_queries(
        [
            Query(parameter="t2m", step="PT6H", native=native),
            Query(parameter="t", step="PT12H", native=native, **PRESSURE),
        ],
        PARAMETERS,
    )
    rows = expand_queries(branches, max_expansion=2)
    assert {(row.parameter, row.level, row.step) for row in rows} == {
        ("cedarkit.t2m", 2, "PT21600S"),
        ("cedarkit.t", 850, "PT43200S"),
    }
    assert all(row.to_dict()["native"] == native for row in rows)
    one, = combine_queries([branches, [Query(step="PT6H", native=native)]])
    assert one["query"]["parameter"] == "cedarkit.t2m"
    with pytest.raises(QueryError) as caught:
        constrain_query(one["query"], {"native": {"grib": {"parameterNumber": 1}}})
    assert (caught.value.code, caught.value.path) == (
        "constraint_conflict", "/native/grib/parameterNumber"
    )
