import pytest

from cedarkit_query import (
    Query,
    QueryError,
    combine_queries,
    constrain_query,
    expand_queries,
    intersect_queries,
    query_identity,
)


@pytest.mark.parametrize(
    "left,right",
    [
        (
            {"level_type": "pressure", "level": 850},
            {"level_type": "pressure", "level": 85000, "level_unit": "Pa"},
        ),
        (
            {
                "level_type": "depth_below_land",
                "level_unit": "cm",
                "layer": {"bottom": 40, "top": 10},
            },
            {
                "layer": {
                    "bottom": {"type": "depth_below_land", "value": 0.4, "unit": "m"},
                    "top": {"type": "depth_below_land", "value": 10, "unit": "cm"},
                }
            },
        ),
        ({"time": "2026-10-27T08:00:00+08:00"}, {"time": "2026-10-27T00:00:00Z"}),
        ({"step": "P1D"}, {"step": "PT24H"}),
        (
            {"level_type": "pressure", "level": [500, 850, 500]},
            {"level_type": "pressure", "level": [850, 500]},
        ),
        ({"member": [1]}, {"member": 1}),
        (
            {"step": {"range": {"start": "PT0H", "end": "PT12H", "every": "PT6H"}}},
            {"step": ["PT0H", "PT6H", "PT12H"]},
        ),
        (
            {"time": "2026-10-27T00:00:00Z", "step": "PT6H"},
            {"time": "2026-10-27T00:00:00Z", "valid_time": "2026-10-27T06:00:00Z"},
        ),
    ],
)
def test_equivalent_identity(left, right):
    assert query_identity(left) == query_identity(right)


def test_identity_context_is_semantic_and_native_is_retained():
    q = {"parameter": "t"}
    assert query_identity(q, context={"scope": {"member": 0}}) != query_identity(
        q, context={"scope": {"member": 1}}
    )
    assert query_identity(q) != query_identity(
        {**q, "native": {"grib": {"edition": 2}}}
    )


def test_pairing_and_time_relations():
    q = {
        "time": ["2026-10-27T00:00:00Z", "2026-10-27T06:00:00Z"],
        "step": ["PT6H", "PT12H"],
        "valid_time": "2026-10-27T12:00:00Z",
    }
    rows = [q.to_dict() for q in expand_queries([q], max_expansion=2)]
    assert {(r["time"], r["step"]) for r in rows} == {
        ("2026-10-27T00:00:00Z", "PT43200S"),
        ("2026-10-27T06:00:00Z", "PT21600S"),
    }
    with pytest.raises(QueryError):
        expand_queries([{**q, "step": ["PT6H", "PT12H", "PT24H"]}])
    with pytest.raises(QueryError):
        Query(
            {
                "time": "2026-10-27T00:00:00Z",
                "step": "PT6H",
                "valid_time": "2026-10-27T12:00:00Z",
            }
        )


def test_nanosecond_and_subsecond_precision():
    rows = expand_queries(
        [{"time": "2026-10-27T08:00:00.123456789+08:00", "step": "PT0.000000001S"}]
    )
    assert rows[0].valid_time == "2026-10-27T00:00:00.12345679Z"
    with pytest.raises(QueryError):
        Query(time="2026-10-27T00:00:00.1234567891Z")


def test_count_legal_effective_combinations_not_raw_time_cartesian():
    q = {
        "time": {
            "range": {
                "start": "2026-10-27T00:00:00Z",
                "end": "2026-10-27T06:00:00Z",
                "every": "PT1H",
            }
        },
        "step": {"range": {"start": "PT0H", "end": "PT6H", "every": "PT1H"}},
        "valid_time": "2026-10-27T06:00:00Z",
    }
    assert len(expand_queries([q], max_expansion=7)) == 7
    with pytest.raises(QueryError) as e:
        expand_queries([q], max_expansion=6)
    assert e.value.code == "expansion_limit_exceeded"
    assert e.value.context["limit"] == 6 and e.value.context["count_lower_bound"] == 7


