from app_core import category_data, json_data


def flat(target, scaling_x=1, scaling_extra="", context=None):
    return 1


def per_x_money(target, scaling_x=100, scaling_extra="", context=None):
    money = target.get("money", 0) or 0
    divisor = float(scaling_x) if scaling_x else 100
    return int(money / divisor)


def per_x_resource(target, scaling_x=1, scaling_extra="", context=None):
    resource = target.get("resource_storage", {}).get(scaling_extra, 0) or 0
    divisor = float(scaling_x) if scaling_x else 1
    return int(resource / divisor)


def per_x_shallow_and_deep_water_tiles(target, scaling_x=1, scaling_extra="", context=None):
    territory = target.get("territory_types", {}) or {}
    shallow = territory.get("shallow_water", 0)
    deep = territory.get("deep_water", 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int((shallow + deep) / divisor)


def per_x_magic_nodes(target, scaling_x=1, scaling_extra="", context=None):
    loose = (target.get("nodes") or {}).get("magic", 0)
    territory = (target.get("_calc_cache") or {}).get("territory_node_counts", {}).get("magic", 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int((loose + territory) / divisor)


def per_x_resource_nodes(target, scaling_x=1, scaling_extra="", context=None):
    """Total territory nodes (active + inactive) for any resource type via scaling_extra."""
    if not scaling_extra:
        return 0
    loose = (target.get("nodes") or {}).get(scaling_extra, 0)
    territory = (target.get("_calc_cache") or {}).get("territory_node_counts", {}).get(scaling_extra, 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int((loose + territory) / divisor)


def per_x_bloodthirsty_pops(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(cache.get("bloodthirsty_pop_count", 0) / divisor)


def per_x_primary_culture_pops_world(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(cache.get("primary_culture_pop_count", 0) / divisor)


def per_x_primary_religion_pops_world(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(cache.get("primary_religion_pop_count", 0) / divisor)


def per_x_pops(target, scaling_x=1, scaling_extra="", context=None):
    pops = target.get("pop_count", 0) or 0
    divisor = float(scaling_x) if scaling_x else 1
    return int(pops / divisor)


def per_x_naval_units(target, scaling_x=1, scaling_extra="", context=None):
    units = target.get("naval_units", []) or []
    divisor = float(scaling_x) if scaling_x else 1
    return int(len(units) / divisor)


def per_x_land_units(target, scaling_x=1, scaling_extra="", context=None):
    units = target.get("land_units", []) or []
    divisor = float(scaling_x) if scaling_x else 1
    return int(len(units) / divisor)


def per_x_turns_library(target, scaling_x=1, scaling_extra="", context=None):
    modifiers = target.get("modifiers", []) or []
    library_turns = 0
    for m in modifiers:
        if isinstance(m, dict) and m.get("source") == "Library District":
            try:
                library_turns = int(m.get("value", 0))
            except (TypeError, ValueError):
                pass
            break
    divisor = float(scaling_x) if scaling_x else 1
    return int(library_turns / divisor)


def per_x_terrain_tiles(target, scaling_x=1, scaling_extra="", context=None):
    territory = target.get("territory_types", {}) or {}
    terrain_count = territory.get(scaling_extra, 0) if scaling_extra else 0
    divisor = float(scaling_x) if scaling_x else 1
    return int(terrain_count / divisor)


def per_x_district_category(target, scaling_x=1, scaling_extra="", context=None):
    from app_core import mongo as _mongo
    districts = target.get("districts", []) or []
    if not districts or not scaling_extra:
        return 0
    def_keys = {d.get("def_key") or d.get("type", "") for d in districts if isinstance(d, dict)}
    def_keys.discard("")
    if not def_keys:
        return 0
    key_to_cat = {
        r["key"]: r.get("category", "")
        for r in _mongo.db.district_defs.find({"key": {"$in": list(def_keys)}}, {"key": 1, "category": 1, "_id": 0})
    }
    count = sum(
        1 for d in districts
        if isinstance(d, dict) and key_to_cat.get(d.get("def_key") or d.get("type", "")) == scaling_extra
    )
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_administration(target, scaling_x=1, scaling_extra="", context=None):
    administration = float(target.get("administration", 0) or 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(administration / divisor)


def per_x_resource_produced(target, scaling_x=1, scaling_extra="", context=None):
    production = target.get("resource_production", {}) or {}
    amount = float(production.get(scaling_extra, 0) or 0) if scaling_extra else 0
    divisor = float(scaling_x) if scaling_x else 1
    return int(amount / divisor)


def per_x_cultures(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    count = cache.get("culture_count", 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_religions(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    count = cache.get("religion_count", 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_cultures_and_religions(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    count = cache.get("culture_count", 0) + cache.get("religion_count", 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_minority_cultures(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    count = max(0, cache.get("culture_count", 0) - 1)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_minority_religions(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    count = max(0, cache.get("religion_count", 0) - 1)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_minority_cultures_and_religions(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    count = max(0, cache.get("culture_count", 0) + cache.get("religion_count", 0) - 2)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_slaves(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(cache.get("slave_count", 0) / divisor)


# Registry — each key must match scaling_types.json (plus legacy aliases).
SCALING_METHODS = {
    "flat": flat,
    "per_x_money": per_x_money,
    "per_x_resource": per_x_resource,
    "per_x_shallow_and_deep_water_tiles": per_x_shallow_and_deep_water_tiles,
    "per_x_magic_nodes": per_x_magic_nodes,
    "per_x_bloodthirsty_pops": per_x_bloodthirsty_pops,
    "per_x_primary_culture_pops_world": per_x_primary_culture_pops_world,
    "per_x_primary_religion_pops_world": per_x_primary_religion_pops_world,
    "per_x_pops": per_x_pops,
    "per_x_naval_units": per_x_naval_units,
    "per_x_land_units": per_x_land_units,
    "per_x_turns_library": per_x_turns_library,
    "per_x_terrain_tiles": per_x_terrain_tiles,
    "per_x_district_category": per_x_district_category,
    "per_x_administration": per_x_administration,
    "per_x_resource_produced": per_x_resource_produced,
    "per_x_resource_nodes": per_x_resource_nodes,
    "per_x_cultures": per_x_cultures,
    "per_x_religions": per_x_religions,
    "per_x_cultures_and_religions": per_x_cultures_and_religions,
    "per_x_minority_cultures": per_x_minority_cultures,
    "per_x_minority_religions": per_x_minority_religions,
    "per_x_minority_cultures_and_religions": per_x_minority_cultures_and_religions,
    "per_x_slaves": per_x_slaves,
}


def get_scaling_multiplier(scaling_key, target, scaling_x=1, scaling_extra="", context=None):
    if not scaling_key or scaling_key == "flat":
        return 1
    method = SCALING_METHODS.get(scaling_key)
    if method is None:
        return 1
    try:
        return method(target, scaling_x=scaling_x, scaling_extra=scaling_extra, context=context)
    except Exception:
        return 1
