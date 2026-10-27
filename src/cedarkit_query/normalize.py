"""Pure canonicalization and bounded selection algebra; no source or resource I/O."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction

from ._json import canonical, plain
from .errors import QueryError

DEFAULT_MAX_EXPANSION = 10000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
_DURATION = re.compile(
    r"^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)
_VERTICAL_UNITS = {
    "pressure": ("hPa", {"Pa": Decimal("0.01"), "hPa": Decimal(1)}),
    "height_above_ground": (
        "m",
        {"m": Decimal(1), "cm": Decimal("0.01"), "km": Decimal(1000)},
    ),
    "height_above_sea": (
        "m",
        {"m": Decimal(1), "cm": Decimal("0.01"), "km": Decimal(1000)},
    ),
    "depth_below_land": (
        "m",
        {"m": Decimal(1), "cm": Decimal("0.01"), "km": Decimal(1000)},
    ),
}
NAMED_LEVELS = frozenset(
    {"surface", "atmosphere_top", "entire_atmosphere", "mean_sea_level"}
)
_UNIT_ALIASES = {
    "pascal": "Pa",
    "hectopascal": "hPa",
    "meter": "m",
    "metre": "m",
    "centimeter": "cm",
    "centimetre": "cm",
    "kilometer": "km",
    "kilometre": "km",
    "kelvin": "K",
    "Celsius": "degC",
    "degrees_Celsius": "degC",
    "percent": "%",
}


def _fail(path, message, code="invalid_query", **context):
    raise QueryError(code, path, message, context or None)


def json_number(value):
    value = Decimal(str(value))
    if not value.is_finite():
        _fail("", "finite number required")
    if value == value.to_integral_value():
        return int(value)
    result = float(value)
    if not math.isfinite(result) or Decimal(str(result)) != value:
        _fail("", "value cannot be represented losslessly as a JSON number")
    return result


def duration_seconds(value, path=""):
    match = _DURATION.fullmatch(value)
    if (
        not match
        or not any(match.groups())
        or ("T" in value and not any(match.groups()[1:]))
    ):
        _fail(path, "expected a nonnegative fixed ISO 8601 duration")
    with localcontext() as ctx:
        ctx.prec = max(28, len(value) + 12)
        result = sum(
            (
                Decimal(v or 0) * scale
                for v, scale in zip(match.groups(), (86400, 3600, 60, 1))
            ),
            Decimal(0),
        )
        if result != result.quantize(Decimal("0.000000001")):
            _fail(path, "duration precision finer than nanoseconds is unsupported")
        return result


def duration_text(seconds):
    text = format(Decimal(seconds), "f")
    return "PT" + (text.rstrip("0").rstrip(".") if "." in text else text) + "S"


def time_seconds(value, path=""):
    match = _TIME.fullmatch(value)
    if not match:
        _fail(path, "expected an explicit-timezone ISO 8601 timestamp")
    if len((match[2] or "").rstrip("0")) > 10:
        _fail(path, "timestamp precision finer than nanoseconds is unsupported")
    try:
        dt = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        delta = dt.astimezone(timezone.utc) - _EPOCH
        return Decimal(delta.days * 86400 + delta.seconds) + Decimal(match[2] or 0)
    except (ValueError, OverflowError) as exc:
        raise QueryError(
            "invalid_query", path, "invalid calendar date or offset"
        ) from exc


def time_text(seconds):
    seconds = Decimal(seconds)
    whole = math.floor(seconds)
    try:
        dt = _EPOCH + timedelta(seconds=whole)
    except OverflowError as exc:
        raise QueryError(
            "invalid_query", "", "timestamp outside supported calendar"
        ) from exc
    fraction = seconds - whole
    return (
        f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        + (format(fraction, "f")[1:].rstrip("0") if fraction else "")
        + "Z"
    )


def unit_name(unit):
    return _UNIT_ALIASES.get(unit, unit)


def unit_info(unit):
    """Finite shared equivalences, not a decoder or an arbitrary unit formula."""
    unit = unit_name(unit)
    known = {
        "Pa": ("pressure", "1", "0"),
        "hPa": ("pressure", "100", "0"),
        "m": ("length", "1", "0"),
        "cm": ("length", ".01", "0"),
        "mm": ("length", ".001", "0"),
        "km": ("length", "1000", "0"),
        "K": ("temperature", "1", "0"),
        "degC": ("temperature", "1", "273.15"),
        "%": ("ratio", ".01", "0"),
        "1": ("ratio", "1", "0"),
    }
    dimension, factor, offset = known.get(unit, (unit, "1", "0"))
    return dimension, Decimal(factor), Decimal(offset)


def convert_quantity(value, source, target, path=""):
    sd, sf, so = unit_info(source)
    td, tf, to = unit_info(target)
    if sd != td:
        _fail(path, "incompatible units", "invalid_config")
    return json_number((Decimal(str(value)) * sf + so - to) / tf)


def _numeric(value, kind, path=""):
    if kind == "time":
        return time_seconds(value, path)
    if kind == "duration":
        return duration_seconds(value, path)
    if type(value) not in (int, float):
        _fail(path, "numeric range value required")
    return Decimal(str(value))


def _encode(value, kind):
    return (
        time_text(value)
        if kind == "time"
        else duration_text(value)
        if kind == "duration"
        else json_number(value)
    )


def selector_kind(field):
    return (
        "time"
        if field in {"time", "valid_time"}
        else "duration"
        if field == "step"
        else "number"
    )


def _normal_selector(value, kind, path, factor=Decimal(1)):
    def scalar(item):
        if kind in {"time", "duration", "number"}:
            return _encode(_numeric(item, kind, path) * factor, kind)
        return item

    if isinstance(value, dict) and "range" in value:
        r = value["range"]
        start, end = scalar(r["start"]), scalar(r["end"])
        if _numeric(start, kind, path) > _numeric(end, kind, path):
            _fail(path + "/range/end", "range end precedes start")
        result = {"start": start, "end": end}
        if "every" in r:
            stride_kind = "duration" if kind == "time" else kind
            stride = _numeric(r["every"], stride_kind, path + "/range/every") * factor
            if stride <= 0:
                _fail(path + "/range/every", "range interval must be positive")
            result["every"] = _encode(stride, stride_kind)
        return start if start == end else {"range": result}
    values = value if isinstance(value, list) else [value]
    unique = {canonical(scalar(v)): scalar(v) for v in values}
    values = sorted(
        unique.values(),
        key=lambda v: (
            _numeric(v, kind)
            if kind in {"number", "time", "duration"}
            else canonical(v)
        ),
    )
    return values[0] if len(values) == 1 else values


def _vertical_factor(kind, unit, path):
    if kind in NAMED_LEVELS or kind == "model_level":
        if unit is not None:
            _fail(path, "named levels and model indices have no physical unit")
        return None, Decimal(1)
    if kind not in _VERTICAL_UNITS:
        _fail(
            path,
            "vertical type requires explicit definition",
            "invalid_config",
            reason="missing_context",
        )
    default, factors = _VERTICAL_UNITS[kind]
    unit = default if unit is None else unit_name(unit)
    if unit not in factors:
        _fail(path, "unit is not valid for this vertical type")
    return default, factors[unit]


def _endpoint(endpoint, outer_type, outer_unit, path, allow_unbound):
    item = {"value": endpoint} if type(endpoint) in (int, float) else dict(endpoint)
    kind = item.get("type", outer_type)
    if outer_type is not None and kind != outer_type:
        _fail(path + "/type", "endpoint conflicts with the shared vertical type")
    if (
        outer_unit is not None
        and "unit" in item
        and unit_name(item["unit"]) != unit_name(outer_unit)
    ):
        _fail(path + "/unit", "endpoint conflicts with the shared unit")
    if kind is None:
        if allow_unbound:
            return item
        _fail(
            path,
            "endpoint needs a vertical type",
            "invalid_config",
            reason="missing_context",
        )
    if kind in NAMED_LEVELS:
        if "value" in item or "unit" in item or outer_unit is not None:
            _fail(path, "named boundary must not have a value or unit")
        return {"type": kind}
    if "value" not in item:
        _fail(path + "/value", "numeric boundary requires a value")
    unit, factor = _vertical_factor(kind, item.get("unit", outer_unit), path + "/unit")
    value = json_number(Decimal(str(item["value"])) * factor)
    if kind == "model_level" and (type(value) is not int or value < 0):
        _fail(path + "/value", "model level must be a nonnegative integer")
    return {"type": kind, "value": value, **({"unit": unit} if unit else {})}


def _normalize(query, path="", allow_unbound=False):
    result = dict(query)
    kind, unit = result.get("level_type"), result.get("level_unit")
    if "layer" in result:
        layers = (
            result["layer"] if isinstance(result["layer"], list) else [result["layer"]]
        )
        normalized = []
        for i, layer in enumerate(layers):
            item = {
                end: _endpoint(
                    layer[end], kind, unit, f"{path}/layer/{i}/{end}", allow_unbound
                )
                for end in ("bottom", "top")
            }
            bottom, top = item["bottom"], item["top"]
            if (
                bottom.get("type")
                in {
                    "pressure",
                    "depth_below_land",
                    "height_above_ground",
                    "height_above_sea",
                }
                and bottom.get("type") == top.get("type")
                and "value" in bottom
                and "value" in top
            ):
                reverse = bottom.get("type") in {"pressure", "depth_below_land"}
                if bottom["value"] == top["value"] or (
                    bottom["value"] < top["value"]
                    if reverse
                    else bottom["value"] > top["value"]
                ):
                    _fail(
                        path + "/layer",
                        "layer boundaries have invalid physical direction or zero width",
                    )
            normalized.append(item)
        normalized = list({canonical(v): v for v in normalized}.values())
        normalized.sort(key=canonical)
        result["layer"] = normalized[0] if len(normalized) == 1 else normalized
        result.pop("level_type", None)
        result.pop("level_unit", None)
    elif kind is not None:
        canonical_unit, factor = _vertical_factor(kind, unit, path + "/level_unit")
        if kind in NAMED_LEVELS and "level" in result:
            _fail(path + "/level", "named level must not have a value")
        if "level" in result:
            result["level"] = _normal_selector(
                result["level"], "number", path + "/level", factor
            )
            if kind == "model_level":
                values = result["level"]
                check = (
                    list(values["range"].values())
                    if isinstance(values, dict)
                    else values
                    if isinstance(values, list)
                    else [values]
                )
                if any(type(v) is not int or v < 0 for v in check):
                    _fail(path + "/level", "model levels must be nonnegative integers")
        if canonical_unit:
            result["level_unit"] = canonical_unit
        else:
            result.pop("level_unit", None)
    elif "level" in result:
        result["level"] = _normal_selector(result["level"], "number", path + "/level")
        if unit is not None:
            result["level_unit"] = unit_name(unit)
    for field in ("time", "step", "valid_time", "member", "parameter", "member_role"):
        if field in result:
            category = (
                selector_kind(field)
                if field in {"time", "step", "valid_time", "member"}
                else "literal"
            )
            result[field] = _normal_selector(
                result[field], category, path + "/" + field
            )
    if "native" in result:
        result["native"] = {
            "grib": {
                k: _normal_selector(
                    v,
                    "number" if isinstance(v, dict) else "literal",
                    path + "/native/grib/" + k,
                )
                for k, v in result["native"]["grib"].items()
            }
        }
    times = [result.get(k) for k in ("time", "step", "valid_time")]
    if all(isinstance(v, str) for v in times):
        if time_seconds(times[0]) + duration_seconds(times[1]) != time_seconds(
            times[2]
        ):
            _fail(path + "/valid_time", "time + step differs from valid_time")
    supplied = {k: result[k] for k in ("time", "step", "valid_time") if k in result}
    if len(supplied) == 2 and all(isinstance(v, str) for v in supplied.values()):
        numbers = {k: _numeric(v, selector_kind(k)) for k, v in supplied.items()}
        missing = next(k for k in ("time", "step", "valid_time") if k not in supplied)
        value = (
            numbers["time"] + numbers["step"]
            if missing == "valid_time"
            else numbers["valid_time"] - numbers["step"]
            if missing == "time"
            else numbers["valid_time"] - numbers["time"]
        )
        if missing == "step" and value < 0:
            _fail(path + "/step", "valid_time precedes time")
        result[missing] = _encode(value, selector_kind(missing))
    return result


def normalize_query(query):
    from .model import Query

    query = Query(query).to_dict()
    return Query(_normalize(query))


def _count(selector, kind):
    if isinstance(selector, list):
        return len(selector)
    if isinstance(selector, dict) and "range" in selector:
        r = selector["range"]
        if "every" not in r:
            return None
        step_kind = "duration" if kind == "time" else kind
        return (
            int(
                (_numeric(r["end"], kind) - _numeric(r["start"], kind))
                // _numeric(r["every"], step_kind)
            )
            + 1
        )
    return 1


def _values(selector, kind):
    if isinstance(selector, list):
        yield from selector
    elif (
        isinstance(selector, dict)
        and "range" in selector
        and "every" in selector["range"]
    ):
        r = selector["range"]
        first = _numeric(r["start"], kind)
        stride = _numeric(r["every"], "duration" if kind == "time" else kind)
        for index in range(_count(selector, kind)):
            yield _encode(first + index * stride, kind)
    else:
        yield selector


def _contains(selector, value, kind):
    if isinstance(selector, list):
        return any(canonical(v) == canonical(value) for v in selector)
    if isinstance(selector, dict) and "range" in selector:
        r = selector["range"]
        first, last, actual = (_numeric(v, kind) for v in (r["start"], r["end"], value))
        return first <= actual <= last and (
            "every" not in r
            or (actual - first)
            % _numeric(r["every"], "duration" if kind == "time" else kind)
            == 0
        )
    return canonical(selector) == canonical(value)


def _range_intersection(a, b, kind):
    left, right = a["range"], b["range"]
    lo = max(_numeric(left["start"], kind), _numeric(right["start"], kind))
    hi = min(_numeric(left["end"], kind), _numeric(right["end"], kind))
    if lo > hi:
        return None
    discrete = [r for r in (left, right) if "every" in r]
    if not discrete:
        return (
            _encode(lo, kind)
            if lo == hi
            else {"range": {"start": _encode(lo, kind), "end": _encode(hi, kind)}}
        )
    step_kind = "duration" if kind == "time" else kind
    fractions = [
        Fraction(_numeric(r[key], step_kind if key == "every" else kind))
        for r in discrete
        for key in ("start", "every")
    ]
    scale = math.lcm(*(f.denominator for f in fractions))
    lattice = [
        (int(fractions[i] * scale), int(fractions[i + 1] * scale))
        for i in range(0, len(fractions), 2)
    ]
    origin, stride = lattice[0]
    if len(lattice) == 2:
        other, step = lattice[1]
        gcd = math.gcd(stride, step)
        if (other - origin) % gcd:
            return None
        modulus = step // gcd
        k = (
            ((other - origin) // gcd * pow(stride // gcd, -1, modulus)) % modulus
            if modulus > 1
            else 0
        )
        origin += stride * k
        stride = math.lcm(stride, step)
    first = Decimal(origin) / scale
    delta = Decimal(stride) / scale
    first += max(0, math.ceil((lo - first) / delta)) * delta
    if first > hi:
        return None
    last = first + int((hi - first) // delta) * delta
    if first == last:
        return _encode(first, kind)
    return {
        "range": {
            "start": _encode(first, kind),
            "end": _encode(last, kind),
            "every": _encode(delta, step_kind),
        }
    }


def _intersection(a, b, kind):
    if isinstance(a, dict) and "range" in a and isinstance(b, dict) and "range" in b:
        return _range_intersection(a, b, kind)
    if isinstance(a, dict) and "range" in a:
        a, b = b, a
    candidates = a if isinstance(a, list) else [a]
    values = [v for v in candidates if _contains(b, v, kind)]
    return None if not values else values[0] if len(values) == 1 else values


def _intersect(a, b, *, native=False):
    shared_type = a.get("level_type", b.get("level_type")) if not native else None
    if shared_type is not None:
        try:
            a = _normalize({"level_type": shared_type, **a}, allow_unbound=True)
            b = _normalize({"level_type": shared_type, **b}, allow_unbound=True)
        except QueryError:
            return None
    result = dict(a)
    # Layer normalization removes the shared type. A single-level restriction
    # cannot be represented as a layer, so such a conjunction is empty.
    if ("level" in a and "layer" in b) or ("layer" in a and "level" in b):
        return None
    for field, value in b.items():
        if field == "native":
            merged = _intersect(
                a.get("native", {}).get("grib", {}), value["grib"], native=True
            )
            if merged is None:
                return None
            result[field] = {"grib": merged}
        elif field in result:
            selected = _intersection(result[field], value, selector_kind(field))
            if selected is None:
                return None
            result[field] = selected
        else:
            result[field] = value
    if not native and "layer" in result and "level_type" in result:
        try:
            result = _normalize(result, allow_unbound=True)
        except QueryError:
            return None
    return result


def intersect_scopes(left, right):
    """Conjoin configuration bounds without enumerating a product domain.

    Scopes constrain later selections; they are not executable requests. Time
    tuple legality and max_expansion still apply when effective queries are
    prepared/expanded. A partial vertical bound can gain its type from the other
    scope, using the same intersection semantics as Query selection.
    """
    from .model import Query

    a = _normalize(Query(left).to_dict(), allow_unbound=True)
    b = _normalize(Query(right).to_dict(), allow_unbound=True)
    result = _intersect(a, b)
    return None if result is None else Query(result)


def intersect_queries(left, right, *, max_expansion=DEFAULT_MAX_EXPANSION):
    """Return a conjunction, or None for two legal disjoint selections."""
    a, b = normalize_query(left).to_dict(), normalize_query(right).to_dict()
    for q in (a, b):
        if all(k in q for k in ("time", "step", "valid_time")):
            for _ in _time_rows(q, None):
                pass
    result = _intersect(a, b)
    if result is None:
        return None
    # Individually legal time conditions may become empty after conjunction.
    try:
        normalized = _normalize(result)
        rows = expand_queries([normalized], max_expansion=max_expansion)
    except QueryError as exc:
        if exc.code == "invalid_query":
            return None
        raise
    return normalize_query(result) if rows else None


def _subset(value, bound, kind):
    if not (isinstance(value, dict) and "range" in value):
        return all(
            _contains(bound, v, kind)
            for v in (value if isinstance(value, list) else [value])
        )
    count = _count(value, kind)
    if isinstance(bound, dict) and "range" in bound:
        a, b = value["range"], bound["range"]
        sk = "duration" if kind == "time" else kind
        first = _numeric(a["start"], kind)
        stride = _numeric(a["every"], sk)
        last = first + (count - 1) * stride
        if first < _numeric(b["start"], kind) or last > _numeric(b["end"], kind):
            return False
        return "every" not in b or (
            _contains(bound, a["start"], kind)
            and (count == 1 or stride % _numeric(b["every"], sk) == 0)
        )
    if count > (len(bound) if isinstance(bound, list) else 1):
        return False
    return all(_contains(bound, v, kind) for v in _values(value, kind))


def _affine_selector(selector, kind, target_kind, factor, offset):
    def number(value):
        return factor * _numeric(value, kind) + offset

    def legal(value):
        return (
            value >= 0
            if target_kind == "duration"
            else Decimal("-62135596800") <= value <= Decimal("253402300799.999999999")
            if target_kind == "time"
            else True
        )

    if isinstance(selector, dict) and "range" in selector:
        r = selector["range"]
        start = _numeric(r["start"], kind)
        end = _numeric(r["end"], kind)
        stride = None
        if "every" in r:
            stride = _numeric(r["every"], "duration" if kind == "time" else kind)
            end = start + (_count(selector, kind) - 1) * stride
        lo, hi = sorted((factor * start + offset, factor * end + offset))
        if target_kind == "duration":
            lower, upper = Decimal(0), hi
        elif target_kind == "time":
            lower, upper = Decimal("-62135596800"), Decimal("253402300799.999999999")
        else:
            lower, upper = lo, hi
        clipped = max(lo, lower)
        if stride is not None:
            stride = abs(factor) * stride
            clipped = lo + math.ceil((clipped - lo) / stride) * stride
        hi = min(hi, upper)
        if clipped > hi:
            return None
        result = {
            "start": _encode(clipped, target_kind),
            "end": _encode(hi, target_kind),
        }
        if stride is not None:
            result["every"] = _encode(
                stride, "duration" if target_kind == "time" else target_kind
            )
        return {"range": result}
    values = (
        [number(v) for v in selector]
        if isinstance(selector, list)
        else [number(selector)]
    )
    values = [_encode(v, target_kind) for v in values if legal(v)]
    return None if not values else values[0] if len(values) == 1 else values


def constrain_query(query, constraints):
    """Apply fixed/scope constraints without silently dropping explicit choices."""
    from .model import Query

    # Product-supplied vertical type may resolve a partial Query.
    raw, fixed = plain(query), plain(constraints)
    if "level_type" not in raw and "level_type" in fixed:
        raw = {"level_type": fixed["level_type"], **raw}
    q, c = _normalize(Query(raw).to_dict()), _normalize(Query(fixed).to_dict())

    def compatible(value, bound, kind):
        if (
            isinstance(value, dict)
            and "range" in value
            and "every" not in value["range"]
        ):
            return _intersection(value, bound, kind) is not None
        return _subset(value, bound, kind)

    for key, value in q.items():
        if key not in c:
            continue
        if key == "native":
            for name, selector in value["grib"].items():
                bound = c[key]["grib"].get(name)
                if bound is not None and not compatible(selector, bound, "number"):
                    _fail(
                        "/native/grib/" + name,
                        "explicit native choice conflicts with bound constraints",
                        "constraint_conflict",
                        reason="fixed_condition_conflict",
                    )
        elif not compatible(value, c[key], selector_kind(key)):
            _fail(
                "/" + key,
                "explicit choice conflicts with bound constraints",
                "constraint_conflict",
                reason="fixed_condition_conflict",
            )
    result = _intersect(q, c)
    if result is None:
        _fail(
            "",
            "selection conflicts with bound constraints",
            "constraint_conflict",
            reason="fixed_condition_conflict",
        )
    return Query(result)


def _time_rows(query, limit):
    keys = [k for k in ("time", "step", "valid_time") if k in query]
    finite = [k for k in keys if _count(query[k], selector_kind(k)) is not None]
    if len(finite) < 2:

        def expand(index, row):
            if index == len(keys):
                yield row
            else:
                key = keys[index]
                for v in _values(query[key], selector_kind(key)):
                    yield from expand(index + 1, {**row, key: v})

        yield from expand(0, {})
        return
    pair = min(
        itertools.combinations(finite, 2),
        key=lambda pair: math.prod(_count(query[k], selector_kind(k)) for k in pair),
    )
    seen = {k: set() for k in finite}
    emitted = 0
    coefficients = {"time": 1, "step": 1, "valid_time": -1}
    outer, inner = pair
    third = next(k for k in coefficients if k not in pair)
    for av in _values(query[outer], selector_kind(outer)):
        inner_domain = query[inner]
        outer_number = _numeric(av, selector_kind(outer))
        if third in query:
            transformed = _affine_selector(
                query[third],
                selector_kind(third),
                selector_kind(inner),
                -coefficients[third] // coefficients[inner],
                -coefficients[outer] * outer_number / coefficients[inner],
            )
            inner_domain = (
                None
                if transformed is None
                else _intersection(inner_domain, transformed, selector_kind(inner))
            )
        elif third == "step":
            # Both timestamps are explicit; negative forecast durations are not
            # legal, and need not be enumerated to prove that fact.
            if inner == "time":
                lower = (
                    query[inner]["range"]["start"]
                    if isinstance(query[inner], dict)
                    else query[inner][0]
                    if isinstance(query[inner], list)
                    else query[inner]
                )
                if time_seconds(lower) > outer_number:
                    inner_domain = None
                else:
                    inner_domain = _intersection(
                        inner_domain, {"range": {"start": lower, "end": av}}, "time"
                    )
            else:
                upper = (
                    query[inner]["range"]["end"]
                    if isinstance(query[inner], dict)
                    else query[inner][-1]
                    if isinstance(query[inner], list)
                    else query[inner]
                )
                if time_seconds(upper) < outer_number:
                    inner_domain = None
                else:
                    inner_domain = _intersection(
                        inner_domain, {"range": {"start": av, "end": upper}}, "time"
                    )
        if inner_domain is None:
            _fail(
                "/" + outer,
                "explicit time choice participates in no legal time combination",
            )
        for bv in _values(inner_domain, selector_kind(inner)):
            row = dict(zip(pair, (av, bv)))
            numbers = {k: _numeric(v, selector_kind(k)) for k, v in row.items()}
            value = (
                -(
                    coefficients[outer] * numbers[outer]
                    + coefficients[inner] * numbers[inner]
                )
                / coefficients[third]
            )
            if third == "step" and value < 0:
                continue
            row[third] = _encode(value, selector_kind(third))
            if any(not _contains(query[k], row[k], selector_kind(k)) for k in keys):
                continue
            for k in finite:
                seen[k].add(canonical(row[k]))
            emitted += 1
            if limit is not None and emitted > limit:
                _limit(limit)
            yield row
    for key in finite:
        if len(seen[key]) != _count(query[key], selector_kind(key)):
            _fail(
                "/" + key,
                "explicit time choice participates in no legal time combination",
            )


def _limit(limit):
    _fail(
        "/queries",
        "effective discrete expansion exceeds the configured limit",
        "expansion_limit_exceeded",
        limit=limit,
        count_lower_bound=limit + 1,
    )


def _validate_limit(limit):
    if type(limit) is not int or limit <= 0:
        _fail(
            "/execution/max_expansion",
            "max_expansion must be a positive integer",
            "invalid_config",
        )


def _expand_one(query, limit, member_roles, cemc):
    fields = [
        k
        for k in query
        if k not in {"time", "step", "valid_time", "native", "member_role"}
    ]
    # Native predicates remain one paired object until their individual selectors
    # are expanded in the same bounded recursion.
    if "member" not in query and "member_role" in query:
        fields.append("member_role")
    if "native" in query:
        for key in query["native"]["grib"]:
            fields.append("native.grib." + key)

    def get(field):
        return (
            query["native"]["grib"][field[12:]]
            if field.startswith("native.grib.")
            else query[field]
        )

    def build(index, row):
        if index == len(fields):
            yield row
            return
        key = fields[index]
        value = get(key)
        for item in _values(value, selector_kind(key)):
            if key.startswith("native.grib."):
                next_row = {
                    **row,
                    "native": {
                        "grib": {
                            **row.get("native", {}).get("grib", {}),
                            key[12:]: item,
                        }
                    },
                }
            else:
                next_row = {**row, key: item}
            yield from build(index + 1, next_row)

    for time_row in _time_rows(query, limit):
        for row in build(0, time_row):
            if "member_role" in query and "member_role" not in row:
                role = None
                if "member" in row and type(row["member"]) is int:
                    role = (member_roles or {}).get(str(row["member"]))
                    if role is None and cemc:
                        role = "control" if row["member"] == 0 else "perturbation"
                if role is not None and not _contains(
                    query["member_role"], role, "literal"
                ):
                    _fail(
                        "/member_role",
                        "member number conflicts with the effective role mapping",
                        "constraint_conflict",
                        reason="fixed_condition_conflict",
                    )
                row["member_role"] = role if role is not None else query["member_role"]
            yield row


def expand_queries(
    queries, *, max_expansion=DEFAULT_MAX_EXPANSION, member_roles=None, cemc=False
):
    """Expand effective discrete combinations, preserving Query/time/layer pairings."""
    _validate_limit(max_expansion)
    if not isinstance(queries, (list, tuple)):
        _fail("/queries", "queries must be a sequence")
    output = {}
    for qi, query in enumerate(queries):
        normalized = normalize_query(query).to_dict()
        try:
            for row in _expand_one(normalized, max_expansion, member_roles, cemc):
                output.setdefault(canonical(row), row)
                if len(output) > max_expansion:
                    _limit(max_expansion)
        except QueryError as exc:
            if exc.code == "expansion_limit_exceeded":
                raise QueryError(
                    exc.code,
                    exc.path,
                    exc.message,
                    {
                        **dict(exc.context),
                        "origins": [{"step_index": 0, "query_index": qi}],
                    },
                ) from exc
            raise
    from .model import Query

    return tuple(Query(output[k]) for k in sorted(output))


def query_identity(query, *, context=None):
    """Identity includes supplied semantic scope/versions, never resolves aliases implicitly."""
    normalized = [q.to_dict() for q in expand_queries([query])]
    return (
        "sha256:"
        + hashlib.sha256(
            canonical(
                {
                    "queries": normalized,
                    "context": {} if context is None else plain(context),
                }
            ).encode()
        ).hexdigest()
    )


def combine_queries(steps, *, scope=None, max_expansion=DEFAULT_MAX_EXPANSION):
    """Pure AND-of-OR combination, with branch origins, checked after each step.

    Backend resource-dependent parameter constraints should be applied to every
    input with prepare_queries first, even after an earlier step became empty.
    """
    _validate_limit(max_expansion)
    branches = [({}, [])]
    for si, inputs in enumerate(steps):
        if not isinstance(inputs, (list, tuple)) or not inputs:
            _fail("/queries", "each selection step requires a nonempty Query list")
        normalized = []
        for qi, q in enumerate(inputs):
            q = (
                constrain_query(q, scope).to_dict()
                if scope is not None
                else normalize_query(q).to_dict()
            )
            if all(k in q for k in ("time", "step", "valid_time")):
                for _ in _time_rows(q, max_expansion if si == 0 else None):
                    pass
            normalized.append((q, {"step_index": si, "query_index": qi}))
        merged = {}
        for previous, origins in branches:
            for query, origin in normalized:
                conjunction = intersect_queries(
                    previous, query, max_expansion=max_expansion
                )
                if conjunction is None:
                    continue
                data = conjunction.to_dict()
                key = canonical(data)
                entry = merged.setdefault(key, [data, []])
                for item in origins + [origin]:
                    if item not in entry[1]:
                        entry[1].append(item)
        branches = [merged[k] for k in sorted(merged)]
        expand_queries([q for q, _ in branches], max_expansion=max_expansion)
    return tuple(
        {
            "index": i,
            "query": q,
            "origins": sorted(
                origins, key=lambda o: (o["step_index"], o["query_index"])
            ),
        }
        for i, (q, origins) in enumerate(branches)
    )