def test_expansion_union_dedup_limits_and_continuous_filters():
    q = {
        "parameter": "t",
        "level_type": "pressure",
        "level": [850, 500],
        "step": {"range": {"start": "PT0H", "end": "PT24H", "every": "PT6H"}},
    }
    assert len(expand_queries([q, q], max_expansion=10)) == 10
    with pytest.raises(QueryError):
        expand_queries([q], max_expansion=9)
    assert (
        len(
            expand_queries(
                [
                    {
                        "level_type": "pressure",
                        "level": {"range": {"start": 1, "end": 1000000}},
                    }
                ],
                max_expansion=1,
            )
        )
        == 1
    )
    values = [
        x.member
        for x in expand_queries(
            [{"member": {"range": {"start": 0, "end": 10, "every": 3}}}]
        )
    ]
    assert sorted(values) == [0, 3, 6, 9]
    with pytest.raises(QueryError):
        expand_queries(
            [{"member": {"range": {"start": 0, "end": 1000000, "every": 1}}}],
            max_expansion=3,
        )
    for bad in [0, -1, True, 1.0, "3", None]:
        with pytest.raises(QueryError) as e:
            expand_queries([{}], max_expansion=bad)
        assert e.value.code == "invalid_config"


def test_layer_pairs_and_native_selector_expansion():
    rows = expand_queries(
        [
            {
                "level_type": "depth_below_land",
                "layer": [{"bottom": 0.1, "top": 0}, {"bottom": 0.4, "top": 0.1}],
                "step": ["PT6H", "PT12H"],
            }
        ]
    )
    assert len(rows) == 4
    assert {(r.layer["bottom"]["value"], r.layer["top"]["value"]) for r in rows} == {
        (0.1, 0),
        (0.4, 0.1),
    }
    rows = expand_queries(
        [
            {
                "native": {
                    "grib": {
                        "parameterNumber": [0, 1],
                        "typeOfFirstFixedSurface": [100, 103],
                    }
                }
            }
        ]
    )
    assert len(rows) == 4 and all("parameter" not in r.to_dict() for r in rows)


def test_chain_intersection_and_origins():
    first = [
        {"parameter": "t", "level_type": "pressure", "level": 850, "step": "PT6H"},
        {"parameter": "t", "level_type": "pressure", "level": 500, "step": "PT12H"},
    ]
    branches = combine_queries(
        [first, [{"level_type": "pressure", "level": 500}]], max_expansion=2
    )
    assert len(branches) == 1 and branches[0]["query"]["step"] == "PT43200S"
    assert branches[0]["origins"] == [
        {"step_index": 0, "query_index": 1},
        {"step_index": 1, "query_index": 0},
    ]
    assert (
        combine_queries(
            [
                [{"level_type": "pressure", "level": 850}],
                [{"level_type": "pressure", "level": 500}],
            ]
        )
        == ()
    )
    with pytest.raises(QueryError):
        combine_queries([[{"level": 1}], [{"level": 2}], [{"level": None}]])
    with pytest.raises(QueryError):
        combine_queries(
            [
                [{"level_type": "pressure", "level": [850, 500]}],
                [{"level_type": "pressure", "level": 850}],
            ],
            max_expansion=1,
        )
    with pytest.raises(QueryError) as e:
        combine_queries(
            [[{"level": 1}], [{"level": 2}], [{"member": 1}]], scope={"member": 0}
        )
    assert e.value.code == "constraint_conflict"


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (
            {"range": {"start": 0, "end": 30, "every": 4}},
            {"range": {"start": 2, "end": 30, "every": 6}},
            [8, 20],
        ),
        (
            {"range": {"start": 0, "end": 1, "every": 0.2}},
            {"range": {"start": 0.1, "end": 1, "every": 0.3}},
            [0.4, 1],
        ),
        ({"range": {"start": 0, "end": 1000000, "every": 2}}, 3, []),
    ],
)
def test_range_intersections_are_pure_and_exact(a, b, expected):
    q = intersect_queries(
        {"level_type": "height_above_ground", "level": a},
        {"level_type": "height_above_ground", "level": b},
    )
    assert (
        [] if q is None else sorted(r.level for r in expand_queries([q]))
    ) == expected


