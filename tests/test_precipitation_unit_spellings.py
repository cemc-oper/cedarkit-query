"""Mass-per-area spellings are aliases; precipitation depth is not a unit alias."""

import pytest

from cedarkit_query import QueryError
from cedarkit_query.normalize import convert_quantity, unit_name


@pytest.mark.parametrize("source", ["kg m**-2", "kg m-2"])
def test_mass_per_area_spellings_do_not_imply_water_depth(source):
    assert unit_name(source) == "kg/m2"
    assert convert_quantity(3, source, "kg/m2") == 3
    with pytest.raises(QueryError):
        convert_quantity(3, source, "mm")
