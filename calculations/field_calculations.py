import math
import copy
from copy import deepcopy
from time import perf_counter
from app_core import mongo, json_data, category_data
from calculations.compute_functions import compute_pop_count, compute_field
from calculations.scaling_methods import get_scaling_multiplier
from bson.objectid import ObjectId

def _build_nation_calc_cache(target):
    target_id = str(target.get("_id", ""))
    if not target_id:
        return {}

    pops = list(category_data["pops"]["database"].find({"nation": target_id}, {"race": 1, "culture": 1, "religion": 1, "slave": 1}))
    race_ids = list({pop.get("race", "") for pop in pops if pop.get("race")})

    race_object_ids = []
    for race_id in race_ids:
        try:
            race_object_ids.append(ObjectId(race_id))
        except Exception:
            continue

    bloodthirsty_races = set()
    if race_object_ids:
        races = category_data["races"]["database"].find(
            {"_id": {"$in": race_object_ids}},
            {"_id": 1, "negative_trait": 1}
        )
        for race in races:
            if race.get("negative_trait", "") == "Bloodthirsty":
                bloodthirsty_races.add(str(race.get("_id")))

    bloodthirsty_pop_count = 0
    for pop in pops:
        if pop.get("race", "") in bloodthirsty_races:
            bloodthirsty_pop_count += 1

    primary_culture_pop_count = 0
    primary_culture = target.get("primary_culture", "")
    if primary_culture:
        try:
            primary_culture_pop_count = category_data["pops"]["database"].count_documents(
                {"culture": primary_culture}
            )
        except Exception:
            pass
    
    primary_religion_pop_count = 0
    primary_religion = target.get("primary_culture", "")
    if primary_religion:
        try:
            primary_religion_pop_count = category_data["pops"]["database"].count_documents(
                {"religion": primary_religion}
            )
        except Exception:
            pass

    nation_name = target.get("name", "")

    # Load raw node tiles — admin-range filtering happens later in calculate_all_fields
    # once law_totals are available (needed for the nomadic multiplier check).
    node_tiles = []
    if nation_name:
        for tile in mongo.db.hex_map_tiles.find(
            {"owner": nation_name, "node.resource_type": {"$exists": True}},
            {"q": 1, "r": 1, "node.resource_type": 1, "city": 1, "district": 1, "wonder": 1, "_id": 0}
        ):
            rt = tile.get("node", {}).get("resource_type")
            if rt:
                node_tiles.append({
                    "q": tile.get("q"), "r": tile.get("r"), "rt": rt,
                    "has_building": bool(tile.get("city") or tile.get("district") or tile.get("wonder")),
                })

    culture_count  = len({p.get("culture")  for p in pops if p.get("culture")})
    religion_count = len({p.get("religion") for p in pops if p.get("religion")})
    slave_count    = sum(1 for p in pops if p.get("slave"))

    return {
        "pops": pops,
        "pop_count": len(pops) - slave_count,
        "bloodthirsty_pop_count": bloodthirsty_pop_count,
        "primary_culture_pop_count": primary_culture_pop_count,
        "primary_religion_pop_count": primary_religion_pop_count,
        "culture_count": culture_count,
        "religion_count": religion_count,
        "slave_count": slave_count,
        "_node_tiles": node_tiles,
        # territory_node_counts, active_node_counts, and out_of_range_tiles are
        # populated by calculate_all_fields after law_totals are available.
    }

def calculate_all_fields(target, schema, target_data_type, return_breakdowns=False, instrumentation=None):
    local_timings = {}

    def record_timing(name, start_time):
        local_timings[name] = round((perf_counter() - start_time) * 1000, 2)

    calc_start = perf_counter()
    schema_properties = schema.get("properties", {})

    phase_start = perf_counter()
    if target_data_type == "nation":
        target["_calc_cache"] = _build_nation_calc_cache(target)
    record_timing("build_calc_cache_ms", phase_start)

    phase_start = perf_counter()
    external_modifiers = collect_external_requirements(target, schema, target_data_type)
    record_timing("collect_external_requirements_ms", phase_start)

    phase_start = perf_counter()
    external_modifiers_total = sum_external_modifier_totals(external_modifiers)
    if target_data_type in ("nation", "nation_jobs"):
        prosperity_mods = collect_prosperity_modifiers(target)
        for k, v in prosperity_mods.items():
            external_modifiers_total[k] = external_modifiers_total.get(k, 0) + v
    record_timing("sum_external_modifiers_ms", phase_start)

    phase_start = perf_counter()
    modifiers = collect_modifiers(target, target_data_type)
    modifier_totals = sum_modifier_totals(modifiers, target)
    record_timing("collect_and_sum_modifiers_ms", phase_start)

    phase_start = perf_counter()
    laws = collect_laws(target, schema)
    law_totals = sum_law_totals(laws)
    record_timing("collect_and_sum_laws_ms", phase_start)

    phase_start = perf_counter()
    district_details = {}
    districts = []
    if target_data_type == "nation":
        district_details = calculate_district_details(target, schema_properties, modifier_totals, law_totals, external_modifiers_total)
        districts = collect_nation_districts(target, law_totals)
    elif target_data_type == "nation_jobs":
        district_details = calculate_district_details(target, schema_properties, modifier_totals, law_totals, external_modifiers_total)
        districts = collect_nation_districts(target, law_totals)
    elif target_data_type == "merchant":
        district_details = json_data["merchant_production_districts"]
        district_details.update(json_data["merchant_specialty_districts"])
        district_details.update(json_data["merchant_luxury_districts"])
        districts = collect_merchant_districts(target, district_details)
    elif target_data_type == "mercenary":
        district_details = json_data["mercenary_districts"]
        districts = collect_mercenary_districts(target, district_details)
    district_totals = sum_district_totals(districts)
    record_timing("district_calculations_ms", phase_start)

    city_totals = {}
    city_schema_totals = {}
    tech_totals = {}
    loose_node_totals = {}
    territory_terrain_totals = {}
    job_details = {}
    job_totals = {}
    land_unit_details = {}
    naval_unit_details = {}
    support_unit_details = {}
    unit_totals = {}
    prestige_modifiers = {}
    title_modifiers = {}
    nation_tagged_sources = None

    phase_start = perf_counter()
    if target_data_type == "nation":
        cities = collect_cities(target)
        city_totals = sum_city_totals(cities)
        city_schema_totals = sum_modifier_totals(collect_city_schema_modifiers(target), target)

        technologies = _normalize_technologies(target.get("technologies"))
        tech_totals = sum_tech_totals(technologies)

        loose_nodes = target.get("nodes", {})
        loose_node_totals = sum_loose_node_totals(loose_nodes, modifier_totals, external_modifiers_total, law_totals, tech_totals)

        all_terrain_rules = _collect_all_terrain_rules(target)
        proximity_rules = _collect_all_proximity_rules(target)

        # Compute admin range now that law_totals is available for nomadic detection.
        _is_nomadic = law_totals.get("nomadic", 0) > 0
        target["is_nomadic"] = _is_nomadic
        _admin_val = target.get("administration", 1) or 1
        from helpers.hex_map_helpers import compute_admin_range_out_of_range as _compute_oor
        _nation_name = target.get("name", "")
        out_of_range = _compute_oor(_nation_name, _admin_val, nomadic=_is_nomadic) if _nation_name else set()
        # Filter cached node tiles by admin range and populate counts for node correction below.
        _node_tiles = (target.get("_calc_cache") or {}).get("_node_tiles", [])
        _oor_set = out_of_range
        territory_node_counts = {}
        active_node_counts = {}
        _luxury_resource_keys = {r["key"] for r in json_data["luxury_resources"]}
        for _nt in _node_tiles:
            _coord = (_nt["q"], _nt["r"])
            if _coord in _oor_set:
                continue
            _res = _nt["rt"]
            territory_node_counts[_res] = territory_node_counts.get(_res, 0) + 1
            if _nt.get("has_building") or _res in _luxury_resource_keys:
                active_node_counts[_res] = active_node_counts.get(_res, 0) + 1
        _cache = target.setdefault("_calc_cache", {})
        _cache["out_of_range_tiles"] = out_of_range
        _cache["territory_node_counts"] = territory_node_counts
        _cache["active_node_counts"] = active_node_counts

        if proximity_rules or out_of_range:
            effective_territory_types = apply_proximity_terrain_overrides(target, proximity_rules, out_of_range)
        else:
            effective_territory_types = target.get("territory_types", {})
        # Store for use in breakdown tooltips (same data as the calculation)
        target["_calc_cache"]["effective_territory_types"] = effective_territory_types
        territory_terrain_totals = collect_territory_terrain(effective_territory_types, all_terrain_rules)

        jobs_assigned = collect_jobs_assigned(target)
        job_details = calculate_job_details(target, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)
        job_totals = sum_job_totals(target, jobs_assigned, job_details)

        land_units_assigned = collect_land_units_assigned(target)
        nation_tagged_sources = _build_unit_tagged_sources(target, schema, district_details)

        land_unit_details = calculate_unit_details(target, "land", modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total, schema, district_details, nation_tagged_sources)

        naval_units_assigned = collect_naval_units_assigned(target)
        naval_unit_details = calculate_unit_details(target, "naval", modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total, schema, district_details, nation_tagged_sources)

        support_units_assigned = collect_support_units_assigned(target)
        support_unit_details = calculate_unit_details(target, "support", modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total, schema, district_details, nation_tagged_sources)

        unit_totals = sum_all_unit_totals(land_units_assigned, land_unit_details, naval_units_assigned, naval_unit_details, support_units_assigned, support_unit_details)

        prestige_modifiers = {}
        if target.get("empire", False):
            prestige_modifiers = calculate_prestige_modifiers(target, schema_properties)
        
        calculate_karma_from_negative_stockpiles(target, modifier_totals)
    elif target_data_type == "nation_jobs":
        job_details = calculate_job_details(target, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)
    elif target_data_type == "character":
        positive_title_modifiers = calculate_title_modifiers(target.get("positive_titles", []), target_data_type, schema_properties)
        negative_title_modifiers = calculate_title_modifiers(target.get("negative_titles", []), target_data_type, schema_properties)
        title_modifiers = positive_title_modifiers.copy()
        for key, value in negative_title_modifiers.items():
            title_modifiers[key] = title_modifiers.get(key, 0) + value
        # District and law modifiers scoped to ruling characters
        from calculations.source_adapters import DistrictAdapter as _DistrictAdapter, LawAdapter as _LawAdapter
        for _contrib in _DistrictAdapter.collect_for_character(target):
            for _k, _v in _contrib.modifiers.items():
                district_totals[_k] = district_totals.get(_k, 0) + _v
        for _contrib in _LawAdapter.collect_for_character(target):
            for _k, _v in _contrib.modifiers.items():
                district_totals[_k] = district_totals.get(_k, 0) + _v
    elif target_data_type == "market":
        primary_resource = target.get("primary_resource", "")
        secondary_resource_one = target.get("secondary_resource_one", "")
        secondary_resource_two = target.get("secondary_resource_two", "")
        law_totals[primary_resource + "_production"] = law_totals.get("primary_resource_production", 0)
        law_totals[secondary_resource_one + "_production"] = law_totals.get("secondary_resource_production", 0)
        law_totals[secondary_resource_two + "_production"] = law_totals.get("secondary_resource_production", 0)
    record_timing("target_specific_calculations_ms", phase_start)

    attributes_to_precalculate = ["administration", "effective_territory", "current_territory", "route_capacity", "effective_pop_capacity", "pop_count", "land_unit_capacity"]

    phase_start = perf_counter()
    overall_total_modifiers = {}
    _karma_sources = [
        ("external",   external_modifiers_total),
        ("modifiers",  modifier_totals),
        ("districts",  district_totals),
        ("tech",       tech_totals),
        ("loose_nodes",loose_node_totals),
        ("terrain",    territory_terrain_totals),
        ("cities",     city_totals),
        ("city_mods",  city_schema_totals),
        ("laws",       law_totals),
        ("jobs",       job_totals),
        ("units",      unit_totals),
        ("prestige",   prestige_modifiers),
        ("titles",     title_modifiers),
    ]
    for _label, d in _karma_sources:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
    calculated_values = {"district_details": district_details, "job_details": job_details, "land_unit_details": land_unit_details, "naval_unit_details": naval_unit_details, "support_unit_details": support_unit_details}

    # Correct {resource}_nodes counts using tile-based truth.
    # Multiple buildings on one tile still activate its node only once. For
    # nomadic nations every territory node tile is automatically active.
    _cache = target.get("_calc_cache") or {}
    _territory_node_counts = _cache.get("territory_node_counts", {})
    if _territory_node_counts:
        _active_node_counts = _cache.get("active_node_counts", {})
        _is_nomadic = law_totals.get("nomadic", 0) > 0
        for _res, _territory_count in _territory_node_counts.items():
            _key = _res + "_nodes"
            _current = overall_total_modifiers.get(_key, 0)
            if _is_nomadic:
                _corrected = _territory_count
            else:
                # Cap at distinct activated tiles (not territory count, not building count)
                _corrected = _active_node_counts.get(_res, 0)
            if _corrected != _current:
                overall_total_modifiers[_key] = _corrected

    record_timing("build_overall_modifiers_ms", phase_start)
    
    phase_start = perf_counter()
    for field in attributes_to_precalculate:
        base_value = schema_properties.get(field, {}).get("base_value", 0)
        calculated_values[field] = compute_field(
            field, target, base_value, schema_properties.get(field, {}),
            overall_total_modifiers
        )
        target[field] = calculated_values[field]
    record_timing("precalculate_core_fields_ms", phase_start)

    phase_start = perf_counter()
    effective_territory_modifiers = calculate_effective_territory_modifiers(target, schema_properties)

    route_capacity_modifiers = calculate_route_capacity_modifiers(target)

    effective_pop_capacity_modifiers = calculate_effective_pop_capacity_modifiers(target)

    for d in [effective_territory_modifiers, route_capacity_modifiers, effective_pop_capacity_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value

    overall_total_modifiers = parse_meta_modifiers(target, overall_total_modifiers)

    if target_data_type == "nation" and target.get("infamy", 0) >= 50:
        overall_total_modifiers["defensive_pact_slots"] = overall_total_modifiers.get("defensive_pact_slots", 0) - 1
        overall_total_modifiers["military_alliance_slots"] = overall_total_modifiers.get("military_alliance_slots", 0) - 1

    if target_data_type == "nation":
        _apply_vassal_tribute_modifiers(target, overall_total_modifiers)

    record_timing("capacity_and_meta_modifiers_ms", phase_start)

    # Excess pop capacity penalties — applied before field calc so stability_loss_chance
    # is correct, and preserved through the food-state rebuild via modifier_totals.
    _excess_pops = 0
    if target_data_type == "nation":
        _pop_count = calculated_values.get("pop_count", 0)
        _eff_cap   = calculated_values.get("effective_pop_capacity", 0)
        _excess_pops = max(0, _pop_count - _eff_cap)
        if _excess_pops > 0:
            _excess_stab = 0.25 * _excess_pops
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + _excess_stab
            overall_total_modifiers["stability_loss_chance"] = overall_total_modifiers.get("stability_loss_chance", 0) + _excess_stab
            _excess_flee = 0.05 * _excess_pops
            modifier_totals["pop_flee_chance"] = modifier_totals.get("pop_flee_chance", 0) + _excess_flee
            overall_total_modifiers["pop_flee_chance"] = overall_total_modifiers.get("pop_flee_chance", 0) + _excess_flee

    #print(overall_total_modifiers)

    phase_start = perf_counter()
    for field, field_schema in schema_properties.items():
        if isinstance(field_schema, dict) and field_schema.get("calculated") and field not in calculated_values.keys():
            base_value = field_schema.get("base_value", 0)
            calculated_values[field] = compute_field(
                field, target, base_value, field_schema,
                overall_total_modifiers
            )
            target[field] = calculated_values[field]
    record_timing("calculate_remaining_fields_ms", phase_start)

    if target_data_type == "nation":
        _pfc = calculated_values.get("pop_flee_chance", 0)
        _pfc_clamped = max(0.0, min(1.0, _pfc))
        calculated_values["pop_flee_chance"] = _pfc_clamped
        target["pop_flee_chance"] = _pfc_clamped

    # Calculate progress per session for progress quests
    phase_start = perf_counter()
    if "progress_quests" in target:
        target["progress_quests"] = compute_field(
            "progress_quests", target, 0, {},
            overall_total_modifiers
        )
        calculated_values["progress_quests"] = target["progress_quests"]
    record_timing("progress_quest_calc_ms", phase_start)
        
    phase_start = perf_counter()
    if target_data_type == "nation":
        food_consumption_per_pop = 1 + overall_total_modifiers.get("food_consumption_per_pop", 0)
        food_consumption = calculated_values.get("pop_count", 0) * food_consumption_per_pop
        if food_consumption_per_pop < 1:
            food_consumption = math.ceil(food_consumption)
        else:
            food_consumption = math.floor(food_consumption)
        food_consumption += _excess_pops  # +1 food consumed per pop over capacity

        excess_food = calculated_values.get("resource_excess", {}).get("food", 0) + target.get("resource_storage", {}).get("food", 0)

        if excess_food < -food_consumption / 2:
            # Nation is Starving (producing/storing < 50% of consumption)
            overall_total_modifiers["land_attack"]  = overall_total_modifiers.get("land_attack",  0) - 2
            overall_total_modifiers["land_defense"] = overall_total_modifiers.get("land_defense", 0) - 2
            overall_total_modifiers["naval_attack"]  = overall_total_modifiers.get("naval_attack",  0) - 2
            overall_total_modifiers["naval_defense"] = overall_total_modifiers.get("naval_defense", 0) - 2
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + 0.50
            # Zero out all non-food job production; large offset restores food jobs
            modifier_totals["job_resource_production"]         = modifier_totals.get("job_resource_production", 0) - 100
            modifier_totals["minimum_job_resource_production"] = -100
            modifier_totals["hunter_food_production"]          = modifier_totals.get("hunter_food_production",  0) + 100
            modifier_totals["farmer_food_production"]          = modifier_totals.get("farmer_food_production",  0) + 100
            modifier_totals["fisherman_food_production"]       = modifier_totals.get("fisherman_food_production", 0) + 100

        elif excess_food < 0:
            # Nation is Underfed (producing/storing 50–99% of consumption)
            overall_total_modifiers["land_attack"]  = overall_total_modifiers.get("land_attack",  0) - 1
            overall_total_modifiers["land_defense"] = overall_total_modifiers.get("land_defense", 0) - 1
            overall_total_modifiers["naval_attack"]  = overall_total_modifiers.get("naval_attack",  0) - 1
            overall_total_modifiers["naval_defense"] = overall_total_modifiers.get("naval_defense", 0) - 1
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + 0.25
            # Reduce non-food yields by 1 (minimum 1 preserved by natural floor)
            modifier_totals["job_resource_production"] = modifier_totals.get("job_resource_production", 0) - 1
            modifier_totals["hunter_food_production"]  = modifier_totals.get("hunter_food_production",  0) + 1
            modifier_totals["farmer_food_production"]  = modifier_totals.get("farmer_food_production",  0) + 1
            modifier_totals["fisherman_food_production"] = modifier_totals.get("fisherman_food_production", 0) + 1

        if excess_food < 0:
            #print("Nation excess food is less than 0")
            job_details = calculate_job_details(target, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)
            job_totals = sum_job_totals(target, target.get("jobs", {}), job_details)
            calculated_values["job_details"] = job_details

            overall_total_modifiers = {}

            for d in [external_modifiers_total, modifier_totals, district_totals, tech_totals, loose_node_totals, territory_terrain_totals, city_totals, city_schema_totals, law_totals, job_totals, unit_totals, prestige_modifiers, title_modifiers]:
                for key, value in d.items():
                    overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value

            #print(overall_total_modifiers)
                        
            calculated_values["stability_loss_chance"] = compute_field(
                "stability_loss_chance", target, schema_properties.get("stability_loss_chance", {}).get("base_value", 0), schema_properties.get("stability_loss_chance", {}),
                overall_total_modifiers
            )
            calculated_values["resource_production"] = compute_field(
                "resource_production", target, schema_properties.get("resource_production", {}).get("base_value", 0), schema_properties.get("resource_production", {}),
                overall_total_modifiers
            )
            target["resource_production"] = calculated_values["resource_production"]
            calculated_values["resource_excess"] = compute_field(
                "resource_excess", target, schema_properties.get("resource_excess", {}).get("base_value", 0), schema_properties.get("resource_excess", {}),
                overall_total_modifiers
            )
            calculated_values["land_attack"] = compute_field(
                "land_attack", target, schema_properties.get("land_attack", {}).get("base_value", 0), schema_properties.get("land_attack", {}),
                overall_total_modifiers
            )
            calculated_values["land_defense"] = compute_field(
                "land_defense", target, schema_properties.get("land_defense", {}).get("base_value", 0), schema_properties.get("land_defense", {}),
                overall_total_modifiers
            )
            calculated_values["naval_attack"] = compute_field(
                "naval_attack", target, schema_properties.get("naval_attack", {}).get("base_value", 0), schema_properties.get("naval_attack", {}),
                overall_total_modifiers
            )
            calculated_values["naval_defense"] = compute_field(
                "naval_defense", target, schema_properties.get("naval_defense", {}).get("base_value", 0), schema_properties.get("naval_defense", {}),
                overall_total_modifiers
            )

        else:
            #Nation is Sated
            pass
    record_timing("food_state_adjustments_ms", phase_start)

    if target_data_type == "nation":
        _etypes = (target.get("_calc_cache") or {}).get("effective_territory_types")
        if _etypes is not None:
            calculated_values["effective_territory_types"] = _etypes

    phase_start = perf_counter()
    if return_breakdowns:
        component_sources = {
            "base": {},
            "modifiers": modifier_totals,
            "districts": district_totals if target_data_type == "nation" else {},
            "cities": city_totals if target_data_type == "nation" else {},
            "laws": law_totals,
            "tech": tech_totals if target_data_type == "nation" else {},
            "jobs": job_totals if target_data_type == "nation" else {},
            "units": unit_totals if target_data_type == "nation" else {},
            "terrain": territory_terrain_totals if target_data_type == "nation" else {},
            "loose_nodes": loose_node_totals if target_data_type == "nation" else {},
            "external": external_modifiers_total,
            "prestige": prestige_modifiers if target_data_type == "nation" else {},
            "titles": title_modifiers if target_data_type == "nation" else {},
        }
        breakdowns = compute_nation_breakdowns(
            target,
            schema_properties,
            component_sources,
            overall_total_modifiers,
            calculated_values,
            nation_tagged_sources if target_data_type == "nation" else None,
        )
        record_timing("compute_breakdowns_ms", phase_start)
        local_timings["total_calculate_all_fields_ms"] = round((perf_counter() - calc_start) * 1000, 2)
        if instrumentation is not None:
            instrumentation.update(local_timings)
        target.pop("_calc_cache", None)
        return calculated_values, breakdowns

    record_timing("compute_breakdowns_ms", phase_start)
    local_timings["total_calculate_all_fields_ms"] = round((perf_counter() - calc_start) * 1000, 2)
    if instrumentation is not None:
        instrumentation.update(local_timings)

    target.pop("_calc_cache", None)
    return calculated_values

def calculate_prestige_modifiers(target, schema_properties):
    prestige = int(target.get("prestige", 50))
    gov_type = target.get("government_type", "Unknown")
    nomadic = schema_properties.get("government_type", {}).get("laws", {}).get(gov_type, {}).get("nomadic", 0)

    prestige_modifiers = {}
    if prestige > 90:
        prestige_modifiers["karma"] = 6
        prestige_modifiers["stability_gain_chance"] = 0.25
        prestige_modifiers["strength"] = 2
        prestige_modifiers["effective_pop_capacity"] = 6
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 25
        else:
            prestige_modifiers["effective_territory"] = 50
    elif prestige > 80:
        prestige_modifiers["karma"] = 4
        prestige_modifiers["stability_gain_chance"] = 0.20
        prestige_modifiers["strength"] = 2
        prestige_modifiers["effective_pop_capacity"] = 5
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 20
        else:
            prestige_modifiers["effective_territory"] = 45
    elif prestige > 70:
        prestige_modifiers["karma"] = 2
        prestige_modifiers["stability_gain_chance"] = 0.15
        prestige_modifiers["strength"] = 1
        prestige_modifiers["effective_pop_capacity"] = 4
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 15
        else:
            prestige_modifiers["effective_territory"] = 40
    elif prestige > 60:
        prestige_modifiers["stability_gain_chance"] = 0.10
        prestige_modifiers["strength"] = 1
        prestige_modifiers["effective_pop_capacity"] = 3
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 12
        else:
            prestige_modifiers["effective_territory"] = 35
    elif prestige > 40:
        prestige_modifiers["stability_gain_chance"] = 0.05
        prestige_modifiers["effective_pop_capacity"] = 2
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 10
        else:
            prestige_modifiers["effective_territory"] = 30
    elif prestige > 30:
        prestige_modifiers["karma"] = -2
        prestige_modifiers["stability_loss_chance"] = 0.10
        prestige_modifiers["strength"] = -1
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 8
        else:
            prestige_modifiers["effective_territory"] = 25
    elif prestige > 20:
        prestige_modifiers["karma"] = -4
        prestige_modifiers["stability_loss_chance"] = 0.15
        prestige_modifiers["strength"] = -1
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 5
        else:
            prestige_modifiers["effective_territory"] = 15
    elif prestige > 10:
        prestige_modifiers["karma"] = -6
        prestige_modifiers["stability_loss_chance"] = 0.20
        prestige_modifiers["strength"] = -2
        prestige_modifiers["effective_pop_capacity"] = -1
        if nomadic > 0:
            prestige_modifiers["effective_territory"] = 3
        else:
            prestige_modifiers["effective_territory"] = 10
    else:
        prestige_modifiers["karma"] = -8
        prestige_modifiers["stability_loss_chance"] = 0.25
        prestige_modifiers["strength"] = -3
        prestige_modifiers["effective_pop_capacity"] = -2

    return prestige_modifiers

def collect_modifiers(target, target_data_type=None):
    modifiers = target.get("modifiers", [])
    if not target_data_type:
        return modifiers
    scope_defs = json_data.get("scope_definitions", {})
    entity_type = "nation" if target_data_type == "nation_jobs" else target_data_type
    def _keep(m):
        scope = m.get("scope", "")
        if not scope:
            return True
        return scope_defs.get(scope, {}).get("target_type", entity_type) == entity_type
    return [m for m in modifiers if _keep(m)]

def collect_laws(target, schema):
    collected_laws = []
    laws_list = schema.get("laws", [])
    for law_name in laws_list:
        current_law_list = schema["properties"].get(law_name, {}).get("laws", {})
        target_law = target.get(law_name, "")
        law = current_law_list.get(target_law)
        if law:
            collected_laws.append(law)
    return collected_laws

def calculate_district_details(target, schema_properties, modifier_totals, law_totals, external_modifiers_total):
    district_details = {}
    for district_type, district_data in json_data["nation_imperial_districts"].items():
        district_details[district_type] = copy.deepcopy(district_data)
    return district_details

def collect_nation_districts(target, law_totals):
    collected_modifiers = []

    def synergy_matches(node, requirement):
        if not node:
            return False
        if isinstance(requirement, list):
            return "any" in requirement or node in requirement
        return requirement == "any" or node == requirement

    def get_synergies(dd):
        if "synergies" in dd:
            return dd["synergies"]
        req = dd.get("synergy_requirement", "")
        mods = dd.get("synergy_modifiers", {})
        if req or mods:
            return [{"requirement": req, "modifiers": mods, "node_active": dd.get("synergy_node_active", True)}]
        return []

    imperial_district_json_data = json_data["nation_imperial_districts"]

    if target.get("empire", False):
        imperial_district = target.get("imperial_district", {})
        imperial_district_type = imperial_district.get("type", "")
        imperial_district_node = imperial_district.get("node", "")
        imperial_dd = imperial_district_json_data.get(imperial_district_type, {})
        collected_modifiers.append(imperial_dd.get("modifiers", {}))
        node_bonus_applied = False
        for syn in get_synergies(imperial_dd):
            if synergy_matches(imperial_district_node, syn.get("requirement", "")):
                collected_modifiers.append(syn.get("modifiers", {}))
                if syn.get("node_active", True) and imperial_district_node and not node_bonus_applied:
                    collected_modifiers.append({imperial_district_node + "_nodes": 1})
                    node_bonus_applied = True
        if not node_bonus_applied and imperial_district_node:
            collected_modifiers.append({imperial_district_node + "_nodes": 1})

    return collected_modifiers

def collect_merchant_districts(target, district_details):
    collected_modifiers = []

    for i in range(1, 4):
        production_district = target.get("production_district_" + str(i), "")
        if production_district:
            collected_modifiers.append(district_details.get(production_district, {}).get("modifiers", {}))
    
    collected_modifiers.append(district_details.get(target.get("specialty_district", ""), {}).get("modifiers", {}))
    collected_modifiers.append(district_details.get(target.get("luxury_district", ""), {}).get("modifiers", {}))

    return collected_modifiers

def collect_mercenary_districts(target, district_details):
    collected_modifiers = []

    for district in target.get("districts", []):
        collected_modifiers.append(district_details.get(district, {}).get("modifiers", {}))
    
    return collected_modifiers

def calculate_title_modifiers(titles, target_data_type, schema_properties):
    title_modifiers = {}
    title_data = deepcopy(json_data["positive_titles"])
    title_data.update(json_data["negative_titles"])
    for title in titles:
        specific_title_data = copy.deepcopy(title_data.get(title, {}))
        for key, value in specific_title_data.get("modifiers", {}).items():
            if key.startswith(target_data_type + "_"):
                temp_key = key.replace(target_data_type + "_", "")
                if "_per_" in temp_key:
                    stats = ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]
                    split_key = temp_key.split("_per_")
                    modifier = split_key[0]
                    stat = split_key[1]
                    if stat in stats:
                        temp_key = modifier
                        calculated_target = calculate_all_fields(target, category_data["characters"]["schema"], "character")
                        value *= calculated_target.get(stat, 0)
                        value = max(value, 0)
                    title_modifiers[temp_key] = title_modifiers.get(temp_key, 0) + value
                else:
                    title_modifiers[temp_key] = title_modifiers.get(temp_key, 0) + value
    return title_modifiers