def test_fixed_definition_requires_every_explicit_choice():
    fixed = {"level_type": "height_above_ground", "level": 2}
    with pytest.raises(QueryError) as e:
        constrain_query({"level": [2, 10]}, fixed)
    assert e.value.code == "constraint_conflict"
    q = constrain_query({"level": {"range": {"start": 0, "end": 10}}}, fixed)
    assert q.level == 2
    with pytest.raises(QueryError):
        constrain_query(
            {"level": {"range": {"start": 0, "end": 10, "every": 2}}}, fixed
        )
    with pytest.raises(QueryError):
        constrain_query(
            {
                "layer": {
                    "bottom": {"type": "surface"},
                    "top": {"type": "atmosphere_top"},
                }
            },
            fixed,
        )


def test_cemc_member_role_pairs_and_overrides():
    assert (
        len(
            expand_queries(
                [{"member": [0, 1, 30], "member_role": ["control", "perturbation"]}],
                cemc=True,
            )
        )
        == 3
    )
    with pytest.raises(QueryError):
        expand_queries([{"member": 1, "member_role": "control"}], cemc=True)
    assert (
        expand_queries(
            [{"member": 1, "member_role": "control"}],
            member_roles={"1": "control"},
            cemc=True,
        )[0].member_role
        == "control"
    )


def test_time_join_clips_inferred_negative_durations_and_unaligned_range_end():
    query = {
        "time": "2026-10-27T00:00:00Z",
        "step": ["PT3H", "PT6H"],
        "valid_time": {
            "range": {"start": "2026-10-26T23:00:00Z", "end": "2026-10-27T06:00:00Z"}
        },
    }
    assert sorted(q.step for q in expand_queries([query])) == ["PT10800S", "PT21600S"]
    query = {
        "time": {
            "range": {
                "start": "2026-10-27T00:00:00Z",
                "end": "2026-10-27T10:00:00Z",
                "every": "PT3H",
            }
        },
        "step": {"range": {"start": "PT0H", "end": "PT10H"}},
        "valid_time": "2026-10-27T10:00:00Z",
    }
    assert sorted(q.step for q in expand_queries([query])) == sorted(
        ["PT14400S", "PT25200S", "PT3600S", "PT36000S"]
    )


def test_large_bound_checks_do_not_enumerate_unused_domains():
    fixed = {"member": {"range": {"start": 0, "end": 1000000000000, "every": 2}}}
    q = constrain_query(fixed, fixed)
    with pytest.raises(QueryError) as e:
        expand_queries([q], max_expansion=3)
    assert e.value.code == "expansion_limit_exceeded"
    q = {
        "time": {
            "range": {
                "start": "0001-01-01T00:00:00Z",
                "end": "9999-12-31T23:59:59Z",
                "every": "PT1S",
            }
        },
        "step": {"range": {"start": "PT0H", "end": "PT1H"}},
        "valid_time": "9999-12-31T23:59:59Z",
    }
    with pytest.raises(QueryError) as e:
        expand_queries([q])
    assert (
        e.value.code == "invalid_query"
    )  # almost every explicit time is unparticipating


def test_native_bound_does_not_drop_explicit_choices():
    with pytest.raises(QueryError) as e:
        constrain_query(
            {"native": {"grib": {"edition": [1, 2]}}},
            {"native": {"grib": {"edition": 2}}},
        )
    assert e.value.code == "constraint_conflict"


def test_sub_nanosecond_duration_is_rejected_without_rounding():
    with pytest.raises(QueryError):
        Query(step="PT0.0000000001S")


def test_later_invalid_time_query_cannot_be_hidden_by_empty_selection():
    with pytest.raises(QueryError):
        combine_queries(
            [
                [{"parameter": "t"}],
                [{"parameter": "u"}],
                [
                    {
                        "time": ["2026-10-27T00:00:00Z", "2026-10-27T06:00:00Z"],
                        "step": "PT6H",
                        "valid_time": "2026-10-27T06:00:00Z",
                    }
                ],
            ]
        )


def test_limit_can_be_configured_above_the_library_default():
    q = {"member": {"range": {"start": 0, "end": 10000, "every": 1}}}
    assert len(expand_queries([q], max_expansion=10001)) == 10001
    with pytest.raises(QueryError) as e:
        expand_queries([q])
    assert e.value.context["limit"] == 10000
    assert e.value.context["origins"] == ({"step_index": 0, "query_index": 0},)
