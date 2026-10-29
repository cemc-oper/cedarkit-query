"""Compare each mapping's combined fixed and applicability conditions."""

from copy import deepcopy

import pytest

from cedarkit_query import QueryError, validate_resource_set


@pytest.mark.parametrize("fixed_in_first", [True, False])
@pytest.mark.parametrize("soil", [False, True])
def test_mapping_overlap_uses_fixed_cross_when(
    parameters, mappings, fixed_in_first, soil
):
    fixed = {"level_type": "pressure", "level": 1000, "level_unit": "hPa"}
    when = {"level_type": "pressure", "level": [925, 850], "level_unit": "hPa"}
    if soil:
        fixed = {"level_type": "depth_below_land", "layer": {"top": 0, "bottom": 0.1}}
        when = {
            "level_type": "depth_below_land",
            "layer": [{"top": 0.1, "bottom": 0.3}, {"top": 0.3, "bottom": 0.6}],
        }
    parameters["parameters"] = [
        {
            "parameter_id": "fixed",
            "name": "fixed",
            "unit": "K",
            "fixed_conditions": fixed,
        },
        {"parameter_id": "generic", "name": "generic", "unit": "K"},
    ]
    a = deepcopy(mappings["mappings"][0])
    b = deepcopy(a)
    a.update(id="a", parameter_id="fixed", when={})
    b.update(
        id="b",
        parameter_id="generic",
        when=when,
    )
    mappings["mappings"] = [a, b] if fixed_in_first else [b, a]
    # Each mapping covers a different level even though its fixed conditions
    # and its when conditions considered separately have common possibilities.
    assert validate_resource_set(parameters, [mappings])
    b["when"]["layer" if soil else "level"].append(fixed["layer"] if soil else 1000)
    with pytest.raises(QueryError) as error:
        validate_resource_set(parameters, [mappings])
    assert error.value.context["reason"] == "ambiguous_mapping"
