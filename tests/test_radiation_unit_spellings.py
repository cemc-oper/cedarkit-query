"""Flux and integrated energy have separate finite unit spelling aliases."""

from decimal import Decimal

import pytest

from cedarkit_query import QueryError
from cedarkit_query.normalize import convert_quantity, unit_name


@pytest.mark.parametrize(
    "source,target",
    [("W m**-2", "W/m2"), ("W m-2", "W/m2"), ("J m**-2", "J/m2"), ("J m-2", "J/m2")],
)
def test_radiation_spellings_preserve_values_without_time_conversion(source, target):
    assert unit_name(source) == target
    assert convert_quantity(Decimal("-0.125"), source, target) == Decimal("-0.125")


@pytest.mark.parametrize(
    "source,target",
    [("J m**-2", "W/m2"), ("W m**-2", "J/m2"), ("J/m2", "J"), ("W/m2", "W")],
)
def test_radiation_aliases_do_not_integrate_average_or_change_area(source, target):
    with pytest.raises(QueryError):
        convert_quantity(1, source, target)
