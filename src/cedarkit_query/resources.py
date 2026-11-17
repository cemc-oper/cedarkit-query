"""Pure validation for definitions, mappings and catalog resource combinations."""

from ._json import canonical, plain, pointer
from .errors import QueryError
from .normalize import (
    _intersection,
    _normal_selector,
    _normalize,
    constrain_query,
    intersect_scopes,
)


def _error(path, message):
    raise QueryError("invalid_config", path, message)


def _index(rows, key, path):
    result = {}
    for index, row in enumerate(rows):
        name = row[key]
        if name in result:
            _error(f"{path}/{index}/{key}", "duplicate identifier")
        result[name] = row
    return result


def _fixed(fixed, path, *, level_type=None):
    query = {key: value for key, value in fixed.items() if key != "statistics"}
    if level_type is not None:
        query.setdefault("level_type", level_type)
    result = _normalize(query, path, allow_unbound=True)
    if "statistics" in fixed:
        result["statistics"] = fixed["statistics"]
    return result


def _effective(data):
    definitions = _index(data["parameters"], "parameter_id", "/parameters")
    effective = {}
    visiting = set()

    def resolve(pid, path):
        if pid in visiting:
            _error(path, "parameter dependency cycle")
        if pid not in definitions:
            _error(path, f"unknown parameter {pid}")
        if pid in effective:
            return effective[pid]
        visiting.add(pid)
        row = definitions[pid]
        path = f"/parameters/{pid}"
        parent = resolve(row["parent"], path + "/parent") if "parent" in row else {}
        inherited = parent.get("fixed_conditions", {})
        fixed = _fixed(
            row.get("fixed_conditions", {}),
            path + "/fixed_conditions",
            level_type=inherited.get("level_type"),
        )
        if "statistics" in fixed and "statistics" in inherited:
            parent_stats = inherited["statistics"]
            child_stats = fixed["statistics"]
            for key in parent_stats.keys() & child_stats.keys():
                if parent_stats[key] != child_stats[key]:
                    _error(
                        path + "/fixed_conditions/statistics/" + key,
                        "conflicting inherited statistic",
                    )
            fixed["statistics"] = {**parent_stats, **child_stats}
            inherited = {
                key: value for key, value in inherited.items() if key != "statistics"
            }
        for key in fixed.keys() & inherited.keys():
            if fixed[key] != inherited[key]:
                _error(
                    path + "/fixed_conditions/" + key, "conflicting inherited condition"
                )
        fixed = _fixed({**inherited, **fixed}, path + "/fixed_conditions")
        if "level" in fixed and "layer" in fixed:
            _error(path + "/fixed_conditions", "level and layer are mutually exclusive")
        statistics = fixed.get("statistics", {})
        if "of" in statistics:
            resolve(
                statistics["of"]["parameter_id"],
                path + "/fixed_conditions/statistics/of",
            )
        result = {
            **{
                key: parent[key]
                for key in ("unit", "description", "description_cn")
                if key in parent
            },
            **row,
            "fixed_conditions": fixed,
        }
        if "unit" not in result:
            _error(path + "/unit", "effective unit is required")
        effective[pid] = result
        visiting.remove(pid)
        return result

    for pid, row in definitions.items():
        aliases = row.get("aliases", ())
        if row["name"] in aliases or any(
            name in definitions and name != pid for name in (row["name"], *aliases)
        ):
            _error(
                f"/parameters/{pid}/aliases",
                "name or alias conflicts with a parameter identity",
            )
        resolve(pid, "/parameters")
    return effective


def validate_resource(data, kind):
    try:
        if kind == "parameters":
            _effective(data)
            return
        _index(data["mappings"], "id", "/mappings")
        tables = _index(data["tables"], "id", "/tables")
        for index, row in enumerate(data["mappings"]):
            path = f"/mappings/{index}"
            if row["backend"] != data["backend"]:
                _error(path + "/backend", "mapping and resource backends differ")
            if {"parameter", "native"} & row["when"].keys():
                _error(path + "/when", "when accepts business conditions only")
            _normalize(row["when"], path + "/when", allow_unbound=True)
            for key, value in row["native"]["match"].items():
                _normal_selector(
                    value,
                    "number" if isinstance(value, dict) else "literal",
                    f"{path}/native/match/{key}",
                )
            for duty, rule in row["native"].get("translate", {}).items():
                table = rule.get("config", {}).get("table")
                if table is not None and table not in tables:
                    _error(
                        f"{path}/native/translate/{duty}/config/table",
                        "missing shared table",
                    )
    except QueryError as exc:
        if exc.code == "invalid_config":
            raise
        raise QueryError(
            "invalid_config", exc.path, exc.message, dict(exc.context)
        ) from exc


def _mapping_conditions(row, definition, path):
    fixed = {
        key: value
        for key, value in definition["fixed_conditions"].items()
        if key != "statistics"
    }
    try:
        return constrain_query(row["when"], fixed).to_dict()
    except QueryError as exc:
        raise QueryError(
            "invalid_config", path + "/when", "mapping conflicts with parameter conditions"
        ) from exc


def _native_overlap(left, right):
    for key in left.keys() & right.keys():
        a, b = left[key], right[key]
        if isinstance(b, dict):
            a, b = b, a
        kind = "number" if isinstance(a, dict) else "literal"
        if kind == "number" and not isinstance(b, dict):
            values = b if isinstance(b, list) else [b]
            b = [value for value in values if type(value) in (int, float)]
        if _intersection(a, b, kind) is None:
            return False
    return True


