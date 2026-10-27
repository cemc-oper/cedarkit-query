from copy import deepcopy
import json

import pytest
import yaml

from cedarkit_query import (
    Catalog,
    Context,
    Mappings,
    Parameters,
    QueryError,
    RULES,
    effective_parameters,
    load_value,
    normalize_statistics,
    prepare_queries,
    validate_product,
    validate_resource_set,
    validate_rule,
)


def test_valid_resource_combination_and_inherited_meaning(
    parameters, mappings, catalog
):
    inputs = deepcopy((parameters, mappings, catalog))
    resources = validate_resource_set(
        parameters,
        [mappings],
        catalog,
        products=[("example", "orig", "oper-1")],
        interface_contracts={("example.single-field", 1)},
    )
    assert (parameters, mappings, catalog) == inputs
    t2m = resources.parameter("t2m")
    assert t2m["unit"] == "K" and t2m["fixed_conditions"] == {
        "level_type": "height_above_ground",
        "level_unit": "m",
        "level": 2,
    }
    rain = resources.parameter("rain24")
    assert rain["fixed_conditions"]["statistics"]["period"]["duration"] == "PT86400S"
    assert (
        resources.parameter("st(10-40)")["fixed_conditions"]["layer"]["bottom"]["value"]
        == 0.4
    )
    assert resources.parameter("temperature")["parameter_id"] == "cedarkit.t"
    with pytest.raises(TypeError):
        resources.effective["cedarkit.t"]["unit"] = "degC"


@pytest.mark.parametrize("kind", ["parameters", "mappings", "catalog"])
def test_resource_python_json_yaml_same_contract(kind, parameters, mappings, catalog):
    data = {"parameters": parameters, "mappings": mappings, "catalog": catalog}[kind]
    a = load_value(data, kind)
    assert load_value(json.dumps(data), kind) == a
    assert load_value(yaml.safe_dump(data), kind) == a
    data["unexpected"] = "no"
    errors = []
    for value in [data, json.dumps(data), yaml.safe_dump(data)]:
        with pytest.raises(QueryError) as e:
            load_value(value, kind)
        errors.append((e.value.code, e.value.path))
    assert errors == [("invalid_config", "/unexpected")] * 3


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["parameters"].append(deepcopy(p["parameters"][0])),
        lambda p: p["parameters"][0].update(unit=None),
        lambda p: p["parameters"][0].update(parameter_id=" "),
        lambda p: p["parameters"][0].update(aliases=["a", "a"]),
        lambda p: p["parameters"][0].update(aliases=["t"]),
        lambda p: p["parameters"][0].update(name="cedarkit.t2m"),
        lambda p: p["parameters"][0].update(aliases=["cedarkit.t2m"]),
        lambda p: p["parameters"][1].update(parent="absent"),
        lambda p: p["parameters"][0].update(parent="cedarkit.t2m"),
        lambda p: p["parameters"][1].update(parent="cedarkit.t2m"),
        lambda p: p["parameters"][1]["fixed_conditions"].update(level=[2, 10]),
        lambda p: p["parameters"][1]["fixed_conditions"].update(step="PT24H"),
        lambda p: p["parameters"][1]["fixed_conditions"].update(level_unit="Pa"),
        lambda p: p["parameters"][3]["fixed_conditions"].update(level=2),
        lambda p: p["parameters"][6]["fixed_conditions"].update(member=0),
        lambda p: p["parameters"][6]["fixed_conditions"]["statistics"].update(
            of={"parameter_id": "absent"}
        ),
    ],
)
def test_bad_parameter_resources(parameters, change):
    change(parameters)
    with pytest.raises(QueryError):
        Parameters(parameters)


def test_parent_and_statistical_object_joint_cycle(parameters):
    parameters["parameters"][4]["parent"] = "cedarkit.rain24.prob"
    with pytest.raises(QueryError):
        Parameters(parameters)


def test_parent_only_narrows_and_equivalent_units_are_accepted(parameters):
    parameters["parameters"][0]["fixed_conditions"] = {
        "level_type": "pressure",
        "level": 500,
        "level_unit": "hPa",
    }
    parameters["parameters"] = [
        parameters["parameters"][0],
        {
            "parameter_id": "child",
            "name": "child",
            "parent": "cedarkit.t",
            "fixed_conditions": {
                "level_type": "pressure",
                "level": 50000,
                "level_unit": "Pa",
            },
        },
    ]
    assert (
        effective_parameters(Parameters(parameters).to_dict())["child"][
            "fixed_conditions"
        ]["level"]
        == 500
    )
    parameters["parameters"][1]["fixed_conditions"]["level"] = 85000
    with pytest.raises(QueryError) as e:
        Parameters(parameters)
    assert e.value.code == "invalid_config"
    parameters["parameters"][1]["fixed_conditions"] = {
        "layer": {"bottom": 850, "top": 500}
    }
    with pytest.raises(QueryError):
        Parameters(parameters)


