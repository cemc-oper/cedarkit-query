from copy import deepcopy

from cedarkit_query import analyze_product_mappings, validate_resource_set


def analyze(parameters, mappings, catalog, provides, *, changes=None, scope=None):
    catalog = deepcopy(catalog)
    product = catalog["datasets"][0]["products"][0]
    product["provides"] = provides
    mappings = deepcopy(mappings)
    if changes:
        changes(mappings)
    resources = validate_resource_set(parameters, [mappings], catalog)
    return analyze_product_mappings(resources, "example", "orig", "oper-1", scope=scope)


def pressure(level, **extra):
    return {
        "parameter": "cedarkit.t",
        "level_type": "pressure",
        "level": level,
        **extra,
    }


def test_time_applicability_is_checked(parameters, mappings, catalog):
    def change(m):
        m["mappings"][0]["applies_to"]["time"] = "2026-01-01T00:00:00Z"

    result = analyze(
        parameters,
        mappings,
        catalog,
        [pressure(850, time="2026-02-01T00:00:00Z")],
        changes=change,
    )
    assert result["rows"][0]["product_context_matches"] is True
    assert result["provides"][0]["static_status"] == "missing"
    assert result["rows"][0]["provided_indexes"] == ()


def test_when_rejects_uncovered_levels(parameters, mappings, catalog):
    def change(m):
        m["mappings"][0]["when"] = {"level_type": "pressure", "level": 500}

    result = analyze(
        parameters, mappings, catalog, [pressure([500, 850])], changes=change
    )
    row = result["provides"][0]
    assert row["static_status"] == "partial"
    assert row["uncovered_cell_count"] == 1
    assert row["uncovered_example"]["level"] == 850


def test_disjoint_rows_can_cover_one_finite_domain(parameters, mappings, catalog):
    def change(m):
        row = m["mappings"][0]
        row["when"] = {"level_type": "pressure", "level": 500}
        other = deepcopy(row)
        other["id"] = "lower"
        other["when"]["level"] = 850
        m["mappings"].append(other)

    result = analyze(
        parameters, mappings, catalog, [pressure([500, 850])], changes=change
    )
    assert result["provides"][0]["static_status"] == "covered"
    assert result["provides"][0]["checked_cell_count"] == 2
    assert result["provides"][0]["translation_coverage"] == "pending_I06"


def test_paired_provides_are_not_crossed(parameters, mappings, catalog):
    def change(m):
        row = m["mappings"][0]
        row["when"] = {"level_type": "pressure", "level": 500, "step": "PT12H"}
        other = deepcopy(row)
        other["id"] = "lower"
        other["when"].update(level=850, step="PT6H")
        m["mappings"].append(other)

    result = analyze(
        parameters,
        mappings,
        catalog,
        [pressure(500, step="PT6H"), pressure(850, step="PT12H")],
        changes=change,
    )
    assert [r["static_status"] for r in result["provides"]] == ["missing", "missing"]


def test_mapping_parent_is_not_assumed_to_cover_variant(parameters, mappings, catalog):
    result = analyze(parameters, mappings, catalog, [{"parameter": "cedarkit.t2m"}])
    assert result["provides"][0]["parameter_id"] == "cedarkit.t2m"
    assert result["provides"][0]["static_status"] == "missing"


def test_context_narrows_product_domain_before_coverage(parameters, mappings, catalog):
    def change(m):
        m["mappings"][0]["when"] = {"level_type": "pressure", "level": 850}

    result = analyze(
        parameters,
        mappings,
        catalog,
        [pressure([500, 850])],
        changes=change,
        scope={"level_type": "pressure", "level": 850},
    )
    assert result["provides"][0]["static_status"] == "covered"


def test_large_partial_domain_is_pending_not_an_execution_limit_error(
    parameters, mappings, catalog
):
    def change(m):
        m["mappings"][0]["when"] = {"member": 0}

    result = analyze(
        parameters,
        mappings,
        catalog,
        [pressure(850, member={"range": {"start": 0, "end": 1000000000, "every": 1}})],
        changes=change,
    )
    assert result["provides"][0]["static_status"] == "undetermined"
    assert result["provides"][0]["reason"] == "static_check_budget_exceeded"


def test_continuous_partition_without_proof_stays_pending(
    parameters, mappings, catalog
):
    def change(m):
        m["mappings"][0]["when"] = {
            "level_type": "pressure",
            "level": {"range": {"start": 500, "end": 700}},
        }

    result = analyze(
        parameters,
        mappings,
        catalog,
        [pressure({"range": {"start": 500, "end": 850}})],
        changes=change,
    )
    assert result["provides"][0]["static_status"] == "undetermined"


def test_declared_time_zone_offsets_are_normalized(parameters, mappings, catalog):
    def change(m):
        m["mappings"][0]["applies_to"]["time"] = "2026-01-01T08:00:00+08:00"

    result = analyze(
        parameters,
        mappings,
        catalog,
        [pressure(850, time="2026-01-01T00:00:00Z")],
        changes=change,
    )
    assert result["provides"][0]["static_status"] == "covered"