def collect_cities(target):
    nation_cities = target.get("cities", [])
    collected_modifiers = []
    city_json_data = json_data["cities"]
    wall_json_data = json_data["walls"]
    for city in nation_cities:
        city_type = city.get("type", "")
        if not city_type:
            continue
        city_node = city.get("node", "")
        city_modifiers = city_json_data.get(city_type, {}).get("modifiers", {})
        wall_modifiers = wall_json_data.get(city.get("wall", ""), {}).get("modifiers", {})
        collected_modifiers.append(city_modifiers)
        collected_modifiers.append(wall_modifiers)
        if city_node:
            collected_modifiers.append({city_node + "_nodes": 1})

    return collected_modifiers

def collect_city_schema_modifiers(target):
    """Collect _modifiers arrays from city JSON defs (supports conditional/scaled modifiers)."""
    result = []
    city_json = json_data["cities"]
    for city in target.get("cities", []):
        city_type = city.get("type", "")
        if not city_type:
            continue
        result.extend(city_json.get(city_type, {}).get("_modifiers", []))
    return result

_DEFAULT_TECHNOLOGIES = {"political_philosophy": {"researched": True}}

def _normalize_technologies(technologies):
    if not technologies or not isinstance(technologies, dict):
        return _DEFAULT_TECHNOLOGIES
    return technologies

def sum_tech_totals(technologies):
    technologies = _normalize_technologies(technologies)
    tech_totals = {}
    for tech, value in technologies.items():
        if value.get("researched", False):
            modifiers = json_data["tech"].get(tech, {}).get("modifiers", {})
            for key, value in modifiers.items():
                tech_totals[key] = tech_totals.get(key, 0) + value
    return tech_totals

def sum_loose_node_totals(loose_nodes, modifier_totals, external_modifiers_total, law_totals, tech_totals):
    node_totals = {}
    loose_node_value = 0
    production_of_available_nodes = 0
    nomadic = False
    if law_totals.get("nomadic", 0) > 0:
        nomadic = True
    
    loose_node_value += modifier_totals.get("loose_node_value", 0) + law_totals.get("loose_node_value", 0) + external_modifiers_total.get("loose_node_value", 0) + tech_totals.get("loose_node_value", 0)
    if nomadic:
        loose_node_value += modifier_totals.get("nomad_loose_node_value", 0) + law_totals.get("nomad_loose_node_value", 0) + external_modifiers_total.get("nomad_loose_node_value", 0) + tech_totals.get("nomad_loose_node_value", 0)
    
    production_of_available_nodes += modifier_totals.get("production_of_available_nodes", 0) + law_totals.get("production_of_available_nodes", 0) + external_modifiers_total.get("production_of_available_nodes", 0) + tech_totals.get("production_of_available_nodes", 0)
    if nomadic:
        production_of_available_nodes += modifier_totals.get("nomad_production_of_available_nodes", 0) + law_totals.get("nomad_production_of_available_nodes", 0) + external_modifiers_total.get("nomad_production_of_available_nodes", 0) + tech_totals.get("nomad_production_of_available_nodes", 0)

    for node, value in loose_nodes.items():
        node_totals[node + "_production"] = value * loose_node_value
        if value > 0:
            node_totals[node + "_production"] += production_of_available_nodes
    
    return node_totals


def _collect_all_terrain_rules(target):
    """Collect structured terrain production rules from all modifier sources on a nation."""
    from calculations.source_adapters import _extract_terrain_rules_from_list

    all_rules = []

    # Direct modifiers on the nation (modifier objects)
    direct_mods = target.get("modifiers", [])
    if isinstance(direct_mods, list):
        all_rules.extend(_extract_terrain_rules_from_list(direct_mods, "direct_modifiers"))

    # DB-driven district modifiers (def_key districts — not covered by legacy pipeline)
    for district in target.get("districts", []):
        if not isinstance(district, dict) or not district.get("def_key"):
            continue
        dd = _resolve_def(district)
        if not dd:
            continue
        label = dd.get("display_name", district["def_key"])
        all_rules.extend(_extract_terrain_rules_from_list(dd.get("modifiers", []), label))
        unlocked_upgrades = district.get("upgrades", [])
        if unlocked_upgrades:
            upgrade_map = {u["key"]: u for u in dd.get("upgrades", []) if u.get("key")}
            for upg_key in unlocked_upgrades:
                upg = upgrade_map.get(upg_key)
                if upg:
                    all_rules.extend(_extract_terrain_rules_from_list(
                        upg.get("modifiers", []), f"{label} ({upg_key})"
                    ))

    return all_rules