def validate_standalone_grib2(parameters, mappings):
    """Validate an independent GRIB2 mapping resource and return effective definitions."""
    from .model import Mappings

    effective = effective_parameters(parameters)
    names = {}
    for pid, definition in effective.items():
        for name in (definition["name"], *definition.get("aliases", ())):
            if names.setdefault(name, pid) != pid:
                _error(
                    pointer(("parameters", pid, "aliases")),
                    f"ambiguous parameter name or alias {name!r}",
                )

    mappings = mappings if type(mappings) is Mappings else Mappings(mappings)
    resource = mappings.to_dict()
    if resource["backend"] != "grib2":
        _error("/backend", "standalone mappings require the grib2 backend")
    for index, table in enumerate(resource["tables"]):
        if table["applies_to"]:
            _error(
                f"/tables/{index}/applies_to", "standalone tables require an empty scope"
            )

    applicable = []
    for index, row in enumerate(resource["mappings"]):
        pid = row["parameter_id"]
        path = f"/mappings/{index}"
        if pid not in effective:
            _error(path + "/parameter_id", "unknown mapped parameter ID")
        if row["applies_to"]:
            _error(path + "/applies_to", "standalone mappings require an empty scope")
        bound = _mapping_conditions(row, effective[pid], path)
        native = row["native"]["match"]
        for other_id, other_native, other_bound in applicable:
            if (
                _native_overlap(native, other_native)
                and intersect_scopes(bound, other_bound) is not None
            ):
                _error(path + "/native/match", f"overlapping standalone mapping {other_id!r}")
        applicable.append((row["id"], native, bound))
    return effective


def validate_resource_set(parameters, mappings, catalog, dataset_id):
    """Validate the selected dataset's resource references and return effective definitions."""
    from .model import Catalog, Mappings

    effective = effective_parameters(parameters)
    catalog = catalog if type(catalog) is Catalog else Catalog(catalog)
    datasets = {row["id"]: row for row in catalog.to_dict()["datasets"]}
    if dataset_id not in datasets:
        _error("/datasets", "dataset is absent from catalog")
    products = {
        (row["id"], row["version"]): row
        for row in datasets[dataset_id].get("products", ())
    }
    if not products:
        _error("/datasets/products", "dataset has no product binding")

    for product in products.values():
        for key in ("scope", "provides", "expected_domain"):
            queries = [product[key]] if key == "scope" else product.get(key, ())
            for query in queries:
                names = query.get("parameter", ())
                if isinstance(names, str):
                    names = (names,)
                if any(name not in effective for name in names):
                    _error(f"/datasets/products/{key}", "unknown catalog parameter ID")

    resources = _index(
        [
            (value if type(value) is Mappings else Mappings(value)).to_dict()
            for value in mappings
        ],
        "backend",
        "/mappings",
    )
    applicable = {identity: [] for identity in products}
    for resource in resources.values():
        for row in resource["mappings"]:
            pid = row["parameter_id"]
            path = f"/mappings/{row['id']}"
            if pid not in effective:
                _error(path + "/parameter_id", "unknown mapped parameter ID")
            applies = row["applies_to"]
            if not _applies(applies, "dataset", dataset_id):
                continue
            targets = [
                (product, version)
                for product, version in products
                if _applies(applies, "product", product)
                and _applies(applies, "product_version", version)
            ]
            if not targets:
                _error(
                    path + "/applies_to",
                    "mapping refers to an unavailable product or version",
                )
            bound = _mapping_conditions(row, effective[pid], path)
            backend = resource["backend"]
            native = canonical(row["native"]["match"])
            time_scope = {"time": applies["time"]} if "time" in applies else {}
            for target in targets:
                for (
                    other_backend,
                    other_pid,
                    other_native,
                    other_bound,
                    other_time,
                ) in applicable[target]:
                    if (
                        (other_backend, other_pid, other_native)
                        == (backend, pid, native)
                        and intersect_scopes(bound, other_bound) is not None
                        and intersect_scopes(time_scope, other_time) is not None
                    ):
                        _error(
                            path + "/native/match",
                            "overlapping mapping for the same parameter and native keys",
                        )
                applicable[target].append((backend, pid, native, bound, time_scope))
    for identity, product in products.items():
        backends = {
            "cmadaas" if source["kind"] == "cmadaas" else "grib2"
            for source in product["sources"]
        }
        for key in ("provides", "expected_domain"):
            for index, query in enumerate(product.get(key, ())):
                names = query.get("parameter", ())
                if isinstance(names, str):
                    names = (names,)
                conditions = {
                    key: value for key, value in query.items() if key != "parameter"
                }
                for pid in names:
                    covered = {
                        backend
                        for backend, mapped, _, bound, time_scope in applicable[
                            identity
                        ]
                        if pid == mapped
                        and intersect_scopes(conditions, bound) is not None
                        and intersect_scopes(conditions, time_scope) is not None
                    }
                    if not backends <= covered:
                        _error(
                            f"/datasets/products/{key}/{index}",
                            "parameter has no applicable mapping",
                        )
    return plain(effective)


def _applies(applies, key, value):
    selector = applies.get(key, value)
    return value in selector if isinstance(selector, list) else value == selector


def effective_parameters(parameters):
    """Expand validated parameter definitions for binding."""
    from .model import Parameters

    parameters = parameters if type(parameters) is Parameters else Parameters(parameters)
    return _effective(parameters.to_dict())
