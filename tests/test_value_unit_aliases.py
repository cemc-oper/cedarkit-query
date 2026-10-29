"""Finite literal equivalences observed in original CEMC GRIB messages."""

from decimal import Decimal

import pytest

from cedarkit_query.normalize import convert_quantity, unit_info
from cedarkit_query import QueryError


@pytest.mark.parametrize(
    "literal,target",
    [
        ("m s**-1", "m/s"),
        ("m s-1", "m/s"),
        ("kg kg**-1", "kg/kg"),
        ("kg kg-1", "kg/kg"),
    ],
)
def test_eccodes_and_declared_unit_literals_are_exact_equivalents(literal, target):
    assert unit_info(literal) == unit_info(target)
    assert convert_quantity(Decimal("0.125"), literal, target) == Decimal("0.125")


@pytest.mark.parametrize(
    "source,target",
    [
        ("kg kg**-1", "m3/m3"),
        ("kg kg**-1", "%"),
        ("m s**-1", "m"),
        ("m s**-1", "km/h"),
    ],
)
def test_aliases_do_not_infer_physical_or_scaled_conversions(source, target):
    with pytest.raises(QueryError):
        convert_quantity(1, source, target)
