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
    territory = (target.get("_calc_cache") or {}).get("territory_node_counts", {}).get("magic", 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(territory / divisor)


def per_x_resource_nodes(target, scaling_x=1, scaling_extra="", context=None):
    """Total territory nodes (active + inactive) for any resource type via scaling_extra."""
    if not scaling_extra:
        return 0
    territory = (target.get("_calc_cache") or {}).get("territory_node_counts", {}).get(scaling_extra, 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(territory / divisor)


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


def _get_unit_defs_cache():
    """Lazy-load and cache unit definitions for subtype filtering."""
    if not hasattr(_get_unit_defs_cache, "_cache"):
        db = category_data["units"]["database"]
        _get_unit_defs_cache._cache = {
            u["name"]: u for u in db.find(
                {}, {"name": 1, "melee": 1, "ranged": 1, "cavalry": 1,
                     "support": 1, "traits": 1, "_id": 0}
            )
        }
    return _get_unit_defs_cache._cache


def _count_filtered(units_dict, subtype=""):
    """Count units matching a subtype filter from a {unit_key: count} dict.
    subtype: '' or 'all' = count everything; 'infantry' = melee non-cavalry;
             'cavalry', 'ranged', 'magical', 'mundane'.
    """
    if not isinstance(units_dict, dict) or not units_dict:
        return 0
    if not subtype or subtype == "all":
        return sum(v for v in units_dict.values() if isinstance(v, (int, float)))

    defs = _get_unit_defs_cache()
    total = 0
    for unit_key, count in units_dict.items():
        if not isinstance(count, (int, float)) or count <= 0:
            continue
        # Unit keys may be era-prefixed (e.g. "classical_swordsman"); try both
        udef = defs.get(unit_key) or {}
        is_melee = bool(udef.get("melee"))
        is_cavalry = bool(udef.get("cavalry"))
        is_ranged = bool(udef.get("ranged"))
        is_magical = any(
            t.lower() == "magical" for t in (udef.get("traits") or [])
        )

        if subtype == "infantry" and is_melee and not is_cavalry:
            total += count
        elif subtype == "cavalry" and is_cavalry:
            total += count
        elif subtype == "ranged" and is_ranged:
            total += count
        elif subtype == "magical" and is_magical:
            total += count
        elif subtype == "mundane" and not is_magical:
            total += count
    return total


def per_x_units(target, scaling_x=1, scaling_extra="", context=None):
    land = target.get("land_units", {}) or {}
    naval = target.get("naval_units", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    total = _count_filtered(land, scaling_extra) + _count_filtered(naval, scaling_extra)
    return int(total / divisor)


def per_x_naval_units(target, scaling_x=1, scaling_extra="", context=None):
    units = target.get("naval_units", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(_count_filtered(units, scaling_extra) / divisor)


def per_x_land_units(target, scaling_x=1, scaling_extra="", context=None):
    units = target.get("land_units", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(_count_filtered(units, scaling_extra) / divisor)


def per_x_sessions_with_library(target, scaling_x=1, scaling_extra="", context=None):
    modifiers = target.get("modifiers", []) or []
    sessions = 0
    for m in modifiers:
        if isinstance(m, dict) and m.get("field") == "Turns with Library":
            try:
                sessions = int(m.get("value", 0))
            except (TypeError, ValueError):
                pass
            break
    divisor = float(scaling_x) if scaling_x else 1
    return int(sessions / divisor)


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


def per_x_money_income(target, scaling_x=1, scaling_extra="", context=None):
    income = float(target.get("money_income", 0) or 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(income / divisor)


def per_x_foreign_culture_pops(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(cache.get("foreign_culture_pop_count", 0) / divisor)


def per_x_foreign_religion_pops(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    divisor = float(scaling_x) if scaling_x else 1
    return int(cache.get("foreign_religion_pop_count", 0) / divisor)


def per_x_vassals(target, scaling_x=1, scaling_extra="", context=None):
    cache = target.get("_calc_cache", {}) or {}
    vassal_counts = cache.get("vassal_counts", {})
    if not scaling_extra or scaling_extra == "all":
        count = sum(vassal_counts.values())
    else:
        count = vassal_counts.get(scaling_extra, 0)
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


def per_x_excess_resource(target, scaling_x=1, scaling_extra="", context=None):
    if not scaling_extra:
        return 0
    excess = target.get("resource_excess", {}) or {}
    amount = max(0, float(excess.get(scaling_extra, 0) or 0))
    divisor = float(scaling_x) if scaling_x else 1
    return int(amount / divisor)


def per_x_nations_in_shared_market(target, scaling_x=1, scaling_extra="", context=None):
    from app_core import mongo as _mongo
    nation_id = str(target.get("_id", ""))
    if not nation_id:
        return 0
    try:
        my_links = list(_mongo.db.market_links.find({"member": nation_id}, {"market": 1}))
        my_market_ids = [lnk["market"] for lnk in my_links if lnk.get("market")]
        if not my_market_ids:
            return 0
        partner_links = _mongo.db.market_links.find(
            {"market": {"$in": my_market_ids}, "member": {"$ne": nation_id}},
            {"member": 1},
        )
        unique_partners = {lnk["member"] for lnk in partner_links if lnk.get("member")}
        count = len(unique_partners)
    except Exception:
        count = 0
    divisor = float(scaling_x) if scaling_x else 1
    return int(count / divisor)


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
    "per_x_units": per_x_units,
    "per_x_naval_units": per_x_naval_units,
    "per_x_land_units": per_x_land_units,
    "per_x_sessions_with_library": per_x_sessions_with_library,
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
    "per_x_money_income": per_x_money_income,
    "per_x_foreign_culture_pops": per_x_foreign_culture_pops,
    "per_x_foreign_religion_pops": per_x_foreign_religion_pops,
    "per_x_vassals": per_x_vassals,
    "per_x_excess_resource": per_x_excess_resource,
    "per_x_nations_in_shared_market": per_x_nations_in_shared_market,
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
