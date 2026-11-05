import pytest
from cedarkit_query import Parameters, QueryError, prepare_queries
from cedarkit_query.resources import effective_parameters

BASE = {"parameter_id": "cedarkit.t", "name": "t", "unit": "K"}
HEIGHT = {"level_type": "height_above_ground", "level": 2, "level_unit": "m"}
VARIANT = {
    "parameter_id": "cedarkit.t2m",
    "name": "t2m",
    "parent": "cedarkit.t",
    "fixed_conditions": HEIGHT,
}


def snapshot(rows):
    return {
        "api_version": "cedarkit.parameters/v1",
        "id": "test.parameters",
        "version": "1",
        "parameters": rows,
    }


def test_parameters_inherit_without_depending_on_record_order():
    parameters = Parameters(snapshot([VARIANT, BASE]))
    index = effective_parameters(parameters)
    assert index["cedarkit.t2m"]["unit"] == "K"
    (query,) = prepare_queries([{"parameter": "t2m"}], index)
    assert query.to_dict() == {"parameter": "cedarkit.t2m", **HEIGHT}


@pytest.mark.parametrize(
    "rows,path",
    [
        ([VARIANT], "/parent"),
        ([{**BASE, "parent": "cedarkit.t2m"}, VARIANT], "/parent"),
        ([BASE, BASE], "/parameter_id"),
        (
            [{**BASE, "fixed_conditions": {**HEIGHT, "level": 10}}, VARIANT],
            "/fixed_conditions/level",
        ),
    ],
)
def test_invalid_parameter_inheritance_is_rejected(rows, path):
    with pytest.raises(QueryError) as caught:
        Parameters(snapshot(rows))
    assert caught.value.code == "invalid_config"
    assert caught.value.path.endswith(path)


def test_ensemble_statistic_is_a_named_product_not_an_individual_member():
    statistic = {
        "parameter_id": "test.ensemble_temperature",
        "name": "ensemble_temperature",
        "unit": "K",
        "fixed_conditions": {
            "statistics": {
                "over": "ensemble",
                "operation": "mean",
                "of": {"parameter_id": "cedarkit.t"},
            },
        },
    }
    index = effective_parameters(snapshot([BASE, statistic]))
    (query,) = prepare_queries([{"parameter": statistic["name"]}], index)
    assert query.to_dict() == {"parameter": statistic["parameter_id"]}
    with pytest.raises(QueryError) as caught:
        prepare_queries([{"parameter": statistic["name"], "member": 0}], index)
    assert (caught.value.code, caught.value.path) == ("constraint_conflict", "/member")


def test_time_statistic_can_inherit_operation_and_refine_period():
    rain = {
        "parameter_id": "test.rain",
        "name": "rain",
        "unit": "mm",
        "fixed_conditions": {
            "statistics": {"over": "time", "operation": "accumulation"}
        },
    }
    since_initialization = {
        "parameter_id": "test.rain.since-init",
        "name": "rain-since-init",
        "parent": "test.rain",
        "fixed_conditions": {
            "statistics": {"period": {"kind": "since_initialization"}}
        },
    }
    params = effective_parameters(snapshot([since_initialization, rain]))
    assert params["test.rain.since-init"]["fixed_conditions"]["statistics"] == {
        "over": "time",
        "operation": "accumulation",
        "period": {"kind": "since_initialization"},
    }
    since_initialization["fixed_conditions"]["statistics"]["operation"] = "maximum"
    with pytest.raises(QueryError, match="conflicting inherited statistic"):
        Parameters(snapshot([since_initialization, rain]))


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"operation": "accumulation", "period": {"kind": "since_initialization"}},
        {
            "period": {"kind": "trailing", "duration": "PT3H"},
            "response_profile": "derived_grid",
        },
    ],
)
def test_service_statistics_config_has_the_same_three_resource_forms(config):
    import json

    import yaml
    from cedarkit_query import Mappings, load_value

    resource = {
        "api_version": "cedarkit.mappings/v1",
        "id": "test.service",
        "version": "1",
        "backend": "cmadaas",
        "tables": [],
        "mappings": [
            {
                "id": "rain",
                "backend": "cmadaas",
                "parameter_id": "test.rain",
                "applies_to": {},
                "when": {},
                "native": {
                    "match": {"fcstEle": "TPE03"},
                    "translate": {
                        "statistics": {
                            "rule": "cmadaas.statistics_fields",
                            "config": config,
                        }
                    },
                },
                "evidence": ["reviewed service contract"],
            }
        ],
    }
    expected = Mappings(resource)
    assert load_value(json.dumps(resource), "mappings") == expected
    assert load_value(yaml.safe_dump(resource), "mappings", format="yaml") == expected
    if config:
        resource["mappings"][0]["native"]["translate"]["statistics"]["config"][
            "period"
        ] = {"kind": "trailing"}
        with pytest.raises(QueryError):
            Mappings(resource)