def test_statistics_inheritance_cannot_change_period_or_operation(parameters):
    parameters["parameters"].append(
        {
            "parameter_id": "rain6",
            "name": "rain6",
            "parent": "cedarkit.rain24",
            "fixed_conditions": {
                "statistics": {"period": {"kind": "trailing", "duration": "PT6H"}}
            },
        }
    )
    with pytest.raises(QueryError):
        Parameters(parameters)
    parameters["parameters"].pop()
    parameters["parameters"][4]["fixed_conditions"]["statistics"]["operation"] = (
        "instantaneous"
    )
    with pytest.raises(QueryError):
        Parameters(parameters)


def test_retired_ids_and_exact_case_sensitive_ambiguous_names(parameters, mappings):
    with pytest.raises(QueryError):
        validate_resource_set(parameters, [mappings], retired_ids=["cedarkit.t"])
    parameters["parameters"].append({"parameter_id": "other", "name": "t", "unit": "K"})
    r = validate_resource_set(parameters)
    with pytest.raises(QueryError) as e:
        r.parameter("t")
    assert e.value.context["reason"] == "ambiguous_parameter"
    assert e.value.context["candidates"] == ("cedarkit.t", "other")
    assert r.parameter("t", candidates=["other"])["parameter_id"] == "other"
    with pytest.raises(QueryError) as e:
        r.parameter("T")
    assert e.value.context["reason"] == "unknown_parameter"


@pytest.mark.parametrize(
    "over,operation,extras",
    [
        (
            "time",
            "instantaneous",
            {"period": {"kind": "trailing", "duration": "PT24H"}},
        ),
        ("time", "instantaneous", {"method": {"kind": "sample_mean"}}),
        ("time", "accumulation", {"of": {"parameter_id": "x"}}),
        ("time", "maximum", {"method": {"weighting": "equal"}}),
        ("time", "probability", {}),
        ("ensemble", "instantaneous", {}),
        (
            "ensemble",
            "mean",
            {"event": {"operator": "ge", "threshold": {"value": 1, "unit": "mm"}}},
        ),
        ("ensemble", "minimum", {"method": {"weighting": "equal"}}),
        ("ensemble", "standard_deviation", {"quantile": 0.9}),
        ("ensemble", "probability", {"quantile": 0.9}),
        ("ensemble", "quantile", {"period": {"kind": "since_initialization"}}),
        (
            "ensemble",
            "quantile",
            {"method": {"kind": "member_fraction", "denominator": "valid_members"}},
        ),
    ],
)
def test_statistical_operation_matrix_rejects_forbidden_fields(over, operation, extras):
    resource = {
        "api_version": "cedarkit.parameters/v1",
        "id": "p",
        "version": "1",
        "parameters": [
            {
                "parameter_id": "x",
                "name": "x",
                "fixed_conditions": {
                    "statistics": {"over": over, "operation": operation, **extras}
                },
            }
        ],
    }
    with pytest.raises(QueryError):
        Parameters(resource)


@pytest.mark.parametrize(
    "stats",
    [
        {},
        {
            "over": "time",
            "operation": "accumulation",
            "period": {"kind": "trailing", "duration": "PT0H"},
        },
        {
            "over": "time",
            "operation": "accumulation",
            "period": {"kind": "since_initialization", "duration": "PT24H"},
        },
        {"over": "ensemble", "operation": "quantile", "quantile": 90},
        {"over": "ensemble", "operation": "quantile", "quantile": True},
        {"over": "ensemble", "operation": "quantile", "quantile": float("nan")},
        {
            "over": "ensemble",
            "operation": "probability",
            "event": {
                "operator": "between",
                "lower": {"value": 40, "unit": "mm", "inclusive": True},
                "upper": {"value": 0.01, "unit": "m", "inclusive": False},
            },
        },
        {
            "over": "ensemble",
            "operation": "probability",
            "method": {
                "kind": "member_fraction",
                "denominator": "valid_members",
                "weighting": "equal",
            },
        },
    ],
)
def test_statistics_strict_shapes_and_units(stats):
    r = {
        "api_version": "cedarkit.parameters/v1",
        "id": "p",
        "version": "1",
        "parameters": [
            {
                "parameter_id": "x",
                "name": "x",
                "fixed_conditions": {"statistics": stats},
            }
        ],
    }
    with pytest.raises(QueryError):
        Parameters(r)


