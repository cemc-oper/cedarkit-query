"""Observed height and inverse-time spellings retain their physical dimensions."""

import pytest

from cedarkit_query import QueryError
from cedarkit_query.normalize import convert_quantity, unit_name


@pytest.mark.parametrize("source,target", [("gpm", "Gpm"), ("s**-1", "s-1")])
def test_instantaneous_spellings_preserve_signed_values(source, target):
    assert unit_name(source) == target
    assert convert_quantity(-0.125, source, target) == -0.125


@pytest.mark.parametrize(
    "source,target",
    [("gpm", "m"), ("gpm", "m2/s2"), ("s**-1", "1"), ("s**-1", "s-2")],
)
def test_spelling_aliases_do_not_convert_geopotential_or_change_dimensions(
    source, target
):
    with pytest.raises(QueryError):
        convert_quantity(1, source, target)