def _collect_all_proximity_rules(target):
    """Collect node_proximity_terrain_override rules from all sources for a nation.

    Sources checked:
    - The nation's own modifiers[] array.
    - modifiers[] on artifacts equipped by the nation's ruler characters.
    """
    from calculations.source_adapters import _extract_proximity_rules_from_list

    all_rules = []

    direct_mods = target.get("modifiers", [])
    if isinstance(direct_mods, list):
        all_rules.extend(_extract_proximity_rules_from_list(direct_mods, "direct_modifiers"))

    target_id = str(target.get("_id", ""))
    if target_id:
        rulers = list(category_data["characters"]["database"].find(
            {"ruling_nation_org": target_id}, {"_id": 1}
        ))
        for ruler in rulers:
            artifacts = list(category_data["artifacts"]["database"].find(
                {"owner": str(ruler["_id"]), "equipped": True},
                {"modifiers": 1, "name": 1}
            ))
            for artifact in artifacts:
                artifact_mods = artifact.get("modifiers", [])
                if isinstance(artifact_mods, list):
                    label = f"Artifact: {artifact.get('name', 'Unknown Artifact')}"
                    all_rules.extend(_extract_proximity_rules_from_list(artifact_mods, label))

    return all_rules


def apply_proximity_terrain_overrides(target, proximity_rules, out_of_range=None):
    """Return a territory_types dict with node proximity and admin-range overrides applied.

    Always derives starting counts from actual tile data (idempotent).
    Works per-tile so multiple overlapping rules never double-count a tile.

    - proximity_rules: tiles near specific node resources count as a different terrain
    - out_of_range: tiles beyond the nation's admin range count as disconnected
    """
    from helpers.hex_map_helpers import hex_distance, get_node_resource_positions

    nation_name = target.get("name", "")
    if not nation_name:
        return dict(target.get("territory_types", {}))

    has_proximity = bool(proximity_rules)
    has_range     = bool(out_of_range)
    if not has_proximity and not has_range:
        return dict(target.get("territory_types", {}))

    owned_tiles = list(mongo.db.hex_map_tiles.find(
        {"owner": nation_name}, {"q": 1, "r": 1, "terrain": 1, "_id": 0}
    ))
    # Per-tile effective terrain (starts as actual terrain, then overridden)
    tile_terrain = {}
    for tile in owned_tiles:
        terrain = tile.get("terrain", "")
        if terrain:
            tile_terrain[(tile["q"], tile["r"])] = terrain

    # Proximity overrides — last matching rule wins for each tile
    for rule in (proximity_rules or []):
        if rule.get("rule_type") != "node_proximity_terrain_override":
            continue
        node_resource = rule.get("node_resource", "")
        terrain_as    = rule.get("terrain_as", "")
        distance      = int(rule.get("distance", 1))
        if not node_resource or not terrain_as:
            continue
        node_positions = get_node_resource_positions(node_resource)
        if not node_positions:
            continue
        for (tq, tr), terrain in list(tile_terrain.items()):
            if terrain == terrain_as:
                continue
            if any(hex_distance(tq, tr, nq, nr) <= distance for nq, nr in node_positions):
                tile_terrain[(tq, tr)] = terrain_as

    # Admin range override — out-of-range tiles count as disconnected (no resources)
    if has_range:
        for coord in out_of_range:
            if coord in tile_terrain:
                tile_terrain[coord] = "disconnected"

    territory_types = {}
    for terrain in tile_terrain.values():
        territory_types[terrain] = territory_types.get(terrain, 0) + 1

    return {k: v for k, v in territory_types.items() if v > 0}


def collect_territory_terrain(territory_types, terrain_rules):
    """Calculate resource production contributions from terrain tiles.

    territory_types is a {terrain: count} dict (already with any proximity
    overrides applied). terrain_rules is a list of structured rule dicts
    produced by _collect_all_terrain_rules().
    """
    terrain_json = json_data["terrains"]
    total_modifiers = {}

    for terrain, tile_count in territory_types.items():
        base_terrain = terrain_json.get(terrain, {})
        if not base_terrain:
            continue

        rules = [r for r in terrain_rules if r.get("terrain") == terrain]

        # Determine which resource this terrain produces (swap_resource wins; last one)
        resource = base_terrain.get("resource", "none")
        for r in rules:
            if r["rule_type"] == "swap_resource" and r.get("resource"):
                resource = r["resource"]
        if resource == "none":
            continue

        # Count required (additive deltas, clamped to ≥ 1)
        count_required = base_terrain.get("count_required", 4)
        for r in rules:
            if r["rule_type"] == "count_required_delta":
                count_required += int(r.get("value", 0))
        count_required = max(count_required, 1)

        base_production = tile_count // count_required

        def apply_multipliers(res, base_prod):
            m = 1.0
            for r in rules:
                if r["rule_type"] != "multiplier":
                    continue
                scoped_res = r.get("resource")
                if scoped_res is None or scoped_res == res:
                    m *= float(r.get("value", 1.0))
            return int(base_prod * m)

        # Base resource contribution
        production = apply_multipliers(resource, base_production)
        key = resource + "_production"
        total_modifiers[key] = total_modifiers.get(key, 0) + production

        # Extra resource contributions
        for r in rules:
            if r["rule_type"] != "extra_resource":
                continue
            extra_res = r.get("resource")
            cr = int(r.get("value", 1))
            if not extra_res or cr < 1:
                continue
            extra_prod = tile_count // cr
            extra_prod = apply_multipliers(extra_res, extra_prod)
            key = extra_res + "_production"
            total_modifiers[key] = total_modifiers.get(key, 0) + extra_prod

    return total_modifiers

def collect_jobs_assigned(target):
    return target.get("jobs", {})

def calculate_job_details(target, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total):
    job_details = json_data["jobs"]
    modifier_sources = [modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total]
    general_resources = json_data["general_resources"]
    general_resources = [resource["key"] for resource in general_resources]
    unique_resources = json_data["unique_resources"]
    unique_resources = [resource["key"] for resource in unique_resources]

    new_job_details = {}
    for job, details in job_details.items():
        locked = modifier_totals.get("locks_" + job, 0) + district_totals.get("locks_" + job, 0) + city_totals.get("locks_" + job, 0) + law_totals.get("locks_" + job, 0) + external_modifiers_total.get("locks_" + job, 0)
        meets_job_requirements = check_job_requirements(target, details, external_modifiers_total)
        if not locked and meets_job_requirements:
            new_details = copy.deepcopy(details)
            original_job_production = details.get("production", {})
            all_resource_production = 0
            all_resource_upkeep = 0
            all_resource_production_multiplier = 1
            all_resource_upkeep_multiplier = 1
            minimum_production_bonus = 0
            minimum_production_bonus += modifier_totals.get("minimum_" + job + "_resource_production", 0) + district_totals.get("minimum_" + job + "_resource_production", 0) + city_totals.get("minimum_" + job + "_resource_production", 0) + law_totals.get("minimum_" + job + "_resource_production", 0) + external_modifiers_total.get("minimum_" + job + "_resource_production", 0)
            minimum_production_bonus += modifier_totals.get("minimum_job_resource_production", 0) + district_totals.get("minimum_job_resource_production", 0) + city_totals.get("minimum_job_resource_production", 0) + law_totals.get("minimum_job_resource_production", 0) + external_modifiers_total.get("minimum_job_resource_production", 0)
            for source in modifier_sources:
                for modifier, value in source.items():
                    if modifier.startswith("imperial_") and target.get("empire", False):
                        modifier = modifier.replace("imperial_", "")
                    if modifier.startswith(job) or modifier.startswith("job"):
                        resource = modifier.replace(job + "_", "").replace("job_", "").replace("_production", "").replace("_upkeep", "")
                        if resource != "resource" and modifier.endswith("production"):
                            new_details.setdefault("production", {})[resource] = new_details.get("production", {}).get(resource, 0) + value
                        elif resource != "resource" and modifier.endswith("upkeep"):
                            new_details.setdefault("upkeep", {})[resource] = new_details.get("upkeep", {}).get(resource, 0) + value
                        elif modifier.endswith("resource_production"):
                            all_resource_production = all_resource_production + value
                        elif modifier.endswith("resource_upkeep"):
                            all_resource_upkeep = all_resource_upkeep + value
                        elif modifier.endswith("resource_production_mult"):
                            all_resource_production_multiplier = all_resource_production_multiplier * value
                        elif modifier.endswith("resource_upkeep_mult"):
                            all_resource_upkeep_multiplier = all_resource_upkeep_multiplier * value
                        

            for resource in new_details.get("production", {}):
                if resource in general_resources or resource in unique_resources:
                    new_production = new_details["production"][resource]
                    new_production += all_resource_production
                    new_production = new_production * all_resource_production_multiplier
                    if all_resource_production_multiplier < 1:
                        new_production = math.ceil(new_production)
                    elif all_resource_production_multiplier > 1:
                        new_production = math.floor(new_production)
                    else:
                        # Keep fractional values (e.g. 1/3) so rounding happens after
                        # multiplying by assigned worker count in sum_job_totals.
                        pass
                    # Preserve fractional modifier-added production (e.g. Bureaucracy's 1/3 magic).
                    # Baseline minimum of 1 only applies to resources a job natively produces.
                    base_resource_minimum = 1 if original_job_production.get(resource, 0) > 0 else 0
                    resource_minimum = max(base_resource_minimum + minimum_production_bonus, 0)
                    new_production = max(new_production, resource_minimum)
                    new_details["production"][resource] = new_production

            for resource in new_details.get("upkeep", {}):
                if resource in general_resources or resource in unique_resources:
                    new_upkeep = new_details["upkeep"][resource]
                    new_upkeep += all_resource_upkeep
                    new_upkeep = new_upkeep * all_resource_upkeep_multiplier
                    if all_resource_upkeep_multiplier < 1:
                        new_upkeep = int(math.ceil(new_upkeep))
                    elif all_resource_upkeep_multiplier > 1:
                        new_upkeep = int(math.floor(new_upkeep))
                    else:
                        new_upkeep = int(round(new_upkeep))
                    new_details["upkeep"][resource] = new_upkeep

            new_job_details[job] = new_details
    
    return new_job_details

_PROSPERITY_EFFECTS = {
    # (tier, role): {modifier_key: value, ...}
    ("Wretched", "Savior"): {
        "karma":                    -4,
        "resource_production":      -1,
        "research_production":      -1,
        "land_defense":             -3,
        "naval_defense":            -3,
        "stability_loss_chance":    0.50,
    },
    ("Wretched", "Ravager"): {
        "karma":                    4,
        "land_attack":              3,
        "naval_attack":             3,
    },
    ("Despairing", "Savior"): {
        "karma":                    -4,
        "resource_production":      -1,
        "land_defense":             -2,
        "naval_defense":            -2,
        "stability_loss_chance":    0.35,
    },
    ("Despairing", "Ravager"): {
        "karma":                    4,
        "land_attack":              2,
        "naval_attack":             2,
    },
    ("Struggling", "Savior"): {
        "karma":                    -2,
        "research_production":      -1,
        "land_defense":             -1,
        "naval_defense":            -1,
        "stability_loss_chance":    0.25,
    },
    ("Struggling", "Ravager"): {
        "karma":                    2,
        "land_attack":              1,
        "naval_attack":             1,
    },
    ("Hopeful", "Savior"): {
        "karma":                    -2,
        "stability_loss_chance":    0.15,
    },
    ("Hopeful", "Ravager"): {
        "karma":                    2,
        "land_attack":              1,
        "naval_attack":             1,
    },
}

def collect_prosperity_modifiers(target):
    role = target.get("prosperity_role", "None")
    if not role or role == "None":
        return {}
    region_id = target.get("region", "")
    if not region_id:
        return {}
    try:
        region_doc = category_data["regions"]["database"].find_one(
            {"_id": ObjectId(region_id)}, {"prosperity": 1}
        )
        tier = region_doc.get("prosperity", "Hopeful") if region_doc else "Hopeful"
    except Exception:
        return {}
    return dict(_PROSPERITY_EFFECTS.get((tier, role), {}))

def check_job_requirements(target, job_details, overall_total_modifiers):
    requirements = job_details.get("requirements", {})
    meets_requirements = True
    region = target.get("region", "")
    try:
        region_name = category_data["regions"]["database"].find_one({"_id": ObjectId(region)}, {"name": 1})["name"]
    except:
        region_name = ""

    # Collect def_keys from DB-driven districts for requirement checks
    def_keys = [d.get("def_key", "") for d in target.get("districts", []) if d.get("def_key")]

    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = False
            for def_key in def_keys:
                    if def_key in value:
                        has_district = True
                        break
            if not has_district:
                meets_requirements = False
        elif requirement == "region":
            if region_name not in value:
                meets_requirements = False
        elif requirement == "modifier":
            for modifier in value:
                if overall_total_modifiers.get(modifier, 0) <= 0:
                    meets_requirements = False
        elif requirement == "wonder":
            for wonder in value:
                wonderdb = category_data["wonders"]["database"]
                wonder = wonderdb.find_one({"name": wonder})
                if str(target.get("_id", "")) != wonder.get("owner_nation", ""):
                    meets_requirements = False
        elif requirement == "empire":
            if target.get("empire", False) != value:
                meets_requirements = False
        elif requirement == "name":
            if target.get("name", "") not in value:
                meets_requirements = False
    
    return meets_requirements

def collect_land_units_assigned(target):
    return target.get("land_units", {})

def collect_naval_units_assigned(target):
    return target.get("naval_units", {})

def collect_support_units_assigned(target):
    return target.get("support_units", {})

def _db_prerequisites_to_requirements(prerequisites):
    """Convert DB prerequisites array [{type, value}] to the JSON requirements dict format."""
    requirements = {}
    for pre in prerequisites or []:
        ptype = pre.get("type", "")
        val = pre.get("value", "")
        if not ptype:
            continue
        # Blank artifact values are kept as empty string so check_unit_requirements can hide the unit
        if not val and ptype != "artifact":
            continue
        # Map DB type names to JSON requirement keys used by check_unit_requirements
        if ptype == "technology":
            key = "research"
        elif ptype == "nation":
            key = "name"
        else:
            key = ptype
        requirements.setdefault(key, []).append(val)
    return requirements

_UNIT_STAT_NAMES = [
    "attack", "defense", "hp", "morale", "damage",
    "retaliation_damage", "range", "speed", "armor",
]

def _get_unit_stat_prefixes(unit_type_str, is_support, is_magical, roles, base_name):
    """Return all modifier prefixes that apply to this unit.

    Prefixes follow this naming convention (all combinations that apply):
      [magical|mundane_][land|naval|support_][role_]unit
      [land|naval|support_]non_ruler_unit   (all processed units are non-ruler)
      civilian_unit                         (hardcoded for units named 'Civilian')

    roles: dict with keys 'melee', 'ranged', 'cavalry' (booleans)
    base_name: raw unit name (without era prefix) for special cases
    """
    prefixes = ["unit", "non_ruler_unit"]
    magic_tag = "magical" if is_magical else "mundane"

    if is_support:
        type_base = "support"
    elif unit_type_str == "Land":
        type_base = "land"
    elif unit_type_str == "Naval":
        type_base = "naval"
    else:
        type_base = None

    if type_base:
        prefixes.append(f"{type_base}_unit")
        prefixes.append(f"{type_base}_non_ruler_unit")
        prefixes.append(f"{magic_tag}_{type_base}_unit")

        # Role-based prefixes: land_melee_unit, land_cavalry_unit, etc.
        for role in ("melee", "ranged", "cavalry"):
            if roles.get(role):
                prefixes.append(f"{type_base}_{role}_unit")

    prefixes.append(f"{magic_tag}_unit")

    # Role-only prefixes (type-agnostic): melee_unit, cavalry_unit, etc.
    for role in ("melee", "ranged", "cavalry"):
        if roles.get(role):
            prefixes.append(f"{role}_unit")

    # Hardcoded: civilian_unit matches any unit whose base name is "Civilian"
    if base_name and base_name.lower() == "civilian":
        prefixes.append("civilian_unit")

    return prefixes


# ---------------------------------------------------------------------------
# Universal labeled modifier helpers
# ---------------------------------------------------------------------------

_EXTERNAL_FIELD_LABEL_MAP = {
    "overlord": "Overlord",
    "primary_religion": "Religion",
    "wonders": "Wonder",
    "region": "Region",
    "vassals": "Vassal",
    "markets": "Market",
    "owned_markets": "Owned Market",
    "pops": "Pop",
}


def _field_label_prefix(field_name):
    """Human-readable label prefix for an external_calculation_requirements field."""
    return _EXTERNAL_FIELD_LABEL_MAP.get(field_name, field_name.replace("_", " ").title())


def _strip_modifier_key(key, target_data_type, modifier_prefix):
    """Strip the target_data_type (with optional modifier_prefix) from a modifier key.

    Returns (stripped_key, matched).  If neither prefix matches, matched=False and
    stripped_key equals the original key.
    """
    if modifier_prefix:
        full_prefix = f"{modifier_prefix}_{target_data_type}_"
        if key.startswith(full_prefix):
            return key[len(full_prefix):], True
    std_prefix = f"{target_data_type}_"
    if key.startswith(std_prefix):
        return key[len(std_prefix):], True
    return key, False


