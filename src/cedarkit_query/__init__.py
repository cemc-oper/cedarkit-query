"""CedarKit's single query/request and resource contracts. No business I/O."""

from .errors import CompletenessError, QueryError, SourceError
from .model import (
    Catalog,
    Context,
    ErrorValue,
    Query,
    QueryExplanation,
    QueryReport,
    Request,
    dump_value,
    get_schema,
    load_request,
    load_value,
    validate_value,
)
from .normalize import (
    DEFAULT_MAX_EXPANSION,
    combine_queries,
    constrain_query,
    expand_queries,
    intersect_queries,
    intersect_scopes,
    normalize_query,
    query_identity,
)

__all__ = [
    "Query",
    "Request",
    "QueryReport",
    "QueryExplanation",
    "QueryError",
    "CompletenessError",
    "SourceError",
    "ErrorValue",
    "Catalog",
    "Context",
    "load_value",
    "load_request",
    "dump_value",
    "get_schema",
    "validate_value",
    "normalize_query",
    "expand_queries",
    "intersect_queries",
    "intersect_scopes",
    "constrain_query",
    "combine_queries",
    "query_identity",
    "DEFAULT_MAX_EXPANSION",
]
