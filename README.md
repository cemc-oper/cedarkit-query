# cedarkit-query

Immutable Query, Request, Context, Catalog, report and error models for selecting native data. The package contains JSON Schemas and selection algebra. File discovery and GRIB decoding are handled by reki.

```python
from cedarkit_query import Query, Request

query = Query(native={"grib": {
    "discipline": 0,
    "parameterCategory": 0,
    "parameterNumber": 0,
    "typeOfFirstFixedSurface": 103,
    "scaleFactorOfFirstFixedSurface": 0,
    "scaledValueOfFirstFixedSurface": 2,
}})
request = Request({
    "api_version": "cedarkit.request/v1",
    "source": {"kind": "file", "path": "original.grib2"},
    "queries": [query.to_dict()],
})
```

Use encoded scalar keys and their numeric codes to distinguish local tables, templates, fixed surfaces, intervals and ensemble members. Optional time and vertical selectors describe coordinates. Field values and their units are outside this declaration contract.

Version 0.2 removes the former business parameter and mapping models, the `parameter` selector, and product provision checks. Applications that used these contracts supply their native selectors or maintain their business vocabulary separately.

- [Request and Query contracts](docs/protocol-v1.md)
- [Selection algebra](docs/normalization-v1.md)
- [Native declarations in Python](docs/using-v1.md)