def _apply_special_mod_multipliers(stripped_key, val, obj, linked_schema):
    """Apply _per_market_tier and _per_member special multipliers.

    Returns (final_key, final_val).
    """
    final_key = stripped_key
    final_val = val

    if "_per_market_tier" in final_key:
        schema_props = (linked_schema or {}).get("properties", {})
        tier_mult = (
            schema_props.get("tier", {})
            .get("laws", {})
            .get(obj.get("tier", "I"), {})
            .get("tier_multiplier", 1)
        )
        if tier_mult > 0:
            final_val = val * tier_mult
        elif "market" in schema_props and obj.get("market"):
            mkt = mongo.db["markets"].find_one({"_id": ObjectId(obj["market"])})
            if mkt:
                tier_mult = (
                    category_data["markets"]["schema"]
                    .get("properties", {})
                    .get("tier", {})
                    .get("laws", {})
                    .get(mkt.get("tier", "I"), {})
                    .get("tier_multiplier", 1)
                )
                final_val = val * tier_mult
        final_key = final_key.replace("_per_market_tier", "")

    if "_per_member" in final_key:
        member_count = mongo.db["market_links"].count_documents({"market": str(obj.get("_id", ""))})
        final_val = final_val * member_count
        final_key = final_key.replace("_per_member", "")

    return final_key, final_val


def _extract_labeled_from_object(obj, required_fields, linked_schema, target_data_type,
                                  modifier_prefix, base_label, field_name="", target=None):
    """Extract labeled modifier entries from a single linked object.

    Returns [{"label": str, "modifiers": dict}].

    Plain modifiers (external_modifiers arrays, modifier arrays, titles, nodes) are
    grouped under *base_label*.  Enum-law fields get their own sub-label that includes
    the selected value so the tooltip is self-explanatory.

    Keys that cannot be matched to target_data_type produce a DEBUG print and are
    skipped so they don't silently corrupt totals.
    """
    schema_props = (linked_schema or {}).get("properties", {})
    plain_mods = {}        # accumulated under base_label
    per_field_entries = [] # entries needing their own enum sub-label

    for req_field in required_fields:
        if isinstance(req_field, dict):
            # Nested requirement (e.g., {"artifacts": [...]}) – handled elsewhere.
            continue

        if req_field not in obj:
            continue

        req_field_schema = schema_props.get(req_field, {})
        field_type = req_field_schema.get("bsonType", "")

        # ── external_modifiers array ──────────────────────────────────────
        if field_type == "array" and req_field == "external_modifiers":
            for modifier in obj[req_field]:
                if modifier.get("type") != target_data_type:
                    continue
                key = modifier.get("modifier", "")
                val = modifier.get("value", 0)
                if key and val:
                    scaling = modifier.get("scaling", "flat")
                    scaling_x = float(modifier.get("scaling_x") or 1)
                    scaling_extra = modifier.get("scaling_extra") or ""
                    if scaling and scaling != "flat" and target is not None:
                        val = val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                    plain_mods[key] = plain_mods.get(key, 0) + val

        # ── enum field with laws (stances, traits, types …) ──────────────
        elif field_type == "enum" and req_field_schema.get("laws"):
            field_value = obj.get(req_field, "")
            if not field_value or field_value == "None":
                continue
            law_mods = req_field_schema["laws"].get(field_value, {})
            entry_mods = {}
            for key, val in law_mods.items():
                stripped, matched = _strip_modifier_key(key, target_data_type, modifier_prefix)
                if not matched:
                    continue
                final_key, final_val = _apply_special_mod_multipliers(stripped, val, obj, linked_schema)
                if final_val:
                    entry_mods[final_key] = entry_mods.get(final_key, 0) + final_val
            if entry_mods:
                field_label = req_field_schema.get("label", req_field.replace("_", " ").title())
                sub_label = f"{base_label} ({field_label}: {field_value})"
                per_field_entries.append({"label": sub_label, "modifiers": entry_mods})

        # ── modifiers array (new scope-based or old {field, value}) ──────
        elif field_type == "array" and req_field == "modifiers":
            _scope_defs_local = json_data.get("scope_definitions", {})
            from calculations.source_adapters import _resolve_modifier_type as _res_mod_type_ext
            for modifier in obj[req_field]:
                scope = modifier.get("scope", "")
                if scope:
                    tgt = _scope_defs_local.get(scope, {}).get("target_type", "")
                    if tgt != target_data_type:
                        continue
                    mod_field = _res_mod_type_ext(modifier)
                    mod_val = modifier.get("value", 0)
                    if mod_field and mod_val:
                        scaling = modifier.get("scaling", "flat")
                        scaling_x = float(modifier.get("scaling_x") or 1)
                        scaling_extra = modifier.get("scaling_extra") or ""
                        if scaling and scaling != "flat" and target is not None:
                            mod_val = mod_val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                        plain_mods[mod_field] = plain_mods.get(mod_field, 0) + mod_val
                else:
                    mod_field = modifier.get("field", "")
                    mod_val = modifier.get("value", 0)
                    stripped, matched = _strip_modifier_key(mod_field, target_data_type, modifier_prefix)
                    if matched and mod_val:
                        scaling = modifier.get("scaling", "flat")
                        scaling_x = float(modifier.get("scaling_x") or 1)
                        scaling_extra = modifier.get("scaling_extra") or ""
                        if scaling and scaling != "flat" and target is not None:
                            mod_val = mod_val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                        plain_mods[stripped] = plain_mods.get(stripped, 0) + mod_val

        # ── title arrays ──────────────────────────────────────────────────
        elif field_type == "array" and req_field in ("positive_titles", "negative_titles"):
            title_mods = calculate_title_modifiers(obj[req_field], target_data_type, schema_props)
            for k, v in title_mods.items():
                if v:
                    plain_mods[k] = plain_mods.get(k, 0) + v

        # ── wonder node (json_resource_enum) ─────────────────────────────
        elif field_type == "json_resource_enum" and req_field == "node":
            resource = obj.get(req_field)
            if resource:
                plain_mods[f"{resource}_nodes"] = plain_mods.get(f"{resource}_nodes", 0) + 1

        # ── catch-all: unknown field type ─────────────────────────────────
        else:
            print(
                f"[MODIFIER DEBUG] Unknown field type in labeled extraction: "
                f"field_name='{field_name}', source_label='{base_label}', "
                f"req_field='{req_field}', field_type='{field_type}', "
                f"obj_keys={list(obj.keys())[:10]}"
            )

    result = []
    if plain_mods:
        result.append({"label": base_label, "modifiers": plain_mods})
    result.extend(per_field_entries)
    return result


def _collect_external_labeled(target, schema, target_data_type):
    """Build labeled tagged sources for all external_calculation_requirements.

    Skips 'rulers' and 'primary_race' because those are already handled with
    richer labeling inside _build_unit_tagged_sources.

    Returns [{"label": str, "modifiers": dict}].
    """
    SKIP_FIELDS = {"rulers", "primary_race"}
    tagged = []
    external_reqs = schema.get("external_calculation_requirements", {})
    schema_properties = schema.get("properties", {})

    # ── Global modifiers ─────────────────────────────────────────────────
    try:
        _gml_sd = json_data.get("scope_definitions", {})
        from calculations.source_adapters import _resolve_modifier_type as _rmt_gml
        for global_mod in mongo.db["global_modifiers"].find():
            mods = {}
            for modifier in global_mod.get("external_modifiers", []):
                if modifier.get("type") == target_data_type:
                    key = modifier.get("modifier", "")
                    val = modifier.get("value", 0)
                    if key and val:
                        mods[key] = mods.get(key, 0) + val
            for modifier in global_mod.get("modifiers", []):
                scope = modifier.get("scope", "")
                if scope and _gml_sd.get(scope, {}).get("target_type", "") == target_data_type:
                    key = _rmt_gml(modifier)
                    val = modifier.get("value", 0)
                    if key and val:
                        mods[key] = mods.get(key, 0) + val
            if mods:
                tagged.append({
                    "label": f"Global: {global_mod.get('name', 'Global Modifier')}",
                    "modifiers": mods,
                })
    except Exception as exc:
        print(f"[MODIFIER DEBUG] Error processing global modifiers: {exc}")

    # ── Per-field external requirements ──────────────────────────────────
    for field_name, required_fields in external_reqs.items():
        if field_name in SKIP_FIELDS:
            continue

        field_schema = schema_properties.get(field_name, {})
        collections = field_schema.get("collections")
        if not collections:
            continue

        modifier_prefix = None
        if isinstance(required_fields, dict):
            modifier_prefix = required_fields.get("modifier_prefix")
            required_fields = required_fields.get("fields", [])

        if required_fields is None:
            required_fields = []
        elif not isinstance(required_fields, list):
            required_fields = [required_fields]

        # Filter out nested dicts (handled elsewhere / skipped for now)
        plain_required = [f for f in required_fields if isinstance(f, str)]
        if not plain_required:
            continue

        label_prefix = _field_label_prefix(field_name)

        for collection in collections:
            linked_schema = category_data.get(collection, {}).get("schema", {})
            try:
                # ── join-table pattern (markets, owned_markets, vassals via linkCollection) ──
                if field_schema.get("linkCollection") and field_schema.get("linkQueryTarget"):
                    link_collection = field_schema["linkCollection"]
                    link_query_target = field_schema["linkQueryTarget"]
                    query_target = field_schema.get("queryTarget")
                    if not query_target:
                        continue

                    links = list(mongo.db[link_collection].find(
                        {link_query_target: str(target.get("_id", ""))},
                    ))
                    for link in links:
                        # Resolve target object to get a name for the label
                        tgt_obj_name = "Unknown"
                        tgt_obj = None
                        if query_target in link:
                            tgt_obj = mongo.db[collection].find_one({"_id": ObjectId(link[query_target])})
                            if tgt_obj:
                                tgt_obj_name = tgt_obj.get("name", tgt_obj.get("display_name", "Unknown"))

                        obj_label = f"{label_prefix}: {tgt_obj_name}"
                        link_schema = category_data.get(link_collection, {}).get("schema", {})

                        # Extract from the link row itself
                        tagged.extend(_extract_labeled_from_object(
                            link, plain_required, link_schema,
                            target_data_type, modifier_prefix, obj_label, field_name, target=target,
                        ))
                        # Extract from the target object
                        if tgt_obj is not None:
                            tagged.extend(_extract_labeled_from_object(
                                tgt_obj, plain_required, linked_schema,
                                target_data_type, modifier_prefix, obj_label, field_name, target=target,
                            ))

                # ── queryTargetAttribute pattern (wonders, vassals, owned_markets) ──
                elif field_schema.get("queryTargetAttribute"):
                    query_attr = field_schema["queryTargetAttribute"]
                    if "_id" not in target:
                        continue
                    linked_objects = list(mongo.db[collection].find(
                        {query_attr: str(target["_id"])}
                    ))
                    for obj in linked_objects:
                        if not obj.get("equipped", True):
                            continue
                        obj_name = obj.get("name", obj.get("display_name", "Unknown"))
                        obj_label = f"{label_prefix}: {obj_name}"
                        tagged.extend(_extract_labeled_from_object(
                            obj, plain_required, linked_schema,
                            target_data_type, modifier_prefix, obj_label, field_name, target=target,
                        ))

                # ── simple direct-link pattern (overlord, religion, region) ──
                else:
                    object_id = target.get(field_name)
                    if not object_id:
                        continue
                    obj = mongo.db[collection].find_one({"_id": ObjectId(object_id)})
                    if not obj:
                        continue
                    obj_name = obj.get("name", obj.get("display_name", "Unknown"))
                    obj_label = f"{label_prefix}: {obj_name}"
                    tagged.extend(_extract_labeled_from_object(
                        obj, plain_required, linked_schema,
                        target_data_type, modifier_prefix, obj_label, field_name, target=target,
                    ))

            except Exception as exc:
                print(
                    f"[MODIFIER DEBUG] Error processing field '{field_name}' "
                    f"collection '{collection}': {exc}"
                )

    return tagged


def _build_unit_tagged_sources(target, schema, district_details):
    """Build a comprehensive list of labeled modifier sources for nation/unit breakdown.

    Returns list of {"label": str, "modifiers": dict}.  Each entry represents one
    distinct source (a law option, a district, a nation modifier, a technology, a city,
    a ruler modifier, an artifact, a racial trait, or any external source).
    """
    tagged = []
    schema_properties = schema.get("properties", {})

    # Laws
    for law_name in schema.get("laws", []):
        law_field = schema_properties.get(law_name, {})
        selected = target.get(law_name, "")
        law_data = law_field.get("laws", {}).get(selected)
        if law_data:
            law_label = law_field.get("label", law_name)
            tagged.append({"label": f"{law_label}: {selected}", "modifiers": law_data})

    # Districts
    def _synergy_matches(node, requirement):
        if not node:
            return False
        if isinstance(requirement, list):
            return "any" in requirement or node in requirement
        return requirement == "any" or node == requirement

    def _get_synergies(dd):
        if "synergies" in dd:
            return dd["synergies"]
        req = dd.get("synergy_requirement", "")
        mods = dd.get("synergy_modifiers", {})
        if req or mods:
            return [{"requirement": req, "modifiers": mods}]
        return []

    for district in target.get("districts", []):
        if not isinstance(district, dict):
            continue
        district_type = district.get("type", "")
        if not district_type:
            continue
        district_node = district.get("node", "")
        district_data = district_details.get(district_type, {})
        district_name = district_data.get("display_name", district_type)
        mods = district_data.get("modifiers", {})
        if mods:
            tagged.append({"label": f"District: {district_name}", "modifiers": mods})
        for syn in _get_synergies(district_data):
            if _synergy_matches(district_node, syn.get("requirement", "")):
                synergy_mods = syn.get("modifiers", {})
                if synergy_mods:
                    tagged.append({"label": f"District: {district_name} (Synergy)", "modifiers": synergy_mods})

    if target.get("empire", False):
        imperial_district = target.get("imperial_district", {})
        imperial_type = imperial_district.get("type", "")
        imperial_node = imperial_district.get("node", "")
        imperial_data = json_data["nation_imperial_districts"].get(imperial_type, {})
        imperial_name = imperial_data.get("display_name", imperial_type)
        mods = imperial_data.get("modifiers", {})
        if mods:
            tagged.append({"label": f"District: {imperial_name}", "modifiers": mods})
        for syn in _get_synergies(imperial_data):
            if _synergy_matches(imperial_node, syn.get("requirement", "")):
                synergy_mods = syn.get("modifiers", {})
                if synergy_mods:
                    tagged.append({"label": f"District: {imperial_name} (Synergy)", "modifiers": synergy_mods})

    # Technologies
    for tech_key, tech_val in _normalize_technologies(target.get("technologies")).items():
        if tech_val.get("researched"):
            tech_data = json_data["tech"].get(tech_key, {})
            mods = tech_data.get("modifiers", {})
            if mods:
                tech_name = tech_data.get("display_name", tech_key)
                tagged.append({"label": f"Technology: {tech_name}", "modifiers": mods})

    # Cities
    city_json = json_data["cities"]
    wall_json = json_data["walls"]
    from calculations.source_adapters import _resolve_modifier_type as _res_city_mod
    for city in target.get("cities", []):
        city_type = city.get("type", "")
        if not city_type:
            continue
        city_data = city_json.get(city_type, {})
        city_name = city_data.get("display_name", city_type)
        city_mods = city_data.get("modifiers", {})
        if city_mods:
            tagged.append({"label": f"City: {city_name}", "modifiers": city_mods})
        wall_data = wall_json.get(city.get("wall", ""), {})
        wall_mods = wall_data.get("modifiers", {})
        if wall_mods:
            wall_name = wall_data.get("display_name", city.get("wall", ""))
            tagged.append({"label": f"City: {wall_name}", "modifiers": wall_mods})
        for m in city_data.get("_modifiers", []):
            condition_scaling = m.get("condition_scaling") or ""
            if condition_scaling:
                try:
                    cond_x     = float(m.get("condition_scaling_x") or 1)
                    cond_extra = m.get("condition_scaling_extra") or ""
                    cond_op    = m.get("condition_operator") or ">="
                    cond_val   = float(m.get("condition_value") or 0)
                    actual     = get_scaling_multiplier(condition_scaling, target, scaling_x=cond_x, scaling_extra=cond_extra)
                    met = (
                        (cond_op == ">=" and actual >= cond_val) or
                        (cond_op == ">"  and actual >  cond_val) or
                        (cond_op == "<=" and actual <= cond_val) or
                        (cond_op == "<"  and actual <  cond_val) or
                        (cond_op == "==" and actual == cond_val)
                    )
                    if not met:
                        continue
                except Exception:
                    continue
            field = _res_city_mod(m)
            value = m.get("value", 0)
            if field and value:
                scaling = m.get("scaling", "flat")
                scaling_x = float(m.get("scaling_x") or 1)
                scaling_extra = m.get("scaling_extra") or ""
                if scaling and scaling != "flat":
                    value = value * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                cond_label = f" (income > {int(m.get('condition_value', 0))})" if condition_scaling else ""
                tagged.append({"label": f"City: {city_name}{cond_label}", "modifiers": {field: value}})

    # Nation modifiers — resolve new modifier_type format and fall back to legacy field/key
    from calculations.source_adapters import _resolve_modifier_type as _res_mod_type
    scope_defs_fc = json_data.get("scope_definitions", {})
    for mod in target.get("modifiers", []):
        scope = mod.get("scope", "")
        if scope:
            target_type = scope_defs_fc.get(scope, {}).get("target_type", "")
            if target_type and target_type != "nation":
                continue
        field = _res_mod_type(mod)
        value = mod.get("value", 0)
        if field and value:
            scaling = mod.get("scaling", "flat")
            scaling_x = float(mod.get("scaling_x") or 1)
            scaling_extra = mod.get("scaling_extra") or ""
            if scaling and scaling != "flat":
                value = value * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
            source = mod.get("source", "")
            label = f"Modifier: {source}" if source else "Modifier"
            tagged.append({"label": label, "modifiers": {field: value}})

    # External: rulers, their modifiers, titles, and artifacts
    nation_id = str(target.get("_id", ""))
    if nation_id:
        char_schema_props = category_data.get("characters", {}).get("schema", {}).get("properties", {})
        rulers = list(category_data["characters"]["database"].find(
            {"ruling_nation_org": nation_id},
            {"name": 1, "modifiers": 1, "positive_titles": 1, "negative_titles": 1},
        ))
        for ruler in rulers:
            ruler_name = ruler.get("name", "Unknown Ruler")

            # Ruler modifiers — support both new scope-based and legacy "nation_" prefix formats
            for mod in ruler.get("modifiers", []):
                scope = mod.get("scope", "")
                if scope:
                    # New format: use scope to determine target type
                    tgt = scope_defs_fc.get(scope, {}).get("target_type", "")
                    if tgt != "nation":
                        continue
                    field = _res_mod_type(mod)
                else:
                    # Legacy format: field name with "nation_" prefix
                    raw = mod.get("field", "") or mod.get("key", "")
                    if not raw or not raw.startswith("nation_"):
                        continue
                    field = raw[len("nation_"):]
                value = mod.get("value", 0)
                if field and value:
                    scaling = mod.get("scaling", "flat")
                    scaling_x = float(mod.get("scaling_x") or 1)
                    scaling_extra = mod.get("scaling_extra") or ""
                    if scaling and scaling != "flat":
                        value = value * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                    tagged.append({
                        "label": f"Ruler: {ruler_name}",
                        "modifiers": {field: value},
                    })

            # Ruler titles
            all_titles = ruler.get("positive_titles", []) + ruler.get("negative_titles", [])
            if all_titles:
                title_mods = calculate_title_modifiers(all_titles, "nation", char_schema_props)
                if title_mods:
                    tagged.append({"label": f"Ruler: {ruler_name} (Titles)", "modifiers": title_mods})

            # Artifacts equipped by this ruler
            ruler_id = str(ruler["_id"])
            artifacts = list(category_data["artifacts"]["database"].find(
                {"owner": ruler_id, "equipped": True},
                {"name": 1, "external_modifiers": 1},
            ))
            for artifact in artifacts:
                artifact_name = artifact.get("name", "Unknown Artifact")
                for ext_mod in artifact.get("external_modifiers", []):
                    if ext_mod.get("type") == "nation":
                        mod_key = ext_mod.get("modifier", "")
                        mod_val = ext_mod.get("value", 0)
                        if mod_key and mod_val:
                            tagged.append({
                                "label": f"Artifact: {artifact_name}",
                                "modifiers": {mod_key: mod_val},
                            })

    # Primary race traits (positive_trait / negative_trait)
    primary_race_id = target.get("primary_race")
    if primary_race_id:
        try:
            race = category_data["races"]["database"].find_one(
                {"_id": ObjectId(primary_race_id)},
                {"name": 1, "positive_trait": 1, "negative_trait": 1},
            )
            if race:
                race_name = race.get("name", "Unknown Race")
                race_schema_props = category_data["races"]["schema"].get("properties", {})
                for trait_field in ("positive_trait", "negative_trait"):
                    trait_value = race.get(trait_field, "")
                    if not trait_value or trait_value == "None":
                        continue
                    trait_laws = race_schema_props.get(trait_field, {}).get("laws", {})
                    raw_mods = trait_laws.get(trait_value, {})
                    # Strip "nation_" prefix; skip keys that don't apply to nations
                    stripped = {k[len("nation_"):]: v for k, v in raw_mods.items() if k.startswith("nation_")}
                    if stripped:
                        tagged.append({"label": f"Race: {race_name} ({trait_value})", "modifiers": stripped})
        except Exception:
            pass

    # Prestige (empire only)
    if target.get("empire", False):
        prestige_mods = calculate_prestige_modifiers(target, schema_properties)
        if prestige_mods:
            prestige_val = int(target.get("prestige", 50))
            tagged.append({"label": f"Prestige ({prestige_val})", "modifiers": prestige_mods})

    # Prosperity role modifiers
    prosperity_mods = collect_prosperity_modifiers(target)
    if prosperity_mods:
        role = target.get("prosperity_role", "None")
        region_id = target.get("region", "")
        try:
            region_doc = category_data["regions"]["database"].find_one(
                {"_id": ObjectId(region_id)}, {"prosperity": 1}
            )
            tier = region_doc.get("prosperity", "Hopeful") if region_doc else "Hopeful"
        except Exception:
            tier = "Hopeful"
        tagged.append({"label": f"Prosperity: {role} ({tier})", "modifiers": prosperity_mods})

    # All remaining external sources (global mods, religion, wonders, region,
    # overlord/vassal stances, markets) — labeled automatically.
    tagged.extend(_collect_external_labeled(target, schema, "nation"))

    return tagged