def test_unit_equivalent_event_and_fixed_statistic_identity():
    a = {
        "over": "ensemble",
        "operation": "probability",
        "event": {"operator": "ge", "threshold": {"value": 25, "unit": "mm"}},
    }
    b = {
        "over": "ensemble",
        "operation": "probability",
        "event": {"operator": "ge", "threshold": {"value": 0.025, "unit": "m"}},
    }
    assert normalize_statistics(a) == normalize_statistics(b)
    assert normalize_statistics(a) != normalize_statistics(
        {**b, "event": {"operator": "gt", "threshold": {"value": 0.025, "unit": "m"}}}
    )


def test_statistical_object_vertical_conflict(parameters):
    parameters["parameters"][6]["fixed_conditions"]["statistics"]["of"][
        "parameter_id"
    ] = "cedarkit.t2m"
    parameters["parameters"][6]["fixed_conditions"].update(
        level_type="pressure", level=850
    )
    with pytest.raises(QueryError):
        Parameters(parameters)


@pytest.mark.parametrize(
    "rule,duty,backend,config",
    [
        ("grib.fixed_surfaces", "vertical", "grib2", {}),
        ("grib.time", "time", "grib2", {}),
        ("grib.member", "member", "grib2", {}),
        ("grib.statistics", "statistics", "grib2", {}),
        ("cmadaas.vertical_table", "vertical", "cmadaas", {"table": "soil"}),
        ("cmadaas.time_fields", "time", "cmadaas", {}),
        ("cmadaas.member_field", "member", "cmadaas", {}),
        ("cmadaas.member_field", "member", "cmadaas", {"table": "members"}),
        ("cmadaas.statistics_fields", "statistics", "cmadaas", {}),
        ("common.value_unit", "unit", "grib2", {"source_unit": "K"}),
    ],
)
def test_nine_rule_contracts(rule, duty, backend, config):
    result = validate_rule(
        {"rule": rule, "config": config}, backend=backend, responsibility=duty
    )
    assert result == {"rule": rule, **({"config": config} if config else {})}
    assert len(RULES) == 9


@pytest.mark.parametrize(
    "rule,duty,backend,reason",
    [
        ({"rule": "unregistered"}, "time", "grib2", "unknown_rule"),
        ({"rule": "grib.time"}, "member", "grib2", "rule_not_applicable"),
        ({"rule": "grib.time"}, "time", "cmadaas", "rule_not_applicable"),
        ({"rule": "grib.time", "config": None}, "time", "grib2", "invalid_rule_config"),
        (
            {"rule": "grib.time", "config": {"time_key": "foo"}},
            "time",
            "grib2",
            "invalid_rule_config",
        ),
        (
            {"rule": "cmadaas.vertical_table"},
            "vertical",
            "cmadaas",
            "invalid_rule_config",
        ),
        (
            {
                "rule": "cmadaas.vertical_table",
                "config": {"table": "x", "request_field": "levelCode"},
            },
            "vertical",
            "cmadaas",
            "invalid_rule_config",
        ),
        (
            {"rule": "cmadaas.member_field", "config": {"offset": 1}},
            "member",
            "cmadaas",
            "invalid_rule_config",
        ),
        (
            {
                "rule": "cmadaas.statistics_fields",
                "config": {"interval_start_field": "start"},
            },
            "statistics",
            "cmadaas",
            "invalid_rule_config",
        ),
        (
            {"rule": "common.value_unit", "config": {"source_unit": "K", "factor": 1}},
            "unit",
            "cmadaas",
            "invalid_rule_config",
        ),
    ],
)
def test_rule_errors_have_registered_reason_and_location(rule, duty, backend, reason):
    with pytest.raises(QueryError) as e:
        validate_rule(
            rule,
            backend=backend,
            responsibility=duty,
            path="/mappings/0/native/translate/" + duty,
        )
    assert e.value.code == "invalid_config" and e.value.context["reason"] == reason
    assert e.value.path.startswith("/mappings/0/native/translate/" + duty)


