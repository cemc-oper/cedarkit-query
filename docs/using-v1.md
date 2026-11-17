# Use a native request

Load the request with `Request.load(path)` or build it from a mapping. Use `Query` to set native predicates and optional coordinate constraints. Use `reki.from_source(request.source, context=request.context)` and `source.sel(request.queries)` to execute the selection.

See [the native protocol](protocol-v1.md) and [the example request](../examples/request.json).

## Validate GRIB2 definitions for a file source

`validate_standalone_grib2(parameters, mappings)` accepts a parameter registry and a single GRIB2 mapping resource as dictionaries or validated models. It returns effective definitions by stable ID for `prepare_queries`. Each name and alias must resolve within the registry. Mapping and table `applies_to` scopes must be empty. Use `when` for query conditions and `native.match` for GRIB2 header predicates. Two rules may coexist when their effective query conditions or their native predicates are disjoint.

Use `validate_resource_set(parameters, mappings, catalog, dataset_id)` to validate mappings selected by a catalog product.