def _apply_unit_stat_modifiers(base_stats, unit_type_str, is_support, is_magical, roles, base_name, tagged_sources):
    """Compute effective_stats and per-stat breakdown from tagged modifier sources.

    tagged_sources: list of {"label": str, "modifiers": dict}
    Returns: (effective_stats, stat_breakdown)
      effective_stats: dict of stat → final value (None if stat is disabled)
      stat_breakdown:  dict of stat → [{"label": str, "value": num}, ...] (only modified stats)

    'strength' is a shorthand that applies to both attack and defense.
    Stats whose base value is None (flag disabled) are skipped.
    Bare 'strength' (e.g. from military_funding laws) applies to all units.
    Type-scoped bare strength (e.g. 'land_strength') applies only to matching types.
    """
    effective = dict(base_stats)
    breakdown = {stat: [] for stat in _UNIT_STAT_NAMES}
    prefixes = _get_unit_stat_prefixes(unit_type_str, is_support, is_magical, roles, base_name)

    if is_support:
        bare_strength_keys = {"strength", "support_strength"}
    elif unit_type_str == "Land":
        bare_strength_keys = {"strength", "land_strength"}
    elif unit_type_str == "Naval":
        bare_strength_keys = {"strength", "naval_strength"}
    else:
        bare_strength_keys = {"strength"}

    def _add_strength(val, label):
        if effective.get("attack") is not None:
            effective["attack"] = (effective["attack"] or 0) + val
            breakdown["attack"].append({"label": label, "value": val})
        if effective.get("defense") is not None:
            effective["defense"] = (effective["defense"] or 0) + val
            breakdown["defense"].append({"label": label, "value": val})

    for source in tagged_sources:
        label = source["label"]
        for modifier, value in source["modifiers"].items():
            if not value:
                continue
            if modifier in bare_strength_keys:
                _add_strength(value, label)
                continue
            for prefix in prefixes:
                if modifier == f"{prefix}_strength":
                    _add_strength(value, label)
                    break
                matched = False
                for stat in _UNIT_STAT_NAMES:
                    if modifier == f"{prefix}_{stat}":
                        if effective.get(stat) is not None:
                            effective[stat] = (effective[stat] or 0) + value
                            breakdown[stat].append({"label": label, "value": value})
                        matched = True
                        break
                if matched:
                    break

    stat_breakdown = {stat: entries for stat, entries in breakdown.items() if entries}
    return effective, stat_breakdown


def load_db_units(unit_type=None):
    """Load units from MongoDB and return them in the JSON calculation-compatible format.
    unit_type: 'land' or 'naval' (lowercase) to filter by type, or None for all.
    Ruler units are always excluded (they are not field units).
    Units whose name appears in multiple eras are keyed/displayed as "Era Name".
    """
    query = {"unit_class": {"$ne": "Ruler Unit"}}
    if unit_type == "support":
        query["unit_type"] = "Land"
        query["support"] = True
    elif unit_type == "land":
        query["unit_type"] = "Land"
        query["support"] = {"$ne": True}
    elif unit_type:
        query["unit_type"] = unit_type.capitalize()
    units_list = list(category_data["units"]["database"].find(query))

    # Detect names that appear across multiple eras
    name_eras = {}
    for unit in units_list:
        name = unit.get("name", "")
        era = unit.get("era", "")
        if name:
            name_eras.setdefault(name, set()).add(era)
    multi_era_names = {n for n, eras in name_eras.items() if len(eras) > 1}

    db_units = {}
    for unit in units_list:
        name = unit.get("name", "")
        if not name:
            continue
        era = unit.get("era", "")
        # Prefix era for units whose name collides across multiple eras
        if name in multi_era_names and era:
            key = f"{era} {name}"
            display_name = f"{era} {name}"
        else:
            key = name
            display_name = name
        upkeep = {}
        if unit.get("has_upkeep") and unit.get("upkeep"):
            for row in unit["upkeep"]:
                res = row.get("resource", "")
                cost = row.get("cost", 0)
                if res:
                    upkeep[res] = cost
        recruitment_cost = {}
        if unit.get("has_recruitment_cost") and unit.get("recruitment_cost") is not None:
            recruitment_cost = {"money": unit["recruitment_cost"]}
        requirements = _db_prerequisites_to_requirements(unit.get("prerequisites", []))

        # Range display: None if no range, single value if min==max, else "min-max"
        if unit.get("has_range"):
            min_r = unit.get("minimum_range")
            max_r = unit.get("maximum_range")
            if min_r is not None and max_r is not None:
                range_val = max_r if min_r == max_r else f"{min_r}-{max_r}"
            elif max_r is not None:
                range_val = max_r
            else:
                range_val = None
        else:
            range_val = None

        traits = unit.get("traits") or []
        is_magical = any(t.lower() == "magical" for t in traits)
        is_support = bool(unit.get("support"))

        db_units[key] = {
            "display_name": display_name,
            "base_name": name,  # raw name without era prefix, for civilian_unit matching
            "unit_type": unit.get("unit_type", ""),
            "unit_class": unit.get("unit_class", ""),
            "era": era,
            "is_support": is_support,
            "is_magical": is_magical,
            "melee": bool(unit.get("melee")),
            "ranged": bool(unit.get("ranged")),
            "cavalry": bool(unit.get("cavalry")),
            "recruitment_cost": recruitment_cost,
            "upkeep": upkeep,
            "requirements": requirements,
            "base_stats": {
                # attack/defense default has_* to True: these fields were added later,
                # so existing DB units that predate them should still show 0 (not None).
                "attack":              unit.get("attack", 0)             if unit.get("has_attack", True)              else None,
                "defense":             unit.get("defense", 0)            if unit.get("has_defense", True)             else None,
                "hp":                  unit.get("hp")                    if unit.get("has_hp")                        else None,
                "morale":              unit.get("morale")                if unit.get("has_morale")                    else None,
                "damage":              unit.get("damage")                if unit.get("has_damage")                    else None,
                "retaliation_damage":  unit.get("retaliation_damage")    if unit.get("has_retaliation_damage")        else None,
                "range":               range_val,
                "speed":               unit.get("speed")                 if unit.get("has_speed")                     else None,
                "armor":               unit.get("armor")                 if unit.get("has_armor")                     else None,
            },
        }
    return db_units


def _resolve_def(district_instance):
    """Return the district definition dict for a nation district instance.

    Checks def_key (MongoDB district_defs collection) first, then falls back
    to the legacy type key in the JSON files.
    """
    if not isinstance(district_instance, dict):
        return {}
    def_key = district_instance.get("def_key")
    if def_key:
        return mongo.db.district_defs.find_one({"key": def_key}) or {}
    legacy_type = district_instance.get("type", "")
    if not legacy_type:
        return {}
    for fname in ["nation_imperial_districts", "mercenary_districts",
                  "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts"]:
        data = json_data.get(fname, {}).get(legacy_type)
        if data:
            return data
    return {}


def _check_requirements_dict(nation, requirements):
    """Evaluate a requirements dict (as produced by _db_prerequisites_to_requirements) against a nation."""
    nation_district_def_keys = [d.get("def_key", "") for d in nation.get("districts", []) if isinstance(d, dict)]
    nation_district_categories = set()
    for dk in nation_district_def_keys:
        if dk:
            dd = _resolve_def({"def_key": dk})
            cat = dd.get("category", "")
            if cat:
                nation_district_categories.add(cat)

    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = any(
                d in nation_district_def_keys or d in nation_district_categories
                for d in value
            )
            if not has_district:
                return False
        elif requirement == "research":
            for tech in value:
                if not nation.get("technologies", {}).get(tech, {}).get("researched", False):
                    return False
        elif requirement == "race":
            nation_id = str(nation.get("_id", ""))
            has_race = False
            for race_name in value:
                race_doc = category_data["races"]["database"].find_one({"name": race_name}, {"_id": 1})
                if race_doc:
                    pop = category_data["pops"]["database"].find_one(
                        {"nation": nation_id, "race": str(race_doc["_id"])}, {"_id": 1}
                    )
                    if pop:
                        has_race = True
                        break
            if not has_race:
                return False
        elif requirement == "name":
            if not any(n in nation.get("name", "") for n in value):
                return False
    return True


def check_district_requirements(nation, district_def):
    """Return True if the nation meets all requirements to build the given district definition."""
    tier = district_def.get("tier", 1)
    if tier > 1:
        needed_tier = tier - 1
        category = district_def.get("category", "")
        has_prev = False
        for inst in nation.get("districts", []):
            dd = _resolve_def(inst)
            if dd.get("category") == category and dd.get("tier") == needed_tier:
                has_prev = True
                break
        if not has_prev:
            return False

    if any(
        isinstance(inst, dict) and _resolve_def(inst).get("key") == district_def.get("key")
        for inst in nation.get("districts", [])
    ):
        return False

    reqs = _db_prerequisites_to_requirements(district_def.get("requirements", []))
    return _check_requirements_dict(nation, reqs)


def check_upgrade_requirements(nation, upgrade_def):
    """Return True if the nation meets all requirements to unlock the given upgrade."""
    reqs = _db_prerequisites_to_requirements(upgrade_def.get("requirements", []))
    return _check_requirements_dict(nation, reqs)


def check_unit_requirements(target, unit_details):
    # Imperial units require the nation to be an empire
    if "Imperial" in (unit_details.get("unit_class") or "") and not target.get("empire", False):
        return False

    requirements = unit_details.get("requirements", {})
    meets_requirements = True
    nation_district_def_keys = {d.get("def_key", "") for d in target.get("districts", []) if isinstance(d, dict) and d.get("def_key")}
    nation_district_categories = set()
    for dk in nation_district_def_keys:
        dd = _resolve_def({"def_key": dk})
        cat = dd.get("category", "")
        if cat:
            nation_district_categories.add(cat)

    check_name = False
    check_defensive_pact = False
    check_military_alliance = False

    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = any(
                d in nation_district_def_keys or d in nation_district_categories
                for d in value
            )
            if not has_district:
                meets_requirements = False
        elif requirement == "research":
            technologies = _normalize_technologies(target.get("technologies"))
            for tech in value:
                if not technologies.get(tech, {}).get("researched", False):
                    meets_requirements = False
                    break
        elif requirement == "spell":
            meets_requirements = False
        elif requirement == "artifact":
            nation_id = str(target.get("_id", ""))
            # Lazily fetch ruler IDs once per unit check
            ruler_ids = None
            for art_val in value:
                if not art_val:
                    # Blank/unknown artifact prerequisite — hide the unit
                    meets_requirements = False
                    break
                if not nation_id:
                    meets_requirements = False
                    break
                if ruler_ids is None:
                    ruler_ids = [
                        str(c["_id"])
                        for c in category_data["characters"]["database"].find(
                            {"ruling_nation_org": nation_id}, {"_id": 1}
                        )
                    ]
                if not ruler_ids:
                    meets_requirements = False
                    break
                has_artifact = category_data["artifacts"]["database"].find_one({
                    "owner": {"$in": ruler_ids},
                    "name": art_val,
                    "equipped": True
                })
                if not has_artifact:
                    meets_requirements = False
                    break
        elif requirement == "name":
            check_name = True
        elif requirement == "defensive_pact":
            check_defensive_pact = True
        elif requirement == "military_alliance":
            check_military_alliance = True
        elif requirement == "mercenary":
            meets_requirements = False
        elif requirement == "race":
            nation_id = str(target.get("_id", ""))
            if not nation_id:
                meets_requirements = False
            else:
                has_race = False
                for race_name in value:
                    race_doc = category_data["races"]["database"].find_one(
                        {"name": race_name}, {"_id": 1}
                    )
                    if race_doc:
                        race_id = str(race_doc["_id"])
                        pop = category_data["pops"]["database"].find_one(
                            {"nation": nation_id, "race": race_id}, {"_id": 1}
                        )
                        if pop:
                            has_race = True
                            break
                if not has_race:
                    meets_requirements = False
    
    if check_name or check_defensive_pact or check_military_alliance:
        related = False
        if check_name:
            for name in requirements.get("name", []):
                if name in target.get("name", ""):
                    related = True
        
        if check_defensive_pact:
            defensive_pacts = []
            defensive_pacts = list(mongo.db.diplo_relations.find({"nation_1": str(target.get("_id", "")), "pact_type": {"$eq": "Defensive Pact"}}, {"nation_2": 1}))
            defensive_pacts += list(mongo.db.diplo_relations.find({"nation_2": str(target.get("_id", "")), "pact_type": {"$eq": "Defensive Pact"}}, {"nation_1": 1}))
            
            # Convert defensive pact IDs to nation objects with names
            defensive_pact_nations = []
            for pact in defensive_pacts:
                nation_id = pact.get("nation_1") if pact.get("nation_1") != str(target.get("_id", "")) else pact.get("nation_2")
                if nation_id:
                    nation = mongo.db.nations.find_one({"_id": ObjectId(nation_id)}, {"name": 1})
                    if nation:
                        defensive_pact_nations.append({
                            "id": nation_id,
                            "name": nation.get("name", "Unknown Nation")
                        })
            
            for required_defensive_pact in requirements.get("defensive_pact", []):
                if required_defensive_pact in [pact["name"] for pact in defensive_pact_nations]:
                    related = True

        if check_military_alliance:
            military_alliances = []
            military_alliances = list(mongo.db.diplo_relations.find({"nation_1": str(target.get("_id", "")), "pact_type": {"$eq": "Military Alliance"}}, {"nation_2": 1}))
            military_alliances += list(mongo.db.diplo_relations.find({"nation_2": str(target.get("_id", "")), "pact_type": {"$eq": "Military Alliance"}}, {"nation_1": 1}))
            
            # Convert military alliance IDs to nation objects with names
            military_alliance_nations = []
            for alliance in military_alliances:
                nation_id = alliance.get("nation_1") if alliance.get("nation_1") != str(target.get("_id", "")) else alliance.get("nation_2")
                if nation_id:
                    nation = mongo.db.nations.find_one({"_id": ObjectId(nation_id)}, {"name": 1})
                    if nation:
                        military_alliance_nations.append({
                            "id": nation_id,
                            "name": nation.get("name", "Unknown Nation")
                        })
            
            for required_military_alliance in requirements.get("military_alliance", []):
                if required_military_alliance in [alliance["name"] for alliance in military_alliance_nations]:
                    related = True
        
        if not related:
            meets_requirements = False

    return meets_requirements