def cmadaas_mapping():
    return {
        "api_version": "cedarkit.mappings/v1",
        "id": "test.service",
        "version": "1",
        "backend": "cmadaas",
        "mappings": [
            {
                "id": "soil",
                "parameter_id": "cedarkit.st",
                "backend": "cmadaas",
                "applies_to": {},
                "when": {},
                "native": {
                    "match": {"fcstEle": "TEM"},
                    "translate": {
                        "vertical": {
                            "rule": "cmadaas.vertical_table",
                            "config": {"table": "soil"},
                        }
                    },
                },
                "evidence": [],
            }
        ],
        "tables": [
            {
                "id": "soil",
                "key_kind": "level_type_and_level",
                "applies_to": {},
                "evidence": [],
                "rows": [
                    {
                        "id": "deep",
                        "native": {"level_type": "106", "level": "25"},
                        "canonical": {
                            "level_type": "depth_below_land",
                            "level_unit": "m",
                            "layer": {"bottom": 0.4, "top": 0.1},
                        },
                    },
                    {
                        "id": "shallow",
                        "native": {"level_type": "106", "level": "5"},
                        "canonical": {
                            "level_type": "depth_below_land",
                            "level_unit": "m",
                            "layer": {"bottom": 0.1, "top": 0},
                        },
                    },
                ],
            }
        ],
    }


def test_joint_table_whole_pairs_equivalence_and_conflicts(parameters):
    m = cmadaas_mapping()
    assert Mappings(m).backend == "cmadaas"
    m["tables"][0]["rows"].append(
        {
            "id": "deep-equivalent",
            "native": {"level_type": "106", "level": "25.0"},
            "canonical": {
                "level_type": "depth_below_land",
                "level_unit": "cm",
                "layer": {"bottom": 40, "top": 10},
            },
        }
    )
    assert validate_resource_set(parameters, [m])
    m["mappings"][0]["parameter_id"] = "cedarkit.st-10-40"
    assert validate_resource_set(
        parameters, [m]
    )  # unrelated rows in a shared table remain valid
    m["tables"][0]["rows"][2]["native"]["level"] = "25"
    assert Mappings(
        m
    )  # an explicit duplicate key with equivalent canonical meaning is acceptable
    m["tables"][0]["rows"][2]["canonical"]["layer"]["bottom"] = 50
    with pytest.raises(QueryError) as e:
        Mappings(m)
    assert e.value.context["reason"] == "conflicting_table_rows"


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["tables"][0]["rows"][0]["native"].pop("level_type"),
        lambda m: m["tables"][0]["rows"][0]["native"].update(level=25),
        lambda m: m["tables"][0]["rows"][0]["native"].update(level=["25", "5"]),
        lambda m: m["tables"][0]["rows"][0]["canonical"].update(level=25),
        lambda m: m["tables"][0].update(rows=[]),
        lambda m: m["tables"][0]["rows"].append(deepcopy(m["tables"][0]["rows"][0])),
        lambda m: m["mappings"][0].update(backend="grib2"),
        lambda m: m["mappings"][0]["native"]["translate"]["vertical"]["config"].update(
            table="absent"
        ),
    ],
)
def test_bad_joint_table_resources(change):
    m = cmadaas_mapping()
    change(m)
    with pytest.raises(QueryError):
        Mappings(m)


def test_member_table_has_no_duplicate_role_semantics():
    m = cmadaas_mapping()
    m["mappings"][0]["native"]["translate"] = {
        "member": {"rule": "cmadaas.member_field", "config": {"table": "soil"}}
    }
    m["tables"][0].update(
        key_kind="code",
        rows=[{"id": "m0", "native": {"code": "CONTROL"}, "canonical": {"member": 0}}],
    )
    assert Mappings(m)
    m["tables"][0]["rows"][0]["canonical"]["member_role"] = "control"
    with pytest.raises(QueryError):
        Mappings(m)


def test_mapping_reference_overlap_and_ancestor_rules(parameters, mappings):
    mappings["mappings"][0]["native"]["translate"]["unit"]["config"] = {
        "source_unit": "K"
    }
    other = deepcopy(mappings["mappings"][0])
    other["id"] = "other"
    other["native"]["translate"]["unit"]["config"] = {"source_unit": "degC"}
    mappings["mappings"].append(other)
    with pytest.raises(QueryError) as e:
        validate_resource_set(parameters, [mappings])
    assert e.value.context["reason"] == "ambiguous_mapping"
    other["applies_to"]["product_version"] = "oper-2"
    assert validate_resource_set(parameters, [mappings])
    other["applies_to"]["product_version"] = "oper-1"
    other["native"]["translate"] = deepcopy(
        mappings["mappings"][0]["native"]["translate"]
    )
    assert validate_resource_set(parameters, [mappings])
    other["parameter_id"] = "cedarkit.t2m"
    assert validate_resource_set(parameters, [mappings])
    other["parameter_id"] = "absent"
    with pytest.raises(QueryError):
        validate_resource_set(parameters, [mappings])


