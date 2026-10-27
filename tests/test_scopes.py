import pytest

from cedarkit_query import QueryError, expand_queries, intersect_scopes


def test_large_configured_scope_does_not_expand_but_query_limit_remains():
    result = intersect_scopes(
        {"member": {"range": {"start": 0, "end": 1000000000, "every": 1}}},
        {"step": {"range": {"start": "PT0H", "end": "PT1000H", "every": "PT1H"}}},
    )
    assert result.member["range"]["end"] == 1000000000
    with pytest.raises(QueryError) as error:
        expand_queries([result], max_expansion=10000)
    assert error.value.code == "expansion_limit_exceeded"


def test_partial_bound_gains_type_and_scopes_conjoin():
    result = intersect_scopes(
        {"level": 850, "step": ["PT0H", "PT6H"]},
        {"level_type": "pressure", "step": "PT0H"},
    )
    assert result.to_dict() == {
        "level_type": "pressure",
        "level": 850,
        "level_unit": "hPa",
        "step": "PT0S",
    }
    assert intersect_scopes({"step": "PT0H"}, {"step": "PT6H"}) is None