def calculate_unit_details(target, unit_type, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total, schema=None, district_details=None, tagged_sources=None):
    unit_details = load_db_units(unit_type)

    modifier_sources = [modifier_totals, district_totals, city_totals, tech_totals, law_totals, external_modifiers_total]
    if tagged_sources is None:
        tagged_sources = _build_unit_tagged_sources(target, schema or {}, district_details or {})
    general_resources = json_data["general_resources"]
    general_resources = [resource["key"] for resource in general_resources]
    unique_resources = json_data["unique_resources"]
    unique_resources = [resource["key"] for resource in unique_resources]
    
    new_unit_details = {}
    for unit, details in unit_details.items():
        if check_unit_requirements(target, details):
            new_details = copy.deepcopy(details)
            has_name_req = bool(details.get("requirements", {}).get("name"))
            all_resource_upkeep = 0
            all_resource_upkeep_multiplier = 1
            for source in modifier_sources:
                for modifier, value in source.items():
                    if modifier.startswith(unit) or modifier.startswith("unit") or modifier.startswith(unit_type + "_unit") or (modifier.startswith("imperial_unit") and new_details.get("upkeep", {}).get("prestige", 0) > 0) or (modifier.startswith("unique_imperial_unit") and has_name_req and new_details.get("upkeep", {}).get("prestige", 0) > 0):
                        resource = modifier.replace(unit + "_", "").replace("unique_imperial_unit_", "").replace("imperial_unit_", "").replace(unit_type + "_unit_", "").replace("unit_", "").replace("_upkeep", "")
                        if resource != "resource" and modifier.endswith("upkeep"):
                            new_details.setdefault("upkeep", {})[resource] = new_details.get("upkeep", {}).get(resource, 0) + value
                        elif modifier.endswith("resource_upkeep"):
                            all_resource_upkeep = all_resource_upkeep + value
                        elif modifier.endswith("resource_upkeep_mult"):
                            all_resource_upkeep_multiplier = all_resource_upkeep_multiplier * value
            
            original_details = copy.deepcopy(new_details)

            for resource in original_details.get("upkeep", {}):
                if (resource in general_resources or resource in unique_resources) and new_details.get("upkeep", {}).get(resource, 0) <= 0:
                    del new_details["upkeep"][resource]
                elif resource in general_resources or resource in unique_resources:
                    new_upkeep = new_details["upkeep"][resource]
                    new_upkeep += all_resource_upkeep
                    new_upkeep = new_upkeep * all_resource_upkeep_multiplier
                    if all_resource_upkeep_multiplier < 1:
                        new_upkeep = int(math.ceil(new_upkeep))
                    elif all_resource_upkeep_multiplier > 1:
                        new_upkeep = int(math.floor(new_upkeep))
                    else:
                        new_upkeep = int(round(new_upkeep))
                    new_details["upkeep"][resource] = new_upkeep

            new_details["effective_stats"], new_details["stat_breakdown"] = _apply_unit_stat_modifiers(
                new_details["base_stats"],
                new_details.get("unit_type", ""),
                new_details.get("is_support", False),
                new_details.get("is_magical", False),
                {"melee": new_details.get("melee", False), "ranged": new_details.get("ranged", False), "cavalry": new_details.get("cavalry", False)},
                new_details.get("base_name", ""),
                tagged_sources,
            )

            new_unit_details[unit] = new_details

    return new_unit_details

def collect_external_requirements(target, schema, target_data_type):
    external_reqs = schema.get("external_calculation_requirements", {})
    collected_modifiers = []
    
    # Add global modifiers
    _gm_sd = json_data.get("scope_definitions", {})
    from calculations.source_adapters import _resolve_modifier_type as _rmt_gm
    global_modifiers = list(mongo.db["global_modifiers"].find())
    for global_mod in global_modifiers:
        for modifier in global_mod.get("external_modifiers", []):
            if modifier.get("type") == target_data_type:
                key = modifier.get("modifier", "")
                val = modifier.get("value", 0)
                scaling = modifier.get("scaling", "flat")
                scaling_x = float(modifier.get("scaling_x") or 1)
                scaling_extra = modifier.get("scaling_extra") or ""
                if scaling and scaling != "flat":
                    val = val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                collected_modifiers.append({key: val})
        for modifier in global_mod.get("modifiers", []):
            scope = modifier.get("scope", "")
            if scope and _gm_sd.get(scope, {}).get("target_type", "") == target_data_type:
                field = _rmt_gm(modifier)
                val = modifier.get("value", 0)
                scaling = modifier.get("scaling", "flat")
                scaling_x = float(modifier.get("scaling_x") or 1)
                scaling_extra = modifier.get("scaling_extra") or ""
                if scaling and scaling != "flat":
                    val = val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                if field and val:
                    collected_modifiers.append({field: val})

    for field, required_fields in external_reqs.items():
        field_schema = schema["properties"].get(field, {})

        collections = field_schema.get("collections")
        if not collections:
            continue

        # Handle new format with modifier_prefix
        modifier_prefix = None
        fields_as_modifiers = []
        if isinstance(required_fields, dict):
            modifier_prefix = required_fields.get("modifier_prefix")
            fields_as_modifiers = required_fields.get("fields_as_modifiers", [])
            required_fields = required_fields.get("fields", [])

        if required_fields is None:
            required_fields = []
        elif not isinstance(required_fields, list):
            required_fields = [required_fields]

        # Skip expensive linked-object scans when no fields are requested.
        if not required_fields and not fields_as_modifiers:
            continue

        for collection in collections:
            linked_object_schema = category_data.get(collection, {}).get("schema", {})

            # Handle indirect links through join tables
            if field_schema.get("linkCollection") and field_schema.get("linkQueryTarget"):
                link_collection = field_schema["linkCollection"]
                link_query_target = field_schema["linkQueryTarget"]
                query_target = field_schema.get("queryTarget")
                
                if query_target:
                    # Find all links in the join table
                    links = list(mongo.db[link_collection].find({link_query_target: str(target.get("_id", ""))}))
                    
                    for link in links:
                        # Check the link object itself for modifiers
                        collected_modifiers.extend(collect_external_modifiers_from_object(link, required_fields, category_data.get(link_collection, {}).get("schema", {}), target_data_type, modifier_prefix, fields_as_modifiers, target=target))

                        # Get the target object and check it too
                        if query_target in link:
                            target_id = link[query_target]
                            target_object = mongo.db[collection].find_one({"_id": ObjectId(target_id)})
                            if target_object:
                                collected_modifiers.extend(collect_external_modifiers_from_object(target_object, required_fields, linked_object_schema, target_data_type, modifier_prefix, fields_as_modifiers, target=target))

            elif field_schema.get("queryTargetAttribute"):
                query_target = field_schema["queryTargetAttribute"]
                linked_objects = []

                if "_id" in target:
                    linked_objects = list(mongo.db[collection].find({query_target: str(target.get("_id", ""))}))

                for object in linked_objects:
                    if object.get("equipped", True):
                        collected_modifiers.extend(collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type, modifier_prefix, fields_as_modifiers, target=target))
            else:
                object_id = target.get(field)
                if not object_id:
                    continue

                object = mongo.db[collection].find_one({"_id": ObjectId(object_id)})
                if not object:
                    continue
                collected_modifiers.extend(collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type, modifier_prefix, fields_as_modifiers, target=target))

    return collected_modifiers

def collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type, modifier_prefix=None, fields_as_modifiers=[], target=None):
    collected_modifiers = []

    for req_field in required_fields:
        if isinstance(req_field, dict):
            for key, value in req_field.items():
                req_field_schema = linked_object_schema["properties"].get(key, {})
                collections = req_field_schema.get("collections")
                if not collections:
                    continue

                for collection in collections:
                    linked_object_schema = category_data.get(collection, {}).get("schema", {})

                    # Handle indirect links through join tables
                    if req_field_schema.get("linkCollection") and req_field_schema.get("linkQueryTarget"):
                        link_collection = req_field_schema["linkCollection"]
                        link_query_target = req_field_schema["linkQueryTarget"]
                        query_target = req_field_schema.get("queryTarget")
                        
                        if query_target:
                            # Find all links in the join table
                            links = list(mongo.db[link_collection].find({link_query_target: str(object["_id"])}))
                            
                            for link in links:
                                # Check the link object itself for modifiers
                                collected_modifiers.extend(collect_external_modifiers_from_object(link, value, category_data.get(link_collection, {}).get("schema", {}), target_data_type, modifier_prefix, target=target))

                                # Get the target object and check it too
                                if query_target in link:
                                    target_id = link[query_target]
                                    target_object = mongo.db[collection].find_one({"_id": ObjectId(target_id)})
                                    if target_object:
                                        collected_modifiers.extend(collect_external_modifiers_from_object(target_object, value, linked_object_schema, target_data_type, modifier_prefix, target=target))

                    elif req_field_schema.get("queryTargetAttribute"):
                        query_target = req_field_schema["queryTargetAttribute"]
                        linked_objects = list(mongo.db[collection].find({query_target: str(object["_id"])}))
                        for object in linked_objects:
                            if object.get("equipped", True):
                                collected_modifiers.extend(collect_external_modifiers_from_object(object, value, linked_object_schema, target_data_type, modifier_prefix, target=target))
            continue
        else:
            req_field_schema = linked_object_schema["properties"].get(req_field, {})
            if req_field in object:
                field_type = linked_object_schema["properties"].get(req_field, {}).get("bsonType")

                if field_type == "array" and req_field == "external_modifiers":
                    for modifier in object[req_field]:
                        if modifier.get("type") == target_data_type:
                            key = modifier.get("modifier", "")
                            val = modifier.get("value", 0)
                            scaling = modifier.get("scaling", "flat")
                            scaling_x = float(modifier.get("scaling_x") or 1)
                            scaling_extra = modifier.get("scaling_extra") or ""
                            if scaling and scaling != "flat" and target is not None:
                                val = val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                            collected_modifiers.append({key: val})

                elif field_type == "array" and req_field == "modifiers":
                    _sd = json_data.get("scope_definitions", {})
                    from calculations.source_adapters import _resolve_modifier_type as _rmt, _is_terrain_rule as _itr
                    for modifier in object[req_field]:
                        if _itr(modifier):
                            continue  # terrain rules handled separately
                        scope = modifier.get("scope", "")
                        if scope:
                            if _sd.get(scope, {}).get("target_type", "") != target_data_type:
                                continue
                            field = _rmt(modifier)
                            val = modifier.get("value", 0)
                            if field and val:
                                scaling = modifier.get("scaling", "flat")
                                scaling_x = float(modifier.get("scaling_x") or 1)
                                scaling_extra = modifier.get("scaling_extra") or ""
                                if scaling and scaling != "flat" and target is not None:
                                    val = val * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
                                collected_modifiers.append({field: val})

                elif field_type == "enum" and req_field_schema.get("laws"):
                    law_modifiers = req_field_schema["laws"].get(object[req_field], {})
                    for key, value in law_modifiers.items():
                        # Check for custom prefix first
                        found_modifier = False
                        if modifier_prefix and key.startswith(f"{modifier_prefix}_{target_data_type}_"):
                            modifier = key.replace(f"{modifier_prefix}_{target_data_type}_", "")
                            found_modifier = True
                        # Fall back to standard target_data_type prefix
                        elif key.startswith(f"{target_data_type}_"):
                            modifier = key.replace(f"{target_data_type}_", "")
                            found_modifier = True
                        if found_modifier:
                            if "_per_market_tier" in modifier:
                                if linked_object_schema.get("properties", {}).get("tier", {}).get("laws", {}).get(object.get("tier", "I"), {}).get("tier_multiplier", 1) > 0:
                                    value *= linked_object_schema.get("properties", {}).get("tier", {}).get("laws", {}).get(object.get("tier", "I"), {}).get("tier_multiplier", 1)
                                    modifier = modifier.replace("_per_market_tier", "")
                                elif "market" in linked_object_schema.get("properties", {}):
                                    market = mongo.db["markets"].find_one({"_id": ObjectId(object["market"])})
                                    if market:
                                        value *= category_data["markets"]["schema"]["properties"].get("tier", {}).get("laws", {}).get(market.get("tier", "I"), {}).get("tier_multiplier", 1)
                                        modifier = modifier.replace("_per_market_tier", "")
                            if "_per_member" in modifier:
                                member_count = mongo.db["market_links"].count_documents({"market": str(object["_id"])})
                                value *= member_count
                                modifier = modifier.replace("_per_member", "")
                            collected_modifiers.append({modifier: value})
                
                elif field_type == "array" and req_field == "modifiers":
                    for modifier in object[req_field]:
                        # Check for custom prefix first
                        if modifier_prefix and modifier.get("field").startswith(f"{modifier_prefix}_{target_data_type}_"):
                            field = modifier["field"].replace(f"{modifier_prefix}_{target_data_type}_", "")
                            collected_modifiers.append({field: modifier["value"]})
                        # Fall back to standard target_data_type prefix
                        if modifier.get("field").startswith(target_data_type + "_"):
                            field = modifier["field"].replace(target_data_type + "_", "")
                            collected_modifiers.append({field: modifier["value"]})
                
                elif field_type == "array" and (req_field == "positive_titles" or req_field == "negative_titles"):
                    calculated_title_modifiers = calculate_title_modifiers(object[req_field], target_data_type, linked_object_schema["properties"])
                    for key, value in calculated_title_modifiers.items():
                        #calculate_title_modifers already filters based on the prefix
                        collected_modifiers.append({key: value})
                
                elif field_type == "json_resource_enum" and req_field == "node":
                    collected_modifiers.append({object[req_field] + "_nodes": 1})
    
    for field in fields_as_modifiers:
        full_modifier = modifier_prefix + "_" + field
        if full_modifier in object:
            collected_modifiers.append({field: object[full_modifier]})

    return collected_modifiers

def calculate_effective_territory_modifiers(target, schema_properties):
    effective_territory = int(target.get("effective_territory", 0))
    current_territory = int(target.get("current_territory", 0))

    over_capacity = current_territory - effective_territory

    modifiers = {}

    gov_type = target.get("government_type", "Unknown")
    nomadic = schema_properties.get("government_type", {}).get("laws", {}).get(gov_type, {}).get("nomadic", 0)

    if nomadic > 0:
        return modifiers

    if over_capacity >= 30:
        modifiers["karma"] = -8
        modifiers["stability_loss_chance"] = 1.0
        modifiers["land_attack"]   = -3
        modifiers["land_defense"]  = -3
        modifiers["naval_attack"]  = -3
        modifiers["naval_defense"] = -3
    elif over_capacity >= 20:
        modifiers["karma"] = -6
        modifiers["stability_loss_chance"] = 0.5
        modifiers["land_attack"]   = -2
        modifiers["land_defense"]  = -2
        modifiers["naval_attack"]  = -2
        modifiers["naval_defense"] = -2
    elif over_capacity >= 10:
        modifiers["karma"] = -4
        modifiers["stability_loss_chance"] = 0.3
        modifiers["land_attack"]   = -1
        modifiers["land_defense"]  = -1
        modifiers["naval_attack"]  = -1
        modifiers["naval_defense"] = -1
    elif over_capacity >= 5:
        modifiers["karma"] = -2
        modifiers["stability_loss_chance"] = 0.2
        modifiers["land_attack"]   = -1
        modifiers["land_defense"]  = -1
        modifiers["naval_attack"]  = -1
        modifiers["naval_defense"] = -1
    elif over_capacity > 0:
        modifiers["karma"] = -2
        modifiers["stability_loss_chance"] = 0.1

    return modifiers

def calculate_route_capacity_modifiers(target):
    road_capacity = int(target.get("route_capacity", 0) or target.get("road_capacity", 0))
    current_territory = int(target.get("road_usage", 0))

    over_capacity = current_territory - road_capacity

    modifiers = {}

    if over_capacity >= 20:
        scale = 1 + (over_capacity - 20) // 10
        modifiers["wood_consumption"]  = scale
        modifiers["stone_consumption"] = scale
        modifiers["mount_consumption"] = scale
    elif over_capacity >= 10:
        modifiers["wood_consumption"]  = 1
        modifiers["stone_consumption"] = 1
    elif over_capacity > 0:
        modifiers["wood_consumption"]  = 1
    
    return modifiers

