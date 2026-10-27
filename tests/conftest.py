from copy import deepcopy

import pytest


def example(name):
    import json
    from pathlib import Path

    return json.loads(
        (Path(__file__).parents[1] / "examples" / (name + ".json")).read_text()
    )


@pytest.fixture
def parameters():
    return example("parameters")


@pytest.fixture
def mappings():
    return example("mappings")


@pytest.fixture
def catalog():
    return example("catalog")


@pytest.fixture
def report():
    summary = {
        "status": "complete",
        "expected_count": 1,
        "satisfied_count": 1,
        "field_count": 1,
        "missing": [],
        "reasons": [],
    }
    return {
        "schema_version": 1,
        "completeness": "available",
        "context": {},
        "overall": deepcopy(summary),
        "queries": [
            {
                "index": 0,
                "query": {"parameter": "cedarkit.t"},
                "summary": deepcopy(summary),
                "origins": [{"step_index": 0, "query_index": 0}],
            }
        ],
        "translations": [
            {
                "query_index": 0,
                "coverage": {"status": "passed", "message": "controlled definition"},
                "checks": [
                    {
                        "code": "request_association_verified",
                        "status": "passed",
                        "message": "controlled association",
                    }
                ],
                "branches": [
                    {
                        "index": 0,
                        "backend": "grib2",
                        "scope": {},
                        "candidate_filter": {"parameterNumber": 0},
                        "residual": {},
                        "basis": [{"kind": "builtin", "rule": "grib.fixed_surfaces"}],
                        "execution": {"status": "exhausted"},
                    }
                ],
            }
        ],
    }