def test_partial_resource_sets_and_product_context_validation(
    parameters, mappings, catalog
):
    r = validate_resource_set(
        parameters, [mappings]
    )  # DB export need not contain every product catalog
    with pytest.raises(QueryError):
        validate_product(r, "example", "orig", "oper-1")
    for kind in ["version", "time_zone", "population", "methods"]:
        c = deepcopy(catalog)
        p = c["datasets"][0]["products"][0]
        if kind == "version":
            p["version"] = "oper-2"
        elif kind == "time_zone":
            p["sources"][1]["time_zone"] = "Invalid/Place"
        elif kind == "population":
            p["populations"] = []
        elif kind == "methods":
            p["methods"] = [
                {
                    "parameter_id": "cedarkit.rain24.prob",
                    "method": {
                        "denominator": "valid_members",
                        "kind": "member_fraction",
                    },
                }
            ]
        with pytest.raises(QueryError):
            validate_resource_set(
                parameters, [mappings], c, products=[("example", "orig", "oper-1")]
            )
    with pytest.raises(QueryError):
        validate_resource_set(
            parameters,
            [mappings],
            catalog,
            products=[("example", "orig", "oper-1")],
            interface_contracts=set(),
        )
    catalog["datasets"][0]["products"][0]["populations"][0]["missing"][
        "min_members"
    ] = 32
    with pytest.raises(QueryError):
        Catalog(catalog)


def test_concrete_statistic_requires_method_not_implicit_default(
    parameters, mappings, catalog
):
    stats = parameters["parameters"][6]["fixed_conditions"]["statistics"]
    stats.pop("method")
    assert Parameters(parameters)  # valid broad/local declaration
    with pytest.raises(QueryError) as e:
        validate_resource_set(
            parameters, [mappings], catalog, products=[("example", "orig", "oper-1")]
        )
    assert e.value.context["reason"] == "missing_context"
    catalog["datasets"][0]["products"][0]["methods"] = [
        {
            "parameter_id": "cedarkit.rain24.prob",
            "method": {"kind": "member_fraction", "denominator": "expected_members"},
        }
    ]
    assert validate_resource_set(
        parameters, [mappings], catalog, products=[("example", "orig", "oper-1")]
    )


def test_name_resolution_fixed_conditions_and_variant_identity(parameters):
    r = validate_resource_set(parameters)
    a = prepare_queries([{"parameter": "t2m"}], r)
    b = prepare_queries(
        [{"parameter": "temperature", "level_type": "height_above_ground", "level": 2}],
        r,
    )
    assert a == b and a[0].parameter == "cedarkit.t2m"
    a = prepare_queries([{"parameter": "st(10-40)"}], r)
    b = prepare_queries(
        [
            {
                "parameter": "st",
                "level_type": "depth_below_land",
                "layer": {"bottom": 0.4, "top": 0.1},
            }
        ],
        r,
    )
    assert a == b and a[0].parameter == "cedarkit.st-10-40"
    assert prepare_queries([{"parameter": "st"}], r)[0].parameter == "cedarkit.st"
    with pytest.raises(QueryError):
        prepare_queries([{"parameter": "t2m", "level": [2, 10]}], r)
    with pytest.raises(QueryError):
        prepare_queries(
            [
                {
                    "parameter": ["t2m", "st(10-40)"],
                    "level_type": "height_above_ground",
                    "level": 2,
                }
            ],
            r,
        )
    with pytest.raises(QueryError):
        prepare_queries([{"parameter": "rain24p25", "member": 0}], r)
    assert prepare_queries([{"native": {"grib": {"parameterNumber": 0}}}], r)[
        0
    ].to_dict() == {"native": {"grib": {"parameterNumber": 0}}}


@pytest.mark.parametrize(
    "ref",
    [
        {"path": "x", "builtin": "default"},
        {"path": "https://example.test/p.json"},
        {"path": "*.yaml"},
        {"builtin": "default", "version": 1},
        {"builtin": "default", "sha256": "abcd"},
    ],
)
def test_bad_resource_references(ref):
    with pytest.raises(QueryError):
        Context({"parameter_registry": ref})