def calculate_effective_pop_capacity_modifiers(target):
    effective_pop_capacity = int(target.get("effective_pop_capacity", 0))
    pop_count = int(target.get("pop_count", 0))

    over_capacity = pop_count - effective_pop_capacity

    modifiers = {}

    if over_capacity >= 6:
        modifiers["karma"] = -8
        modifiers["stability_loss_chance"] = 1
        modifiers["food_consumption_per_pop"] = 1
    elif over_capacity == 5:
        modifiers["karma"] = -8
        modifiers["stability_loss_chance"] = 0.5
        modifiers["food_consumption_per_pop"] = 1
    elif over_capacity == 4:
        modifiers["karma"] = -6
        modifiers["stability_loss_chance"] = 0.4
        modifiers["food_consumption_per_pop"] = 0.333333333333334
    elif over_capacity == 3:
        modifiers["karma"] = -4
        modifiers["stability_loss_chance"] = 0.3
        modifiers["food_consumption_per_pop"] = 0.25
    elif over_capacity == 2:
        modifiers["karma"] = -2
        modifiers["stability_loss_chance"] = 0.25
    elif over_capacity == 1:
        modifiers["karma"] = -2
        modifiers["stability_loss_chance"] = 0.1
    
    return modifiers

_ALL_CHAR_STATS = ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]

def sum_modifier_totals(modifiers, target=None):
    totals = {}
    modifier_types_data = json_data.get("modifier_types", {})
    for m in modifiers:
        modifier_type = m.get("modifier_type", "")
        expand_all_attrs = False
        if modifier_type and modifier_type in modifier_types_data:
            type_def = modifier_types_data[modifier_type]
            if type_def.get("is_terrain_rule"):
                continue  # terrain rules are handled separately in collect_territory_terrain
            field = type_def.get("field_template", modifier_type)
            for extra_field in type_def.get("extra_fields", []):
                key = extra_field["key"]
                val = m.get(key) or ""
                if extra_field.get("source") == "attributes" and val == "attribute":
                    expand_all_attrs = True
                field = field.replace(f"{{{key}}}", val)
        else:
            # Backwards compat: old format stored with "field" or "key"
            field = m.get("field", m.get("key", modifier_type))
        value = m.get("value", 0)

        # Conditional modifier: skip if condition is not met
        condition_scaling = m.get("condition_scaling") or ""
        if condition_scaling and target is not None:
            try:
                cond_x     = float(m.get("condition_scaling_x") or 1)
                cond_extra = m.get("condition_scaling_extra") or ""
                cond_op    = m.get("condition_operator") or ">="
                cond_val   = float(m.get("condition_value") or 0)
                actual     = get_scaling_multiplier(condition_scaling, target, scaling_x=cond_x, scaling_extra=cond_extra)
                met = (
                    (cond_op == ">="  and actual >= cond_val) or
                    (cond_op == ">"   and actual >  cond_val) or
                    (cond_op == "<="  and actual <= cond_val) or
                    (cond_op == "<"   and actual <  cond_val) or
                    (cond_op == "=="  and actual == cond_val)
                )
                if not met:
                    continue
            except Exception:
                continue

        scaling = m.get("scaling", "flat")
        scaling_x = float(m.get("scaling_x") or 1)
        scaling_extra = m.get("scaling_extra") or ""
        if scaling and scaling != "flat" and target is not None:
            value = value * get_scaling_multiplier(scaling, target, scaling_x=scaling_x, scaling_extra=scaling_extra)
        max_value = m.get("max_value")
        if max_value is not None:
            try:
                value = min(value, float(max_value))
            except (TypeError, ValueError):
                pass
        if expand_all_attrs:
            for stat in _ALL_CHAR_STATS:
                expanded = field.replace("attribute", stat)
                if expanded:
                    totals[expanded] = totals.get(expanded, 0) + value
        elif field:
            totals[field] = totals.get(field, 0) + value
    return totals

def sum_law_totals(laws):
    totals = {}
    for law in laws:
        for key, value in law.items():
            if key.startswith('_'):
                continue
            totals[key] = totals.get(key, 0) + value
    return totals

def sum_district_totals(districts):
    totals = {}
    for d in districts:
        for key, value in d.items():
            totals[key] = totals.get(key, 0) + value
    return totals

def sum_city_totals(cities):
    totals = {}
    for d in cities:
        for key, value in d.items():
            totals[key] = totals.get(key, 0) + value
    return totals

def sum_job_totals(target, jobs_assigned, job_details):
    if not jobs_assigned:
        return {}

    totals = {}
    original_job_details = json_data["jobs"]
    general_resources = json_data["general_resources"]
    general_resources = [resource["key"] for resource in general_resources]
    unique_resources = json_data["unique_resources"]
    unique_resources = [resource["key"] for resource in unique_resources]

    for job, count in jobs_assigned.items():
        original_job_production = original_job_details.get(job, {}).get("production", {})
        original_job_upkeep = original_job_details.get(job, {}).get("upkeep", {})
        for field, val in job_details.get(job, {}).get("production", {}).items():
            round_result = False
            if field == "money":
                field = "money_income"
                round_result = True
            elif field in general_resources or field in unique_resources:
                field = field + "_production"
                round_result = True

            if job == "astronomer":
                pop_count = compute_pop_count("pop_count", target, 0, {}, {})
                if count == pop_count:
                    count += 6
                elif count > pop_count / 2:
                    count += 3
            
            total_value = val * count
            
            if round_result:
                if val > original_job_production.get(field, 0):
                    total_value = int(math.floor(total_value))
                elif val < original_job_production.get(field, 0):
                    total_value = int(math.ceil(total_value))
                else:
                    total_value = int(round(total_value))
            
            totals[field] = totals.get(field, 0) + total_value

        for field, val in job_details.get(job, {}).get("upkeep", {}).items():
            if field == "money":
                field = "money_income"
                val = -val
            elif field in general_resources or field in unique_resources:
                field = field + "_consumption"
            else:
                val = -val
            
            total_value = val * count
            
            if val > original_job_upkeep.get(field, 0):
                total_value = int(math.floor(total_value))
            elif val < original_job_upkeep.get(field, 0):
                total_value = int(math.ceil(total_value))
            else:
                total_value = int(round(total_value))

            totals[field] = totals.get(field, 0) + total_value
        
    return totals

def sum_all_unit_totals(land_units_assigned, land_unit_details, naval_units_assigned, naval_unit_details, support_units_assigned, support_unit_details):
    totals = {}
    for unit_totals in [
        sum_unit_totals(land_units_assigned, land_unit_details),
        sum_unit_totals(naval_units_assigned, naval_unit_details),
        sum_unit_totals(support_units_assigned, support_unit_details),
    ]:
        for key, value in unit_totals.items():
            totals[key] = totals.get(key, 0) + value
    return totals

def sum_unit_totals(units_assigned, unit_details):
    if not units_assigned:
        return {}

    original_unit_details = load_db_units()

    totals = {}
    general_resources = json_data["general_resources"]
    general_resources = [resource["key"] for resource in general_resources]
    unique_resources = json_data["unique_resources"]
    unique_resources = [resource["key"] for resource in unique_resources]

    for unit, count in units_assigned.items():
        original_unit_upkeep = original_unit_details.get(unit, {}).get("upkeep", {})
        for field, val in unit_details.get(unit, {}).get("upkeep", {}).items():
            if field == "money":
                field = "money_income"
                val = -val
            elif field == "prestige":
                field = "prestige_gain"
                val = -val
            elif field in general_resources or field in unique_resources:
                field = field + "_consumption"
            else:
                val = -val
            
            total_value = val * count
            
            if val > original_unit_upkeep.get(field, 0):
                total_value = int(math.floor(total_value))
            elif val < original_unit_upkeep.get(field, 0):
                total_value = int(math.ceil(total_value))
            else:
                total_value = int(round(total_value))
            
            totals[field] = totals.get(field, 0) + total_value
    
    return totals

def sum_external_modifier_totals(external_modifiers):
    totals = {}
    for modifier in external_modifiers:
        for field, val in modifier.items():
            totals[field] = totals.get(field, 0) + val
    return totals

def calculate_karma_from_negative_stockpiles(target, modifier_totals):
    if target.get("temperament", "None") != "Player": #Only apply to player nations
        return
    for resource in json_data["general_resources"]:
        if target.get("resource_storage", {}).get(resource["key"], 0) < 0 and resource["key"] != "research":
            modifier_totals["karma"] = modifier_totals.get("karma", 0) + target.get("resource_storage", {}).get(resource["key"], 0)
    for resource in json_data["unique_resources"]:
        if target.get("resource_storage", {}).get(resource["key"], 0) < 0:
            modifier_totals["karma"] = modifier_totals.get("karma", 0) + target.get("resource_storage", {}).get(resource["key"], 0)

def parse_meta_modifiers(target, overall_total_modifiers):
    meta_mods = json_data["meta_mods"]
    for mod_key, mod_details in meta_mods.items():
        if overall_total_modifiers.get(mod_key, 0) > 0:
            for field, value in mod_details.get("modifiers", {}).items():
                overall_total_modifiers[field] = overall_total_modifiers.get(field, 0) + value
    return overall_total_modifiers


_TRIBUTE_RESOURCES = ["food", "wood", "stone"]


