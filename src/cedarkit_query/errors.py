"""Public errors. No backend imports or conversion of arbitrary exceptions."""

import re

from ._json import freeze, plain, thaw

QUERY_CODES = frozenset(
    {
        "invalid_request",
        "unsupported_version",
        "invalid_config",
        "invalid_query",
        "constraint_conflict",
        "unsupported",
        "expansion_limit_exceeded",
        "no_match",
        "multiple_matches",
        "missing_fields",
        "completeness_undetermined",
        "duplicate_field",
        "incompatible_grid",
        "multiple_variables",
    }
)
SOURCE_CODES = frozenset(
    {
        "resource_not_found",
        "permission_denied",
        "authentication_failed",
        "timeout",
        "decode_failed",
        "io_error",
        "service_error",
        "metadata_interpretation_failed",
        "incomplete_response",
    }
)
METADATA_REASONS = frozenset(
    {
        "missing_native_metadata",
        "unmapped_native_value",
        "invalid_vertical_metadata",
        "inconsistent_native_metadata",
        "fixed_definition_mismatch",
        "invalid_time_metadata",
        "invalid_member_metadata",
        "invalid_statistics_metadata",
        "unsupported_native_encoding",
    }
)


class _Error:
    @property
    def code(self):
        return self._data["code"]

    @property
    def path(self):
        return self._data["path"]

    @property
    def message(self):
        return self._data["message"]

    @property
    def context(self):
        return self._data["context"]

    def _init(self, code, path, message, context):
        if (
            not isinstance(path, str)
            or re.fullmatch(r"(?:/(?:[^~]|~[01])*)?", path) is None
        ):
            raise ValueError("path must be a JSON Pointer")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be nonempty")
        context = {} if context is None else plain(context)
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        self._data = freeze(
            {
                "api_version": "cedarkit.error/v1",
                "code": code,
                "path": path,
                "message": message,
                "context": context,
            }
        )
        super().__init__(f"{code} at {path or '/'}: {message}")

    def to_dict(self):
        return thaw(self._data)


class QueryError(_Error, ValueError):
    def __init__(self, code, path="", message="Query failed", context=None):
        if code not in QUERY_CODES:
            raise ValueError(f"unknown QueryError code: {code}")
        if (
            code in {"missing_fields", "completeness_undetermined"}
            and type(self) is QueryError
        ):
            raise ValueError(
                "completeness codes require CompletenessError and a report"
            )
        self._init(code, path, message, context)


class SourceError(_Error, RuntimeError):
    def __init__(self, code, path="", message="Source failed", context=None):
        if code not in SOURCE_CODES:
            raise ValueError(f"unknown SourceError code: {code}")
        reason = (context or {}).get("reason")
        if code == "metadata_interpretation_failed" and reason not in METADATA_REASONS:
            raise ValueError("metadata interpretation requires a registered reason")
        if code == "incomplete_response" and reason not in {
            "response_truncated",
            "pagination_incomplete",
        }:
            raise ValueError("incomplete response requires a registered reason")
        self._init(code, path, message, context)


class CompletenessError(QueryError):
    def __init__(
        self,
        code,
        path="",
        message="Completeness check failed",
        *,
        report,
        context=None,
    ):
        from .model import QueryReport

        if code not in {"missing_fields", "completeness_undetermined"}:
            raise ValueError("invalid CompletenessError code")
        self._report = (
            report if isinstance(report, QueryReport) else QueryReport(report)
        )
        status = self._report.overall["status"]
        if status != ("missing" if code == "missing_fields" else "unchecked"):
            raise ValueError("completeness code and report disagree")
        super().__init__(code, path, message, context)

    @property
    def report(self):
        return self._report

    def to_dict(self):
        return {**super().to_dict(), "report": self.report.to_dict()}
