"""Restricted, one-line-per-request grid query text syntax."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .model import QueryError, Request, RequestList

_ACTIONS = {"validate", "plan", "list", "locate", "read"}
_FIELDS = {
    "dataset": "/query/dataset",
    "parameter": "/query/parameter",
    "reference_time": "/query/reference_time",
    "lead_time": "/query/lead_time",
    "vertical.type": "/query/vertical/type",
    "vertical.unit": "/query/vertical/unit",
    "vertical.values": "/query/vertical/values",
    "statistic.kind": "/query/statistic/kind",
    "statistic.window": "/query/statistic/window",
    "member": "/query/member",
    "native_grid.id": "/query/native_grid/id",
    "policy.cardinality": "/policy/cardinality",
    "policy.missing": "/policy/missing",
}
_LIST_FIELDS = {"parameter", "reference_time", "lead_time", "vertical.values", "member"}
_RANGE_FIELDS = {"reference_time", "lead_time", "vertical.values"}
_ATOM = re.compile(r"[A-Za-z0-9._:+/\-]+\Z")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@dataclass(frozen=True)
class _Token:
    value: str
    column: int


def _error(path: str, message: str, line: int, column: int, *, code: str = "invalid_query") -> QueryError:
    return QueryError(code, path, message, {"line": line, "column": column})


def _token_path(fragment: str) -> str:
    key, separator, _ = fragment.partition("=")
    return _FIELDS.get(key, "") if separator else ""


def _tokens(source: str, line: int) -> list[_Token]:
    result = []
    index = 0
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        start = index
        quoted = escaped = False
        bracket = 0
        while index < len(source):
            char = source[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "[":
                bracket += 1
                if bracket > 1:
                    raise _error(_token_path(source[start:index]), "nested lists are not supported", line, index + 1)
            elif char == "]":
                bracket -= 1
                if bracket < 0:
                    raise _error(_token_path(source[start:index]), "unexpected closing bracket", line, index + 1)
            elif char.isspace():
                if bracket:
                    raise _error(_token_path(source[start:index]),
                                 "list whitespace must be inside a quoted string", line, index + 1)
                break
            index += 1
        if quoted or bracket:
            raise _error(_token_path(source[start:index]), "unterminated quote or list", line, start + 1)
        result.append(_Token(source[start:index], start + 1))
    return result


def _atom(source: str, key: str, line: int, column: int) -> str:
    path = _FIELDS[key]
    if source.startswith('"'):
        try:
            value = json.loads(source)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise _error(path, "invalid JSON quoted string", line, column) from exc
        if not isinstance(value, str):
            raise _error(path, "quoted value must be a string", line, column)
    elif _ATOM.fullmatch(source):
        value = source
    else:
        raise _error(path, "special characters require a JSON quoted string", line, column)
    if not value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _error(path, "value must be a non-empty Unicode string", line, column)
    return value


def _number(source: str, line: int, column: int) -> int | float:
    path = _FIELDS["vertical.values"]
    if not _NUMBER.fullmatch(source):
        raise _error(path, "vertical value must be a decimal number", line, column)
    try:
        number = Decimal(source)
    except InvalidOperation as exc:
        raise _error(path, "invalid decimal number", line, column) from exc
    if number.adjusted() > 308:
        raise _error(path, "decimal number exceeds text numeric range", line, column)
    try:
        value = int(number) if number == number.to_integral_value() else float(number)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise _error(path, "invalid decimal number", line, column) from exc
    if isinstance(value, float) and (not math.isfinite(value) or (value == 0 and number != 0)):
        raise _error(path, "decimal number exceeds JSON range or precision", line, column)
    return value


def _parts(source: str, key: str, line: int, column: int) -> list[tuple[str, int]]:
    parts = []
    quoted = escaped = False
    start = 0
    for index, char in enumerate(source):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            parts.append((source[start:index], start))
            start = index + 1
    parts.append((source[start:], start))
    if not source or any(not part for part, _ in parts):
        raise _error(_FIELDS[key], "list requires non-empty values", line, column)
    return parts


def _scalar(source: str, key: str, line: int, column: int) -> object:
    value = _atom(source, key, line, column)
    if key == "vertical.values":
        return _number(value, line, column)
    if key == "member":
        role, separator, identity = value.partition(":")
        if not separator or not identity:
            raise _error(_FIELDS[key], "member requires role:id", line, column)
        return {"role": role, "id": identity}
    return value


def _value(source: str, key: str, line: int, column: int) -> object:
    path = _FIELDS[key]
    if source.startswith("["):
        if key not in _LIST_FIELDS or not source.endswith("]"):
            raise _error(path, "list is not supported for this field", line, column)
        return [_scalar(part, key, line, column + 1 + offset)
                for part, offset in _parts(source[1:-1], key, line, column)]
    if key in _RANGE_FIELDS and ".." in source and not source.startswith('"'):
        limits = source.split("..")
        if len(limits) != 2 or "/" not in limits[1]:
            raise _error(path, "range requires start..stop/step", line, column)
        stop, step = limits[1].split("/", 1)
        if not limits[0] or not stop or not step:
            raise _error(path, "range requires start, stop and step", line, column)
        return {"start": _scalar(limits[0], key, line, column),
                "stop": _scalar(stop, key, line, column + len(limits[0]) + 2),
                "step": _scalar(step, key, line, column + len(limits[0]) + len(stop) + 3),
                "closed": "both"}
    return _scalar(source, key, line, column)


def _request(source: str, line: int) -> Request:
    tokens = _tokens(source, line)
    if not tokens:
        raise _error("/action", "request line is empty", line, 1)
    action = tokens[0].value.lower()
    if action not in _ACTIONS or tokens[0].value not in (action, action.upper()):
        raise _error("/action", "unknown action", line, tokens[0].column)
    request: dict[str, object] = {"api_version": "cedarkit.request/v1", "action": action,
                                  "query": {"api_version": "cedarkit.query/v1", "domain": "grid"}}
    positions = {"/action": tokens[0].column, "/query": tokens[0].column}
    seen = set()
    for token in tokens[1:]:
        key, separator, raw = token.value.partition("=")
        if not separator or not key:
            raise _error("", "expected key=value", line, token.column)
        path = _FIELDS.get(key, "/" + key.replace(".", "/"))
        if key not in _FIELDS:
            raise _error(path, "unknown text field", line, token.column)
        if key in seen:
            raise _error(path, "duplicate text field", line, token.column)
        seen.add(key)
        positions[path] = token.column
        item = _value(raw, key, line, token.column + len(key) + 1)
        target = request
        segments = path.strip("/").split("/")
        for segment in segments[:-1]:
            target = target.setdefault(segment, {})
        target[segments[-1]] = item
    required = ["dataset"]
    if action in {"plan", "locate", "read"}:
        required.extend(("parameter", "reference_time", "lead_time"))
    for key in required:
        if key not in seen:
            raise _error(_FIELDS[key], "required text field is missing", line, tokens[0].column)
    try:
        return Request(request)
    except QueryError as exc:
        parent = exc.path
        while parent not in positions and parent:
            parent = parent.rpartition("/")[0]
        raise _error(exc.path, exc.message, line, positions.get(parent, tokens[0].column), code=exc.code) from exc


def parse_text_request(text: str) -> Request:
    """Parse exactly one restricted text request into the public Request model."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    lines = [(number, source) for number, source in enumerate(text.splitlines(), 1) if source.strip()]
    if len(lines) != 1:
        line = lines[1][0] if len(lines) > 1 else 1
        raise _error("", "expected exactly one request line", line, 1)
    number, source = lines[0]
    return _request(source, number)


def parse_text_requests(text: str) -> RequestList:
    """Parse independent request lines separated by stand-alone ``---`` lines."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    lines = [(number, source) for number, source in enumerate(text.splitlines(), 1) if source.strip()]
    if not lines:
        raise _error("", "request list is empty", 1, 1)
    requests = []
    expect_request = True
    for number, source in lines:
        if source.strip() == "---":
            if expect_request:
                raise _error("", "separator needs a preceding request", number, 1)
            expect_request = True
        else:
            if not expect_request:
                raise _error("", "requests must be separated by ---", number, 1)
            try:
                requests.append(_request(source, number))
            except QueryError as exc:
                raise QueryError(exc.code, f"/requests/{len(requests)}{exc.path}", exc.message,
                                 exc.context) from exc
            expect_request = False
    if expect_request:
        raise _error("", "separator needs a following request", lines[-1][0], 1)
    return RequestList({"api_version": "cedarkit.request-list/v1",
                        "requests": [request.to_dict() for request in requests]})
