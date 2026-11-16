import json

import pytest
from cedarkit_query import (
    Catalog,
    Mappings,
    Parameters,
    QueryError,
    load_prevalidated_resource,
    load_value,
    prepare_queries,
    validate_resource_set,
)
from cedarkit_query import model
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


def test_prevalidated_resources_match_validated_models(catalog, monkeypatch):
    resources = {
        "catalog": catalog,
        "parameters": snapshot([BASE, VARIANT]),
        "mappings": {
            "api_version": "cedarkit.mappings/v1",
            "id": "test.grib2",
            "version": "1",
            "backend": "grib2",
            "tables": [],
            "mappings": [],
        },
    }
    expected = {kind: load_value(value, kind) for kind, value in resources.items()}

    def reject_validation(*args):
        pytest.fail("prevalidated resource ran the full validator")

    monkeypatch.setattr(model, "validate_value", reject_validation)
    for kind, value in resources.items():
        resource = load_prevalidated_resource(json.dumps(value), kind)
        assert resource == expected[kind]
        loaded = resource.to_dict()
        loaded["id"] = "changed"
        assert resource == expected[kind]
        with pytest.raises(AttributeError):
            resource.id = "changed"


@pytest.mark.parametrize(
    "text,code",
    [
        ("[]", "invalid_config"),
        ('{"api_version":"cedarkit.catalog/v2"}', "unsupported_version"),
        (
            '{"api_version":"cedarkit.catalog/v1","api_version":"cedarkit.catalog/v1"}',
            "invalid_config",
        ),
    ],
)
def test_prevalidated_resource_checks_json_and_protocol_version(text, code):
    with pytest.raises(QueryError) as caught:
        load_prevalidated_resource(text, "catalog")
    assert caught.value.code == code


def test_parameters_inherit_without_depending_on_record_order():
    parameters = Parameters(snapshot([VARIANT, BASE]))
    index = effective_parameters(parameters)
    assert index["cedarkit.t2m"]["unit"] == "K"
    (query,) = prepare_queries([{"parameter": "t2m"}], index)
    assert query.to_dict() == {"parameter": "cedarkit.t2m", **HEIGHT}


def test_resource_set_reuses_validated_models(monkeypatch):
    parameters = Parameters(snapshot([BASE, VARIANT]))
    product = {
        "id": "original",
        "version": "1",
        "scope": {},
        "provides": [{"parameter": "cedarkit.t2m"}],
        "populations": [],
        "methods": [],
        "evidence": [],
        "sources": [{"id": "file", "kind": "file"}],
    }
    catalog = Catalog(
        {
            "api_version": "cedarkit.catalog/v1",
            "id": "test.catalog",
            "version": "1",
            "datasets": [{"id": "test", "products": [product]}],
        }
    )
    mapping = {
        "id": "t2m",
        "parameter_id": "cedarkit.t2m",
        "backend": "grib2",
        "applies_to": {},
        "when": {},
        "evidence": [],
        "native": {"match": {"shortName": "2t"}},
    }
    mappings = Mappings(
        {
            "api_version": "cedarkit.mappings/v1",
            "id": "test.grib2",
            "version": "1",
            "backend": "grib2",
            "tables": [],
            "mappings": [mapping],
        }
    )
    expected = validate_resource_set(
        parameters.to_dict(), [mappings.to_dict()], catalog.to_dict(), "test"
    )
    validate_value = model.validate_value

    def reject_revalidation(value, kind):
        assert kind not in {"parameters", "catalog", "mappings"}
        return validate_value(value, kind)

    monkeypatch.setattr(model, "validate_value", reject_revalidation)
    assert validate_resource_set(parameters, [mappings], catalog, "test") == expected
    with pytest.raises(QueryError, match="dataset is absent"):
        validate_resource_set(parameters, [mappings], catalog, "missing")


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
    import yaml

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
