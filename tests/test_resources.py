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
