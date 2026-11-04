# Use a native request

Load the request with `Request.load(path)` or build it from a mapping. Use `Query` to set native predicates and optional coordinate constraints. Use `reki.from_source(request.source, context=request.context)` and `source.sel(request.queries)` to execute the selection.

See [the native protocol](protocol-v1.md) and [the example request](../examples/request.json).