@pytest.mark.parametrize(
    "stats",
    [
        {
            "operation": "instantaneous",
            "period": {"kind": "trailing", "duration": "PT1H"},
        },
        {"operation": "mean", "period": {"kind": "since_initialization"}},
        {
            "operation": "mean",
            "period": {"kind": "trailing", "duration": "PT1H"},
            "of": {"parameter_id": "x"},
        },
        {
            "operation": "minimum",
            "event": {"operator": "ge", "threshold": {"value": 1, "unit": "mm"}},
        },
    ],
)
def test_local_statistical_contradictions_do_not_wait_for_inheritance(stats):
    with pytest.raises(QueryError):
        normalize_statistics(stats)


def test_source_unit_and_business_unit_do_not_default_each_other(parameters, mappings):
    mappings["mappings"][0]["native"]["translate"]["unit"]["config"] = {
        "source_unit": "degC"
    }
    r = validate_resource_set(parameters, [mappings])
    assert r.parameter("t")["unit"] == "K"
    assert (
        r.mappings[0]["mappings"][0]["native"]["translate"]["unit"]["config"][
            "source_unit"
        ]
        == "degC"
    )


def test_business_probability_unit_and_object_definition_are_required(
    parameters, mappings, catalog
):
    parameters["parameters"][6]["unit"] = "K"
    with pytest.raises(QueryError):
        validate_resource_set(
            parameters, [mappings], catalog, products=[("example", "orig", "oper-1")]
        )
    parameters["parameters"][6]["unit"] = "%"
    parameters["parameters"][6]["fixed_conditions"]["statistics"]["of"][
        "parameter_id"
    ] = "cedarkit.rain"
    with pytest.raises(QueryError):
        validate_resource_set(
            parameters, [mappings], catalog, products=[("example", "orig", "oper-1")]
        )


def test_single_resource_missing_reference_differs_from_product_context(parameters):
    m = cmadaas_mapping()
    m["mappings"][0]["native"]["translate"]["vertical"]["config"]["table"] = "absent"
    with pytest.raises(QueryError) as e:
        Mappings(m)
    assert e.value.context["reason"] == "missing_table"
    old = {"api_version": "reki.parameter-registry/v3", "entries": []}
    with pytest.raises(QueryError) as e:
        Parameters(old)
    assert e.value.code == "unsupported_version"


def test_ambiguous_names_can_be_disambiguated_by_declared_conditions(parameters):
    parameters["parameters"] = [
        {
            "parameter_id": "pressure_t",
            "name": "t",
            "unit": "K",
            "fixed_conditions": {"level_type": "pressure"},
        },
        {
            "parameter_id": "height_t",
            "name": "t",
            "unit": "K",
            "fixed_conditions": {"level_type": "height_above_ground"},
        },
    ]
    resources = validate_resource_set(parameters)
    assert (
        prepare_queries(
            [{"parameter": "t", "level_type": "pressure", "level": 850}], resources
        )[0].parameter
        == "pressure_t"
    )
    with pytest.raises(QueryError):
        prepare_queries(
            [{"parameter": "pressure_t"}], resources, parameter_ids=["height_t"]
        )


def test_multiple_table_mappings_can_supply_complementary_candidates(parameters):
    m = cmadaas_mapping()
    first = deepcopy(m["mappings"][0])
    first["id"] = "deep"
    first["native"]["translate"]["vertical"]["config"]["table"] = "deep-only"
    deep = deepcopy(m["tables"][0])
    deep["id"] = "deep-only"
    deep["rows"] = [deep["rows"][0]]
    m["mappings"].append(first)
    m["tables"][0]["rows"] = [m["tables"][0]["rows"][1]]
    m["tables"].append(deep)
    assert validate_resource_set(parameters, [m])
    equivalent = deepcopy(deep)
    equivalent["id"] = "same-deep"
    second = deepcopy(first)
    second["id"] = "equivalent"
    second["native"]["translate"]["vertical"]["config"]["table"] = "same-deep"
    m["tables"].append(equivalent)
    m["mappings"].append(second)
    assert validate_resource_set(parameters, [m])
    equivalent["rows"][0]["canonical"]["layer"]["bottom"] = 0.5
    with pytest.raises(QueryError) as e:
        validate_resource_set(parameters, [m])
    assert e.value.context["reason"] == "ambiguous_mapping"


def test_context_semantic_error_is_configuration_error():
    with pytest.raises(QueryError) as e:
        Context({"scope": {"level_type": "pressure", "level_unit": "m", "level": 850}})
    assert e.value.code == "invalid_config" and e.value.path == "/scope/level_unit"