def _calc_tribute(pop_count, vassal_type, overall_total_modifiers):
    """Return the per-resource tribute amount for a vassal.

    Base: max(1, pops // 10).  Tributary doubles base and minimum.
    Modifiers: vassal_tribute_flat (added before multiplier),
               vassal_tribute_multiplier (fractional bonus, e.g. 0.5 = +50%).
    """
    base = max(1, pop_count // 10)
    if vassal_type == "Tributary":
        amount = max(2, base * 2)
    else:
        amount = base
    flat   = overall_total_modifiers.get("vassal_tribute_flat", 0)
    mult   = 1 + overall_total_modifiers.get("vassal_tribute_multiplier", 0)
    return max(0, int(round((amount + flat) * mult)))


def _apply_vassal_tribute_modifiers(target, overall_total_modifiers):
    """Inject vassal tribute into overall_total_modifiers before production/consumption
    fields are computed.

    Vassals:  food/wood/stone consumption += tribute amount.
    Overlords: food/wood/stone production += sum of all vassals' tributes.
    """
    # ── Vassal side (this nation pays tribute) ────────────────────────────
    overlord_id = str(target.get("overlord") or "")
    if overlord_id:
        pop_count  = target.get("pop_count", 0)
        vassal_type = target.get("vassal_type", "None")
        tribute = _calc_tribute(pop_count, vassal_type, overall_total_modifiers)
        for resource in _TRIBUTE_RESOURCES:
            overall_total_modifiers[f"{resource}_consumption"] = (
                overall_total_modifiers.get(f"{resource}_consumption", 0) + tribute
            )

    # ── Overlord side (this nation receives tribute from vassals) ─────────
    if "_id" not in target:
        return
    nation_id_str = str(target["_id"])
    try:
        vassals = list(mongo.db.nations.find(
            {"overlord": nation_id_str},
            {"pop_count": 1, "vassal_type": 1, "modifiers": 1},
        ))
    except Exception:
        return
    for vassal in vassals:
        v_pop   = vassal.get("pop_count", 0)
        v_type  = vassal.get("vassal_type", "None")
        # Use an empty modifiers dict for the vassal's tribute modifiers since we
        # don't re-run the full calculation here; the per-resource formula is the
        # canonical source of truth and additional scaling can be added via
        # overlord_nation_* modifiers on the vassal_type law.
        v_tribute = _calc_tribute(v_pop, v_type, {})
        for resource in _TRIBUTE_RESOURCES:
            overall_total_modifiers[f"{resource}_production"] = (
                overall_total_modifiers.get(f"{resource}_production", 0) + v_tribute
            )



def _build_computed_contributions(
    target, schema_properties, overall_totals, calculated_values,
    tagged_sources, component_sources,
):
    """Build SourceContribution objects for dynamic computed values not in raw modifiers.

    These cover terrain production, node production, jobs, naval bonuses, stability
    extras (karma/pop/territory), food/research consumption, and money income per-pop.
    """
    import math as _math
    from calculations.source_contribution import SourceContribution

    contribs = []
    pop_count = int(calculated_values.get("pop_count", target.get("pop_count", 0)) or 0)
    all_res = json_data.get("general_resources", []) + json_data.get("unique_resources", [])

    # ── Terrain production ────────────────────────────────────────────────────
    terrain_json = json_data.get("terrains", {})
    terrain_rules = _collect_all_terrain_rules(target)
    _eff_tt = (target.get("_calc_cache") or {}).get("effective_territory_types") or target.get("territory_types", {})
    for terrain, tile_count in _eff_tt.items():
        td = terrain_json.get(terrain, {})
        rules = [r for r in terrain_rules if r.get("terrain") == terrain]

        res_key = td.get("resource", "none")
        for r in rules:
            if r["rule_type"] == "swap_resource" and r.get("resource"):
                res_key = r["resource"]
        if res_key == "none":
            continue

        count_req = td.get("count_required", 4)
        for r in rules:
            if r["rule_type"] == "count_required_delta":
                count_req += int(r.get("value", 0))
        count_req = max(count_req, 1)

        def _apply_mult(res, base):
            m = 1.0
            for r in rules:
                if r["rule_type"] != "multiplier":
                    continue
                sr = r.get("resource")
                if sr is None or sr == res:
                    m *= float(r.get("value", 1.0))
            return int(base * m)

        prod = _apply_mult(res_key, tile_count // count_req)
        if prod:
            name = td.get("display_name", terrain.replace("_", " ").title())
            contribs.append(SourceContribution(
                label=f"Terrain: {name} ({tile_count} tiles)",
                source_type="terrain",
                modifiers={res_key + "_production": prod},
            ))

        for r in rules:
            if r["rule_type"] != "extra_resource":
                continue
            extra_res = r.get("resource")
            cr = int(r.get("value", 1))
            if not extra_res or cr < 1:
                continue
            extra_prod = _apply_mult(extra_res, tile_count // cr)
            if extra_prod:
                name = td.get("display_name", terrain.replace("_", " ").title())
                contribs.append(SourceContribution(
                    label=f"Terrain: {name} (extra {extra_res})",
                    source_type="terrain",
                    modifiers={extra_res + "_production": extra_prod},
                ))

    # ── Node production ───────────────────────────────────────────────────────
    for resource in all_res:
        key = resource["key"]
        nodes = overall_totals.get(key + "_nodes", 0)
        if nodes:
            nv = 2 + overall_totals.get("resource_node_value", 0) + overall_totals.get(key + "_node_value", 0)
            contribs.append(SourceContribution(
                label="Nodes", source_type="node",
                modifiers={key + "_production": nodes * nv},
            ))

    # ── Job production / upkeep ───────────────────────────────────────────────
    job_details = calculated_values.get("job_details", {})
    jobs = target.get("jobs") or {}
    for job_key, count in (jobs.items() if isinstance(jobs, dict) else []):
        if not count or job_key not in job_details:
            continue
        mods = {}
        for res, v in job_details[job_key].get("production", {}).items():
            if v:
                mods[res + "_production"] = int(v * count)
        for res, v in job_details[job_key].get("upkeep", {}).items():
            if v:
                mods[res + "_consumption"] = int(v * count)
        if mods:
            label = job_details[job_key].get("display_name", job_key.replace("_", " ").title())
            contribs.append(SourceContribution(label=label, source_type="job", modifiers=mods))

    # ── Job money income (not in resource production/upkeep) ──────────────────
    job_money = component_sources.get("jobs", {}).get("money_income", 0)
    if job_money:
        contribs.append(SourceContribution(
            label="Jobs", source_type="job", modifiers={"money_income": job_money},
        ))

    # ── Naval unit resource bonuses ───────────────────────────────────────────
    naval_count = target.get("naval_unit_count", calculated_values.get("naval_unit_count", 0))
    if naval_count:
        for resource in all_res:
            key = resource["key"]
            pn = overall_totals.get(key + "_production_per_naval_unit", 0)
            if pn:
                contribs.append(SourceContribution(
                    label="Naval Units", source_type="unit",
                    modifiers={key + "_production": int(_math.floor(naval_count * pn))},
                ))

    # ── Stability gain extras ─────────────────────────────────────────────────
    karma       = target.get("karma", 0)
    road_usage  = int(target.get("road_usage", 0))
    unique_min  = target.get("unique_minority_count", 0)
    min_impact  = 1 + overall_totals.get("minority_impact", 0)
    total_prod  = sum(calculated_values.get("resource_production", {}).values())
    territory   = target.get("territory_types", {})

    kg = max(min(karma * overall_totals.get("stability_gain_chance_per_positive_karma", 0),
                 overall_totals.get("max_stability_gain_chance_per_positive_karma", 0)), 0)
    if kg:
        contribs.append(SourceContribution(label="Karma", source_type="computed",
                                           modifiers={"stability_gain_chance": kg}))
    mg = max(min(unique_min * min_impact * overall_totals.get("stability_gain_chance_per_unique_minority", 0),
                 overall_totals.get("max_stability_gain_chance_per_unique_minority", 0)), 0)
    if mg:
        contribs.append(SourceContribution(label="Minorities", source_type="computed",
                                           modifiers={"stability_gain_chance": mg}))
    pg = pop_count * overall_totals.get("stability_gain_chance_per_pop", 0)
    if pg:
        contribs.append(SourceContribution(label="Population", source_type="computed",
                                           modifiers={"stability_gain_chance": pg}))
    rg = road_usage * overall_totals.get("stability_gain_chance_per_road_usage", 0)
    if rg:
        contribs.append(SourceContribution(label="Road Usage", source_type="computed",
                                           modifiers={"stability_gain_chance": rg}))
    prodg = overall_totals.get("stability_gain_chance_per_resource_production", 0) * total_prod
    if prodg:
        contribs.append(SourceContribution(label="Production", source_type="computed",
                                           modifiers={"stability_gain_chance": prodg}))
    tg = overall_totals.get("stability_gain_chance_per_tile", 0) * sum(territory.values())
    for t, c in territory.items():
        tg += overall_totals.get("stability_gain_chance_per_" + t, 0) * c
    if tg:
        contribs.append(SourceContribution(label="Territory", source_type="computed",
                                           modifiers={"stability_gain_chance": tg}))

    # ── Stability loss extras ─────────────────────────────────────────────────
    stability = target.get("stability", "")
    kl = max(min(-karma * overall_totals.get("stability_loss_chance_per_negative_karma", 0),
                 overall_totals.get("max_stability_loss_chance_per_negative_karma", 0)), 0)
    if kl:
        contribs.append(SourceContribution(label="Karma", source_type="computed",
                                           modifiers={"stability_loss_chance": kl}))
    ml = max(min(unique_min * min_impact * overall_totals.get("stability_loss_chance_per_unique_minority", 0),
                 overall_totals.get("max_stability_loss_chance_per_unique_minority", 0)), 0)
    if ml:
        contribs.append(SourceContribution(label="Minorities", source_type="computed",
                                           modifiers={"stability_loss_chance": ml}))
    pl = pop_count * overall_totals.get("stability_loss_chance_per_pop", 0)
    if pl:
        contribs.append(SourceContribution(label="Population", source_type="computed",
                                           modifiers={"stability_loss_chance": pl}))
    rl = road_usage * overall_totals.get("stability_loss_chance_per_road_usage", 0)
    if rl:
        contribs.append(SourceContribution(label="Road Usage", source_type="computed",
                                           modifiers={"stability_loss_chance": rl}))
    sl = (overall_totals.get("stability_loss_chance_at_united", 0) if stability == "United"
          else overall_totals.get("stability_loss_chance_at_stable", 0) if stability == "Stable"
          else 0)
    if sl:
        contribs.append(SourceContribution(label="Stability Level", source_type="computed",
                                           modifiers={"stability_loss_chance": sl}))
    tl = sum(overall_totals.get("stability_loss_chance_per_" + t, 0) * c
             for t, c in territory.items())
    if tl:
        contribs.append(SourceContribution(label="Territory", source_type="computed",
                                           modifiers={"stability_loss_chance": tl}))

    # ── Food / research special consumption ───────────────────────────────────
    fpc = 1 + overall_totals.get("food_consumption_per_pop", 0)
    food_from_pop = (_math.ceil(pop_count * fpc) if fpc < 1
                     else _math.floor(pop_count * fpc))
    if food_from_pop:
        contribs.append(SourceContribution(label="Population", source_type="computed",
                                           modifiers={"food_consumption": food_from_pop}))

    tech_invest = sum(d.get("investing", 0) for d in _normalize_technologies(target.get("technologies")).values()
                      if isinstance(d, dict))
    if tech_invest:
        contribs.append(SourceContribution(label="Tech Investment", source_type="computed",
                                           modifiers={"research_consumption": tech_invest}))

    # ── Special resource production ───────────────────────────────────────────
    hom = overall_totals.get("research_production_if_religously_homogeneous", 0)
    if hom:
        contribs.append(SourceContribution(label="Religious Homogeneity", source_type="computed",
                                           modifiers={"research_production": hom}))

    food_stock = target.get("resource_storage", {}).get("food", 0)
    per_st     = overall_totals.get("food_production_per_stockpiled_food", 0)
    max_st     = overall_totals.get("max_food_production_per_stockpiled_food", 0)
    if per_st > 0:
        mill = min(food_stock // per_st, max_st)
        if mill:
            contribs.append(SourceContribution(label="Mill (Stockpile)", source_type="computed",
                                               modifiers={"food_production": mill}))

    # ── Money income computed: per-pop and stockpile ──────────────────────────
    if tagged_sources:
        for source in tagged_sources:
            per_pop_money = source["modifiers"].get("money_income_per_pop", 0)
            if per_pop_money and pop_count:
                contribs.append(SourceContribution(
                    label=source["label"], source_type="computed",
                    modifiers={"money_income": round(per_pop_money * pop_count, 4)},
                ))
    else:
        total_per_pop_money = overall_totals.get("money_income_per_pop", 0)
        if total_per_pop_money and pop_count:
            contribs.append(SourceContribution(
                label="Per Pop", source_type="computed",
                modifiers={"money_income": total_per_pop_money * pop_count},
            ))

    stockpile    = target.get("money", 0)
    per_storage  = overall_totals.get("money_income_per_money_storage", 0)
    max_storage  = overall_totals.get("max_money_income_per_money_storage", 0)
    if per_storage > 0:
        storage_income = min((stockpile // per_storage) * 100, max_storage)
        if storage_income:
            contribs.append(SourceContribution(label="Stockpile", source_type="computed",
                                               modifiers={"money_income": storage_income}))

    # ── Consumption conversions ───────────────────────────────────────────────
    # e.g. food_to_magic_consumption_conversion: 0.5 means 50% of food consumption
    # becomes magic consumption instead.  Show this as explicit +/- entries so the
    # final tooltip totals match the computed values.
    all_res_keys = [r["key"] for r in all_res]
    res_display  = {r["key"]: r.get("name", r["key"]) for r in all_res}
    for r_from in all_res_keys:
        for r_to in all_res_keys:
            if r_from == r_to:
                continue
            ratio = overall_totals.get(f"{r_from}_to_{r_to}_consumption_conversion", 0)
            if not ratio:
                continue
            # Pre-conversion consumption of r_from: sum contributions so far
            pre = max(sum(
                c.modifiers.get(r_from + "_consumption", 0)
                + c.modifiers.get("resource_consumption", 0)
                for c in contribs
            ), 0)
            if not pre:
                continue
            converted = pre * ratio
            contribs.append(SourceContribution(
                label=f"Conversion to {res_display.get(r_to, r_to)}",
                source_type="computed",
                modifiers={r_from + "_consumption": -pre},
            ))
            contribs.append(SourceContribution(
                label=f"Conversion from {res_display.get(r_from, r_from)}",
                source_type="computed",
                modifiers={r_to + "_consumption": converted},
            ))

    # ── Negative stockpile karma ──────────────────────────────────────────────
    if target.get("temperament", "None") == "Player":
        storage = target.get("resource_storage") or {}
        for resource in json_data.get("general_resources", []):
            rkey = resource["key"]
            if rkey == "research":
                continue
            v = storage.get(rkey, 0)
            if v < 0:
                contribs.append(SourceContribution(
                    label=f"Negative {resource.get('display_name', rkey)} Stockpile",
                    source_type="computed",
                    modifiers={"karma": v},
                ))
        for resource in json_data.get("unique_resources", []):
            rkey = resource["key"]
            v = storage.get(rkey, 0)
            if v < 0:
                contribs.append(SourceContribution(
                    label=f"Negative {resource.get('display_name', rkey)} Stockpile",
                    source_type="computed",
                    modifiers={"karma": v},
                ))

    # ── Over-capacity penalties (population & territory) ─────────────────────
    pop_cap_mods = calculate_effective_pop_capacity_modifiers(target)
    if pop_cap_mods.get("karma"):
        eff_cap = int(calculated_values.get("effective_pop_capacity", target.get("effective_pop_capacity", 0)))
        over = int(calculated_values.get("pop_count", target.get("pop_count", 0))) - eff_cap
        contribs.append(SourceContribution(
            label=f"Over Population Capacity ({over} over)",
            source_type="computed",
            modifiers={k: v for k, v in pop_cap_mods.items() if k == "karma"},
        ))

    terr_mods = calculate_effective_territory_modifiers(target, schema_properties)
    if terr_mods.get("karma"):
        eff_terr = int(calculated_values.get("effective_territory", target.get("effective_territory", 0)))
        curr_terr = int(calculated_values.get("current_territory", target.get("current_territory", 0)))
        over = curr_terr - eff_terr
        contribs.append(SourceContribution(
            label=f"Over Territory Capacity ({over} over)",
            source_type="computed",
            modifiers={k: v for k, v in terr_mods.items() if k == "karma"},
        ))

    # ── Vassal tribute ────────────────────────────────────────────────────────
    nation_id_str = str(target.get("_id", ""))
    if nation_id_str:
        try:
            vassals = list(mongo.db.nations.find(
                {"overlord": nation_id_str},
                {"name": 1, "pop_count": 1, "vassal_type": 1},
            ))
        except Exception:
            vassals = []
        for vassal in vassals:
            v_name    = vassal.get("name", "Unknown Vassal")
            v_pop     = vassal.get("pop_count", 0)
            v_type    = vassal.get("vassal_type", "None")
            v_tribute = _calc_tribute(v_pop, v_type, {})
            if v_tribute:
                contribs.append(SourceContribution(
                    label=f"Vassal: {v_name}",
                    source_type="computed",
                    modifiers={res + "_production": v_tribute for res in _TRIBUTE_RESOURCES},
                ))

    overlord_id = str(target.get("overlord") or "")
    if overlord_id:
        v_pop_count   = target.get("pop_count", 0)
        v_vassal_type = target.get("vassal_type", "None")
        tribute = _calc_tribute(v_pop_count, v_vassal_type, overall_totals)
        if tribute:
            try:
                overlord = mongo.db.nations.find_one({"_id": ObjectId(overlord_id)}, {"name": 1})
                overlord_name = overlord.get("name", "Overlord") if overlord else "Overlord"
            except Exception:
                overlord_name = "Overlord"
            contribs.append(SourceContribution(
                label=f"Tribute to {overlord_name}",
                source_type="computed",
                modifiers={res + "_consumption": tribute for res in _TRIBUTE_RESOURCES},
            ))

    return contribs


def compute_nation_breakdowns(
    target, schema_properties, component_sources, overall_total_modifiers,
    calculated_values, tagged_sources=None,
):
    """Build tooltip-friendly breakdowns for a nation's key fields.

    Uses SourceContribution objects and build_field_breakdown so that every
    breakdown is derived from the same per-source pipeline used by the rest of
    the calculation system.  Custom modifiers using the new modifier_type format
    are fully resolved before being added to contributions.
    """
    from calculations.source_contribution import SourceContribution
    from calculations.source_adapters import build_field_breakdown

    # Convert tagged_sources → SourceContribution list (tagged_sources always
    # present for nations; component_sources fallback kept for completeness).
    if tagged_sources is not None:
        contributions = [
            SourceContribution(
                label=s["label"],
                source_type=s.get("source_type", "source"),
                modifiers=dict(s["modifiers"]),
            )
            for s in tagged_sources
        ]
    else:
        contributions = [
            SourceContribution(label=lbl.title(), source_type="component", modifiers=dict(mods))
            for lbl, mods in (component_sources or {}).items()
            if mods
        ]

    # Add computed contributions (terrain, nodes, jobs, stability extras, etc.)
    contributions.extend(
        _build_computed_contributions(
            target, schema_properties, overall_total_modifiers,
            calculated_values, tagged_sources, component_sources or {},
        )
    )

    # ── Generic single-field breakdown ────────────────────────────────────────
    def _field_bd(field, *, pct=False):
        mult = 100 if pct else 1
        entries = []
        base = schema_properties.get(field, {}).get("base_value")
        if base:
            entries.append({"label": "Base", "value": base * mult})
        for entry in build_field_breakdown(field, contributions):
            v = round(entry["value"] * mult, 4)
            if v:
                entries.append({"label": entry["label"], "value": v})
        total = calculated_values.get(field)
        if total is not None:
            entries.append({"label": "Total", "value": round(total * mult, 2) if pct else total})
        return entries

    # ── Per-resource breakdown (handles specific + generic keys) ──────────────
    def _resource_bd(resource_key, direction):
        specific = resource_key + "_" + direction
        generic  = "resource_" + direction
        entries  = []
        for c in contributions:
            v = c.modifiers.get(specific, 0) + c.modifiers.get(generic, 0)
            if v:
                entries.append({"label": c.label, "value": v})
        if direction == "production":
            if overall_total_modifiers.get("locks_" + resource_key + "_production", 0) > 0:
                sub = sum(e["value"] for e in entries)
                if sub:
                    entries.append({"label": "Production Locked", "value": -sub})
        total = calculated_values.get("resource_" + direction, {}).get(resource_key, 0)
        entries.append({"label": "Total", "value": total})
        return entries

    # ── Build all breakdowns ──────────────────────────────────────────────────
    # Schema-driven: any calculated field with "show_breakdown": true gets a
    # generic _field_bd entry automatically. Special-case blocks below overwrite
    # fields that need custom logic (admin injection, karma, money, resources).
    breakdowns = {
        "money_income": [],
        "karma": [],
        "resource_production": {},
        "resource_consumption": {},
    }
    for _f, _fschema in schema_properties.items():
        if isinstance(_fschema, dict) and _fschema.get("calculated") and _fschema.get("show_breakdown"):
            breakdowns[_f] = _field_bd(_f, pct=_fschema.get("format") == "percentage")

    # effective_territory — inject Administration contribution before Total
    eff_terr_bd = _field_bd("effective_territory")
    admin_val = target.get("administration", 0)
    et_per_admin = schema_properties.get("effective_territory", {}).get("effective_territory_per_admin", 0)
    if et_per_admin and admin_val:
        eff_terr_bd.insert(-1, {"label": "Administration", "value": et_per_admin * admin_val})
    breakdowns["effective_territory"] = eff_terr_bd

    # route_capacity — inject Administration contribution before Total
    route_cap_bd = _field_bd("route_capacity")
    rc_per_admin = schema_properties.get("route_capacity", {}).get("route_capacity_per_admin", 0)
    if rc_per_admin and admin_val:
        route_cap_bd.insert(-1, {"label": "Administration", "value": rc_per_admin * admin_val})
    breakdowns["route_capacity"] = route_cap_bd

    # Karma — strip _field_bd's premature Total, inject rolling/temporary before the real one
    karma_bd = _field_bd("karma")
    if karma_bd and karma_bd[-1].get("label") == "Total":
        karma_bd.pop()
    rolling   = target.get("rolling_karma", 0)
    temporary = target.get("temporary_karma", 0)
    if rolling:
        karma_bd.append({"label": "Rolling Karma",   "value": rolling})
    if temporary:
        karma_bd.append({"label": "Temporary Karma", "value": temporary})
    karma_bd.append({"label": "Total", "value": calculated_values.get("karma", target.get("karma", 0))})
    breakdowns["karma"] = karma_bd

    # Money income — base + contributions + total
    base_money = schema_properties.get("money_income", {}).get("base_value", 0)
    money_bd   = []
    if base_money:
        money_bd.append({"label": "Base", "value": base_money})
    for entry in build_field_breakdown("money_income", contributions):
        if entry["value"]:
            money_bd.append({"label": entry["label"], "value": entry["value"]})
    money_bd.append({"label": "Total",
                     "value": calculated_values.get("money_income", target.get("money_income", 0))})
    breakdowns["money_income"] = money_bd

    # Trade route contributions — add before resource breakdown loop so totals include them
    nation_name_bd = target.get("name", "")
    if nation_name_bd:
        from helpers.trade_route_helpers import _get_cached_routes, get_trade_route_source_contributions
        tr_routes = _get_cached_routes(target)
        if tr_routes:
            contributions.extend(get_trade_route_source_contributions(nation_name_bd, tr_routes))

    # Resources
    for resource in json_data["general_resources"] + json_data["unique_resources"] + json_data["luxury_resources"]:
        key = resource["key"]
        breakdowns["resource_production"][key]  = _resource_bd(key, "production")
        breakdowns["resource_consumption"][key] = _resource_bd(key, "consumption")

    # Per-job production/upkeep breakdowns
    base_job_defs    = json_data.get("jobs", {})
    job_details_calc = calculated_values.get("job_details", {})
    breakdowns["job_production"]  = {}
    breakdowns["job_consumption"] = {}

    for job_key, job_calc in job_details_calc.items():
        base_job = base_job_defs.get(job_key, {})

        prod_bd = {}
        for resource, per_worker in job_calc.get("production", {}).items():
            entries = []
            base_val = base_job.get("production", {}).get(resource, 0)
            if base_val:
                entries.append({"label": "Base", "value": base_val})
            for c in contributions:
                v = (c.modifiers.get(f"{job_key}_{resource}_production", 0)
                     + c.modifiers.get(f"{job_key}_resource_production", 0)
                     + c.modifiers.get(f"job_{resource}_production", 0)
                     + c.modifiers.get("job_resource_production", 0))
                if v:
                    entries.append({"label": c.label, "value": v})
            entries.append({"label": "Total", "value": round(per_worker, 4)})
            prod_bd[resource] = entries
        breakdowns["job_production"][job_key] = prod_bd

        cons_bd = {}
        for resource, per_worker in job_calc.get("upkeep", {}).items():
            entries = []
            base_val = base_job.get("upkeep", {}).get(resource, 0)
            if base_val:
                entries.append({"label": "Base", "value": base_val})
            for c in contributions:
                v = (c.modifiers.get(f"{job_key}_{resource}_upkeep", 0)
                     + c.modifiers.get(f"{job_key}_resource_upkeep", 0)
                     + c.modifiers.get(f"job_{resource}_upkeep", 0)
                     + c.modifiers.get("job_resource_upkeep", 0))
                if v:
                    entries.append({"label": c.label, "value": v})
            entries.append({"label": "Total", "value": per_worker})
            cons_bd[resource] = entries
        breakdowns["job_consumption"][job_key] = cons_bd

    return breakdowns

