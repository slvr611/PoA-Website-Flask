import math
import copy
from copy import deepcopy
from time import perf_counter
from app_core import mongo, json_data, category_data
from calculations.compute_functions import compute_pop_count, compute_field
from bson.objectid import ObjectId
from app_core import json_data

def _build_nation_calc_cache(target):
    target_id = str(target.get("_id", ""))
    if not target_id:
        return {}

    pops = list(category_data["pops"]["database"].find({"nation": target_id}, {"race": 1, "culture": 1, "religion": 1}))
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

    return {
        "pops": pops,
        "pop_count": len(pops),
        "bloodthirsty_pop_count": bloodthirsty_pop_count,
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
    record_timing("sum_external_modifiers_ms", phase_start)

    phase_start = perf_counter()
    modifiers = collect_modifiers(target)
    modifier_totals = sum_modifier_totals(modifiers)
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
        districts = collect_nation_districts(target, law_totals, district_details)
    elif target_data_type == "nation_jobs":
        district_details = calculate_district_details(target, schema_properties, modifier_totals, law_totals, external_modifiers_total)
        districts = collect_nation_districts(target, law_totals, district_details)
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

    phase_start = perf_counter()
    if target_data_type == "nation":
        cities = collect_cities(target)
        city_totals = sum_city_totals(cities)

        technologies = target.get("technologies", {})
        tech_totals = sum_tech_totals(technologies)

        loose_nodes = target.get("nodes", {})
        loose_node_totals = sum_loose_node_totals(loose_nodes, modifier_totals, external_modifiers_total, law_totals, tech_totals)

        territory_terrain_totals = collect_territory_terrain(target, modifier_totals, external_modifiers_total)

        jobs_assigned = collect_jobs_assigned(target)
        job_details = calculate_job_details(target, district_details, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)
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
        job_details = calculate_job_details(target, district_details, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)
    elif target_data_type == "character":
        positive_title_modifiers = calculate_title_modifiers(target.get("positive_titles", []), target_data_type, schema_properties)
        negative_title_modifiers = calculate_title_modifiers(target.get("negative_titles", []), target_data_type, schema_properties)
        title_modifiers = positive_title_modifiers.copy()
        for key, value in negative_title_modifiers.items():
            title_modifiers[key] = title_modifiers.get(key, 0) + value
    elif target_data_type == "market":
        primary_resource = target.get("primary_resource", "")
        secondary_resource_one = target.get("secondary_resource_one", "")
        secondary_resource_two = target.get("secondary_resource_two", "")
        law_totals[primary_resource + "_production"] = law_totals.get("primary_resource_production", 0)
        law_totals[secondary_resource_one + "_production"] = law_totals.get("secondary_resource_production", 0)
        law_totals[secondary_resource_two + "_production"] = law_totals.get("secondary_resource_production", 0)
    record_timing("target_specific_calculations_ms", phase_start)

    attributes_to_precalculate = ["administration", "effective_territory", "current_territory", "road_capacity", "effective_pop_capacity", "pop_count", "land_unit_capacity"]

    phase_start = perf_counter()
    overall_total_modifiers = {}
    calculated_values = {"district_details": district_details, "job_details": job_details, "land_unit_details": land_unit_details, "naval_unit_details": naval_unit_details, "support_unit_details": support_unit_details}
    for d in [external_modifiers_total, modifier_totals, district_totals, tech_totals, loose_node_totals, territory_terrain_totals, city_totals, law_totals, job_totals, unit_totals, prestige_modifiers, title_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
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

    road_capacity_modifiers = calculate_road_capacity_modifiers(target)

    effective_pop_capacity_modifiers = calculate_effective_pop_capacity_modifiers(target)

    for d in [effective_territory_modifiers, road_capacity_modifiers, effective_pop_capacity_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
    
    overall_total_modifiers = parse_meta_modifiers(target, overall_total_modifiers)
    record_timing("capacity_and_meta_modifiers_ms", phase_start)

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
        
        excess_food = calculated_values.get("resource_excess", {}).get("food", 0) + target.get("resource_storage", {}).get("food", 0)

        if excess_food < -food_consumption / 2:
            #Nation is Starving
            #print("Nation is Starving")
            overall_total_modifiers["strength"] = overall_total_modifiers.get("strength", 0) - 2
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + 0.25
            modifier_totals["job_resource_production"] = modifier_totals.get("job_resource_production", 0) - 1
            modifier_totals["minimum_job_resource_production"] = -1
            modifier_totals["hunter_food_production"] = modifier_totals.get("hunter_food_production", 0) + 1
            modifier_totals["farmer_food_production"] = modifier_totals.get("farmer_food_production", 0) + 1
            modifier_totals["fisherman_food_production"] = modifier_totals.get("fisherman_food_production", 0) + 1
            modifier_totals["locks_research_production"] = 1

        elif excess_food < 0:
            #Nation is Underfed
            #print("Nation is Underfed")
            overall_total_modifiers["strength"] = overall_total_modifiers.get("strength", 0) - 1
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + 0.1
            modifier_totals["job_resource_production"] = modifier_totals.get("job_resource_production", 0) - 1
            modifier_totals["minimum_job_resource_production"] = -1
            modifier_totals["hunter_food_production"] = modifier_totals.get("hunter_food_production", 0) + 1
            modifier_totals["farmer_food_production"] = modifier_totals.get("farmer_food_production", 0) + 1
            modifier_totals["fisherman_food_production"] = modifier_totals.get("fisherman_food_production", 0) + 1

        if excess_food < 0:
            #print("Nation excess food is less than 0")
            job_details = calculate_job_details(target, district_details, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)
            job_totals = sum_job_totals(target, target.get("jobs", {}), job_details)
            calculated_values["job_details"] = job_details

            overall_total_modifiers = {}

            for d in [external_modifiers_total, modifier_totals, district_totals, tech_totals, loose_node_totals, territory_terrain_totals, city_totals, law_totals, job_totals, unit_totals, prestige_modifiers, title_modifiers]:
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
    
    phase_start = perf_counter()
    if return_breakdowns and target_data_type == "nation":
        component_sources = {
            "base": {},
            "modifiers": modifier_totals,
            "districts": district_totals,
            "cities": city_totals,
            "laws": law_totals,
            "tech": tech_totals,
            "jobs": job_totals,
            "units": unit_totals,
            "terrain": territory_terrain_totals,
            "loose_nodes": loose_node_totals,
            "external": external_modifiers_total,
            "prestige": prestige_modifiers,
            "titles": title_modifiers,
        }
        breakdowns = compute_nation_breakdowns(
            target,
            schema_properties,
            component_sources,
            overall_total_modifiers,
            calculated_values,
            nation_tagged_sources,
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

def collect_modifiers(target):
    return target.get("modifiers", [])

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
    district_consumption_modifier = modifier_totals.get("district_resource_consumption", 0) + law_totals.get("district_resource_consumption", 0) + external_modifiers_total.get("district_resource_consumption", 0)
    for district_type, district_data in json_data["nation_districts"].items():
        district_data = copy.deepcopy(district_data)
        district_details[district_type] = district_data
        current_district_consumption_modifier = district_consumption_modifier + modifier_totals.get(district_type + "_resource_consumption", 0) + law_totals.get(district_type + "_resource_consumption", 0) + external_modifiers_total.get(district_type + "_resource_consumption", 0)
        for modifier, value in district_data.get("modifiers", {}).items():
            if modifier.endswith("_consumption"):
                district_data["modifiers"][modifier] = max(value + current_district_consumption_modifier, 0)
        
    for district_type, district_data in json_data["nation_imperial_districts"].items():
        district_details[district_type] = copy.deepcopy(district_data)
    return district_details

def collect_nation_districts(target, law_totals, district_details):
    nation_districts = target.get("districts", [])
    collected_modifiers = []

    def synergy_matches(node, requirement):
        if not node:
            return False
        if isinstance(requirement, list):
            return "any" in requirement or node in requirement
        if requirement == "any":
            return True
        return node == requirement
    
    for district in nation_districts:
        if isinstance(district, dict):
            district_type = district.get("type", "")
            district_node = district.get("node", "")
            district_modifiers = district_details.get(district_type, {}).get("modifiers", {})
            collected_modifiers.append(district_modifiers)
            if synergy_matches(district_node, district_details.get(district_type, {}).get("synergy_requirement", "")):
                collected_modifiers.append(district_details.get(district_type, {}).get("synergy_modifiers", {}))
                if district_details.get(district_type, {}).get("synergy_node_active", True):
                    collected_modifiers.append({district_node + "_nodes": 1})
            elif district_node != "":
                collected_modifiers.append({district_node + "_nodes": 1})

    imperial_district_json_data = json_data["nation_imperial_districts"]

    if target.get("empire", False):
        imperial_district = target.get("imperial_district", {})
        imperial_district_type = imperial_district.get("type", "")
        imperial_district_node = imperial_district.get("node", "")
        imperial_district_synergy_node = imperial_district_json_data.get(imperial_district_type, {}).get("synergy_requirement", "")
        imperial_district_synergy_node_active = imperial_district_json_data.get(imperial_district_type, {}).get("synergy_node_active", True)
        imperial_district_modifiers = imperial_district_json_data.get(imperial_district_type, {}).get("modifiers", {})
        collected_modifiers.append(imperial_district_modifiers)
        if synergy_matches(imperial_district_node, imperial_district_synergy_node):
            collected_modifiers.append(imperial_district_json_data.get(imperial_district_type, {}).get("synergy_modifiers", {}))
            if imperial_district_synergy_node_active:
                collected_modifiers.append({district_node + "_nodes": 1})
        elif imperial_district_node != "":
            collected_modifiers.append({district_node + "_nodes": 1})

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
        city_node = city.get("node", "")
        city_modifiers = city_json_data.get(city_type, {}).get("modifiers", {})
        wall_modifiers = wall_json_data.get(city.get("wall", ""), {}).get("modifiers", {})
        collected_modifiers.append(city_modifiers)
        collected_modifiers.append(wall_modifiers)
        if city_node:
            collected_modifiers.append({city_node + "_nodes": 1})

    return collected_modifiers

def sum_tech_totals(technologies):
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


def collect_territory_terrain(target, modifier_totals, external_modifier_totals):
    territory_types = target.get("territory_types", {})
    total_modifiers = {}
    terrain_json_data = json_data["terrains"]

    terrain_resource_swap_modifiers = {} # A List of modifiers in the format: {terrain}_swap_{resource}_production: 1       Anything above 1 will swap the base resource production for that tile

    extra_terrain_production_modifiers = {} # A List of modifiers in the format: {terrain}_extra_{resource}_production_per: {num_tiles}

    for modifier, value in modifier_totals.items():
        if modifier.endswith("_production_per") and "_extra_" in modifier:
            extra_terrain_production_modifiers[modifier] = value
        elif modifier.endswith("_production") and "_swap_" in modifier:
            terrain_resource_swap_modifiers[modifier] = value
    for modifier, value in external_modifier_totals.items():
        if modifier.endswith("_production_per") and "_extra_" in modifier:
            extra_terrain_production_modifiers[modifier] = value
        elif modifier.endswith("_production") and "_swap_" in modifier:
            terrain_resource_swap_modifiers[modifier] = value

    for terrain, value in territory_types.items():
        terrain_modifier = terrain_json_data.get(terrain, {}).get("resource", "none") + "_production"

        for modifier, mod_value in terrain_resource_swap_modifiers.items():
            if modifier.startswith(terrain + "_swap_"):
                if mod_value > 1:
                    resource = modifier.replace("_production", "").replace(terrain + "_swap_", "")
                    terrain_modifier = resource + "_production"

        terrain_count_required = terrain_json_data.get(terrain, {}).get("count_required", 4)
        terrain_count_required += modifier_totals.get(terrain + "_terrain_count_required", 0) + external_modifier_totals.get(terrain + "_terrain_count_required", 0)
        total_modifiers[terrain_modifier] = total_modifiers.get(terrain_modifier, 0) + value // terrain_count_required

        for modifier, mod_value in extra_terrain_production_modifiers.items():
            if modifier.startswith(terrain + "_extra_"):
                resource = modifier.replace("_production_per", "").replace(terrain + "_extra_", "")
                total_modifiers[resource + "_production"] = total_modifiers.get(resource + "_production", 0) + (value // mod_value)
        
    return total_modifiers

def collect_jobs_assigned(target):
    return target.get("jobs", {})

def calculate_job_details(target, district_details, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total):
    job_details = json_data["jobs"]
    modifier_sources = [modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total]
    general_resources = json_data["general_resources"]
    general_resources = [resource["key"] for resource in general_resources]
    unique_resources = json_data["unique_resources"]
    unique_resources = [resource["key"] for resource in unique_resources]

    district_types = []
    for district in target.get("districts", []):
        if isinstance(district, dict):
            district_types.append(district_details.get(district.get("type", ""), {}).get("type", ""))
    
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
                        
                        if job == "hunter":
                            if modifier == "hunter_food_production_from_dock_or_farm" and ("dock" in district_types or "farm" in district_types):
                                new_details.setdefault("production", {})["food"] = new_details.get("production", {}).get("food", 0) + value

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

def check_job_requirements(target, job_details, overall_total_modifiers):
    requirements = job_details.get("requirements", {})
    meets_requirements = True
    districts = []
    districts = [district.get("type", "") for district in target.get("districts", [])]
    district_types = []
    for district in districts:
        district_types.append(json_data["nation_districts"].get(district, {}))
    region = target.get("region", "")
    try:
        region_name = category_data["regions"]["database"].find_one({"_id": ObjectId(region)}, {"name": 1})["name"]
    except:
        region_name = ""

    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = False
            required_district_era = requirements.get("district_era", 0)
            for district in district_types:
                if district.get("type", "") in value and district.get("era", 0) >= required_district_era:
                    has_district = True
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
                                  modifier_prefix, base_label, field_name=""):
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
                    print(
                        f"[MODIFIER DEBUG] Unmatched enum law key: "
                        f"field_name='{field_name}', source_label='{base_label}', "
                        f"req_field='{req_field}', field_value='{field_value}', "
                        f"key='{key}', val={val}, "
                        f"target_data_type='{target_data_type}', modifier_prefix='{modifier_prefix}'"
                    )
                    continue
                final_key, final_val = _apply_special_mod_multipliers(stripped, val, obj, linked_schema)
                if final_val:
                    entry_mods[final_key] = entry_mods.get(final_key, 0) + final_val
            if entry_mods:
                field_label = req_field_schema.get("label", req_field.replace("_", " ").title())
                sub_label = f"{base_label} ({field_label}: {field_value})"
                per_field_entries.append({"label": sub_label, "modifiers": entry_mods})

        # ── modifiers array ({field, value} objects) ──────────────────────
        elif field_type == "array" and req_field == "modifiers":
            for modifier in obj[req_field]:
                mod_field = modifier.get("field", "")
                mod_val = modifier.get("value", 0)
                stripped, matched = _strip_modifier_key(mod_field, target_data_type, modifier_prefix)
                if matched and mod_val:
                    plain_mods[stripped] = plain_mods.get(stripped, 0) + mod_val
                elif not matched and mod_val:
                    print(
                        f"[MODIFIER DEBUG] Unmatched modifier in array: "
                        f"field_name='{field_name}', source_label='{base_label}', "
                        f"mod_field='{mod_field}', mod_val={mod_val}, "
                        f"target_data_type='{target_data_type}', modifier_prefix='{modifier_prefix}'"
                    )

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
        for global_mod in mongo.db["global_modifiers"].find():
            mods = {}
            for modifier in global_mod.get("external_modifiers", []):
                if modifier.get("type") == target_data_type:
                    key = modifier.get("modifier", "")
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
                            target_data_type, modifier_prefix, obj_label, field_name,
                        ))
                        # Extract from the target object
                        if tgt_obj is not None:
                            tagged.extend(_extract_labeled_from_object(
                                tgt_obj, plain_required, linked_schema,
                                target_data_type, modifier_prefix, obj_label, field_name,
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
                            target_data_type, modifier_prefix, obj_label, field_name,
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
                        target_data_type, modifier_prefix, obj_label, field_name,
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

    for district in target.get("districts", []):
        if not isinstance(district, dict):
            continue
        district_type = district.get("type", "")
        district_node = district.get("node", "")
        district_data = district_details.get(district_type, {})
        district_name = district_data.get("display_name", district_type)
        mods = district_data.get("modifiers", {})
        if mods:
            tagged.append({"label": f"District: {district_name}", "modifiers": mods})
        if _synergy_matches(district_node, district_data.get("synergy_requirement", "")):
            synergy_mods = district_data.get("synergy_modifiers", {})
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
        if _synergy_matches(imperial_node, imperial_data.get("synergy_requirement", "")):
            synergy_mods = imperial_data.get("synergy_modifiers", {})
            if synergy_mods:
                tagged.append({"label": f"District: {imperial_name} (Synergy)", "modifiers": synergy_mods})

    # Technologies
    for tech_key, tech_val in target.get("technologies", {}).items():
        if tech_val.get("researched"):
            tech_data = json_data["tech"].get(tech_key, {})
            mods = tech_data.get("modifiers", {})
            if mods:
                tech_name = tech_data.get("display_name", tech_key)
                tagged.append({"label": f"Technology: {tech_name}", "modifiers": mods})

    # Cities
    city_json = json_data["cities"]
    wall_json = json_data["walls"]
    for city in target.get("cities", []):
        city_type = city.get("type", "")
        city_data = city_json.get(city_type, {})
        city_mods = city_data.get("modifiers", {})
        if city_mods:
            city_name = city_data.get("display_name", city_type)
            tagged.append({"label": f"City: {city_name}", "modifiers": city_mods})
        wall_data = wall_json.get(city.get("wall", ""), {})
        wall_mods = wall_data.get("modifiers", {})
        if wall_mods:
            wall_name = wall_data.get("display_name", city.get("wall", ""))
            tagged.append({"label": f"City: {wall_name}", "modifiers": wall_mods})

    # Nation modifiers (each entry is its own source)
    for mod in target.get("modifiers", []):
        field = mod.get("field", "")
        value = mod.get("value", 0)
        if field and value:
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

            # Ruler modifiers (prefixed "nation_" in the character's modifier list)
            for mod in ruler.get("modifiers", []):
                field = mod.get("field", "")
                value = mod.get("value", 0)
                if field and value and field.startswith("nation_"):
                    tagged.append({
                        "label": f"Ruler: {ruler_name}",
                        "modifiers": {field[len("nation_"):]: value},
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

def _district_key_to_category(district_key):
    """Return the category type (e.g. 'forge') for a district key (e.g. 'ancient_forge')."""
    all_district_files = [
        "nation_districts", "nation_imperial_districts", "mercenary_districts",
        "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts"
    ]
    for fname in all_district_files:
        data = json_data.get(fname, {}).get(district_key)
        if data:
            return data.get("type", "")
    return ""


def check_unit_requirements(target, unit_details):
    # Imperial units require the nation to be an empire
    if "Imperial" in (unit_details.get("unit_class") or "") and not target.get("empire", False):
        return False

    requirements = unit_details.get("requirements", {})
    meets_requirements = True
    nation_district_keys = [district.get("type", "") for district in target.get("districts", [])]
    # Categories of all districts the nation has (e.g. {'forge', 'workshop'})
    nation_district_categories = {_district_key_to_category(k) for k in nation_district_keys if k}

    check_name = False
    check_defensive_pact = False
    check_military_alliance = False

    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = False
            for district in value:
                # Exact key match (old JSON format: e.g. "ancient_forge")
                if district in nation_district_keys:
                    has_district = True
                # Category match (new MongoDB format: e.g. "forge")
                elif district in nation_district_categories:
                    has_district = True
            if not has_district:
                meets_requirements = False
        elif requirement == "research":
            for tech in value:
                if not target.get("technologies", {}).get(tech, {}).get("researched", False):
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
            all_resource_upkeep = 0
            all_resource_upkeep_multiplier = 1
            for source in modifier_sources:
                for modifier, value in source.items():
                    if modifier.startswith(unit) or modifier.startswith("unit") or modifier.startswith(unit_type + "_unit") or (modifier.startswith("imperial_unit") and new_details.get("upkeep", {}).get("prestige", 0) > 0):
                        resource = modifier.replace(unit + "_", "").replace("imperial_unit_", "").replace(unit_type + "_unit_", "").replace("unit_", "").replace("_upkeep", "")
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
    global_modifiers = list(mongo.db["global_modifiers"].find())
    for global_mod in global_modifiers:
        for modifier in global_mod.get("external_modifiers", []):
            if modifier.get("type") == target_data_type:
                collected_modifiers.append({modifier.get("modifier", ""): modifier.get("value", 0)})
    
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
                        collected_modifiers.extend(collect_external_modifiers_from_object(link, required_fields, category_data.get(link_collection, {}).get("schema", {}), target_data_type, modifier_prefix, fields_as_modifiers))
                        
                        # Get the target object and check it too
                        if query_target in link:
                            target_id = link[query_target]
                            target_object = mongo.db[collection].find_one({"_id": ObjectId(target_id)})
                            if target_object:
                                collected_modifiers.extend(collect_external_modifiers_from_object(target_object, required_fields, linked_object_schema, target_data_type, modifier_prefix, fields_as_modifiers))
            
            elif field_schema.get("queryTargetAttribute"):
                query_target = field_schema["queryTargetAttribute"]
                linked_objects = []

                if "_id" in target:
                    linked_objects = list(mongo.db[collection].find({query_target: str(target.get("_id", ""))}))
                
                for object in linked_objects:
                    if object.get("equipped", True):
                        collected_modifiers.extend(collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type, modifier_prefix, fields_as_modifiers))
            else:
                object_id = target.get(field)
                if not object_id:
                    continue
                
                object = mongo.db[collection].find_one({"_id": ObjectId(object_id)})
                if not object:
                    continue
                collected_modifiers.extend(collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type, modifier_prefix, fields_as_modifiers))

    return collected_modifiers

def collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type, modifier_prefix=None, fields_as_modifiers=[]):
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
                                collected_modifiers.extend(collect_external_modifiers_from_object(link, value, category_data.get(link_collection, {}).get("schema", {}), target_data_type, modifier_prefix))
                                
                                # Get the target object and check it too
                                if query_target in link:
                                    target_id = link[query_target]
                                    target_object = mongo.db[collection].find_one({"_id": ObjectId(target_id)})
                                    if target_object:
                                        collected_modifiers.extend(collect_external_modifiers_from_object(target_object, value, linked_object_schema, target_data_type, modifier_prefix))
                    
                    elif req_field_schema.get("queryTargetAttribute"):
                        query_target = req_field_schema["queryTargetAttribute"]
                        linked_objects = list(mongo.db[collection].find({query_target: str(object["_id"])}))
                        for object in linked_objects:
                            if object.get("equipped", True):
                                collected_modifiers.extend(collect_external_modifiers_from_object(object, value, linked_object_schema, target_data_type, modifier_prefix))
            continue
        else:
            req_field_schema = linked_object_schema["properties"].get(req_field, {})
            if req_field in object:
                field_type = linked_object_schema["properties"].get(req_field, {}).get("bsonType")

                if field_type == "array" and req_field == "external_modifiers":
                    for modifier in object[req_field]:
                        if modifier.get("type") == target_data_type:
                            collected_modifiers.append({modifier.get("modifier", ""): modifier.get("value", 0)})
                
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
        modifiers["stability_loss_chance"] = 1
        modifiers["strength"] = -3
    elif over_capacity >= 20:
        modifiers["karma"] = -6
        modifiers["stability_loss_chance"] = 0.5
        modifiers["strength"] = -2
    elif over_capacity >= 10:
        modifiers["karma"] = -4
        modifiers["stability_loss_chance"] = 0.3
        modifiers["strength"] = -1
    elif over_capacity >= 5:
        modifiers["karma"] = -2
        modifiers["stability_loss_chance"] = 0.2
        modifiers["strength"] = -1
    elif over_capacity > 0:
        modifiers["karma"] = -2
        modifiers["stability_loss_chance"] = 0.1

    return modifiers

def calculate_road_capacity_modifiers(target):
    road_capacity = int(target.get("road_capacity", 0))
    current_territory = int(target.get("road_usage", 0))

    over_capacity = current_territory - road_capacity

    modifiers = {}

    if over_capacity >= 50:
        modifiers["wood_consumption"] = 5
        modifiers["stone_consumption"] = 5
        modifiers["mount_consumption"] = 5
    elif over_capacity >= 40:
        modifiers["wood_consumption"] = 4
        modifiers["stone_consumption"] = 4
        modifiers["mount_consumption"] = 4
    elif over_capacity >= 30:
        modifiers["wood_consumption"] = 3
        modifiers["stone_consumption"] = 3
        modifiers["mount_consumption"] = 3
    elif over_capacity >= 20:
        modifiers["wood_consumption"] = 2
        modifiers["stone_consumption"] = 2
        modifiers["mount_consumption"] = 2
    elif over_capacity > 10:
        modifiers["wood_consumption"] = 1
        modifiers["stone_consumption"] = 1
        modifiers["mount_consumption"] = 1
    elif over_capacity > 5:
        modifiers["wood_consumption"] = 1
        modifiers["stone_consumption"] = 1
    elif over_capacity > 0:
        modifiers["wood_consumption"] = 1
    
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

def sum_modifier_totals(modifiers):
    totals = {}
    for m in modifiers:
        totals[m.get("field", "")] = totals.get(m.get("field", ""), 0) + m.get("value", 0)
    return totals

def sum_law_totals(laws):
    totals = {}
    for law in laws:
        for key, value in law.items():
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


def compute_nation_breakdowns(target, schema_properties, component_sources, overall_total_modifiers, calculated_values, tagged_sources=None):
    """
    Build tooltip-friendly breakdowns for a nation's key fields.
    tagged_sources: list of {"label": str, "modifiers": dict} from _build_unit_tagged_sources.
    When provided, stab/resource/karma entries show individual source names instead of aggregate labels.
    """
    pop_count = int(calculated_values.get("pop_count", target.get("pop_count", 0)) or 0)
    road_usage = int(target.get("road_usage", 0) or 0)
    karma = int(calculated_values.get("karma", target.get("karma", 0)) or 0)
    unique_minority_count = int(calculated_values.get("unique_minority_count", target.get("unique_minority_count", 0)) or 0)
    territory_types = target.get("territory_types", {})
    rolling_consumption = {
        "pop_count": pop_count,
        "road_usage": road_usage,
        "karma": karma,
        "unique_minority_count": unique_minority_count,
    }

    breakdowns = {
        "stability_gain_chance": [],
        "stability_loss_chance": [],
        "resource_production": {},
        "resource_consumption": {},
        "money_income": [],
        "karma": [],
    }

    use_detailed = tagged_sources is not None

    # Stability Gain Chance
    stability_gain_base = schema_properties.get("stability_gain_chance", {}).get("base_value", 0)
    if use_detailed:
        stability_gain_mods = collect_detailed_field_contributions("stability_gain_chance", tagged_sources, multiplier=100)
    else:
        stability_gain_mods = collect_field_contributions("stability_gain_chance", component_sources, overall_total_modifiers)
    stability_gain_extra = stability_gain_extras(calculated_values, overall_total_modifiers, target, territory_types, tagged_sources if use_detailed else None)
    breakdowns["stability_gain_chance"].append({"label": "Base", "value": stability_gain_base * 100})
    breakdowns["stability_gain_chance"].extend(convert_percentage_contribs(stability_gain_mods))
    breakdowns["stability_gain_chance"].extend(convert_percentage_contribs(stability_gain_extra))
    breakdowns["stability_gain_chance"].append({"label": "Total", "value": round(calculated_values.get("stability_gain_chance", 0) * 100, 2)})

    # Stability Loss Chance
    stability_loss_base = schema_properties.get("stability_loss_chance", {}).get("base_value", 0)
    if use_detailed:
        stability_loss_mods = collect_detailed_field_contributions("stability_loss_chance", tagged_sources, multiplier=100)
    else:
        stability_loss_mods = collect_field_contributions("stability_loss_chance", component_sources, overall_total_modifiers)
    stability_loss_extra = stability_loss_extras(calculated_values, overall_total_modifiers, target, territory_types, tagged_sources if use_detailed else None)
    breakdowns["stability_loss_chance"].append({"label": "Base", "value": stability_loss_base * 100})
    breakdowns["stability_loss_chance"].extend(convert_percentage_contribs(stability_loss_mods))
    breakdowns["stability_loss_chance"].extend(convert_percentage_contribs(stability_loss_extra))
    breakdowns["stability_loss_chance"].append({"label": "Total", "value": round(calculated_values.get("stability_loss_chance", 0) * 100, 2)})

    # Resource Production
    for resource in json_data["general_resources"] + json_data["unique_resources"]:
        key = resource["key"]
        breakdowns["resource_production"][key] = build_resource_production_breakdown(
            key, component_sources, overall_total_modifiers, calculated_values, target, tagged_sources
        )

    # Resource Consumption
    for resource in json_data["general_resources"] + json_data["unique_resources"]:
        key = resource["key"]
        breakdowns["resource_consumption"][key] = build_resource_consumption_breakdown(
            key, component_sources, overall_total_modifiers, calculated_values, target, rolling_consumption, tagged_sources
        )

    # Karma
    karma_base = schema_properties.get("karma", {}).get("base_value", 0)
    if use_detailed:
        karma_mods = collect_detailed_field_contributions("karma", tagged_sources)
    else:
        karma_mods = collect_field_contributions("karma", component_sources, overall_total_modifiers)
    breakdowns["karma"].append({"label": "Base", "value": karma_base})
    breakdowns["karma"].extend(karma_mods)
    rolling = target.get("rolling_karma", 0)
    temporary = target.get("temporary_karma", 0)
    if rolling:
        breakdowns["karma"].append({"label": "Rolling Karma", "value": rolling})
    if temporary:
        breakdowns["karma"].append({"label": "Temporary Karma", "value": temporary})
    breakdowns["karma"].append({"label": "Total", "value": calculated_values.get("karma", karma)})

    # Money Income
    breakdowns["money_income"] = build_money_income_breakdown(
        component_sources, overall_total_modifiers, schema_properties, calculated_values, target, tagged_sources
    )

    return breakdowns


def collect_field_contributions(field_key, component_sources, overall_totals):
    contributions = []
    for label, source in component_sources.items():
        if not source:
            continue
        if field_key in source:
            contributions.append({"label": label.title(), "value": source.get(field_key, 0) * 100})
    return contributions


def collect_detailed_field_contributions(field_keys, tagged_sources, multiplier=1):
    """Return per-source contributions using labeled tagged sources.

    field_keys: str or list of str — all matched keys are summed per source.
    multiplier: applied to each value before appending (e.g. 100 for percentages).
    Only sources with a non-zero combined value are included.
    """
    if isinstance(field_keys, str):
        field_keys = [field_keys]
    contributions = []
    for source in tagged_sources:
        value = sum(source["modifiers"].get(k, 0) for k in field_keys)
        if value:
            contributions.append({"label": source["label"], "value": round(value * multiplier, 4)})
    return contributions


def convert_percentage_contribs(items):
    converted = []
    for item in items:
        rounded = round(item["value"], 4)
        if rounded:
            converted.append({"label": item["label"], "value": rounded})
    return converted


def stability_gain_extras(calculated_values, overall_total_modifiers, target, territory_types, tagged_sources=None):
    extras = []
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    minority_impact = 1 + overall_total_modifiers.get("minority_impact", 0)
    pop_count = calculated_values.get("pop_count", 0)
    road_usage = target.get("road_usage", 0)

    total_production = sum(calculated_values.get("resource_production", {}).values())

    extras.append({"label": "Karma", "value": max(min(karma * overall_total_modifiers.get("stability_gain_chance_per_positive_karma", 0), overall_total_modifiers.get("max_stability_gain_chance_per_positive_karma", 0)), 0) * 100})

    # Minorities: break down per source if possible
    if tagged_sources is not None:
        for source in tagged_sources:
            per_minority = source["modifiers"].get("stability_gain_chance_per_unique_minority", 0)
            if per_minority:
                extras.append({"label": source["label"], "value": round(unique_minority_count * minority_impact * per_minority * 100, 4)})
    else:
        extras.append({"label": "Minorities", "value": max(min(unique_minority_count * minority_impact * overall_total_modifiers.get("stability_gain_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_gain_chance_per_unique_minority", 0)), 0) * 100})

    # Population: break down per source if possible
    if tagged_sources is not None:
        for source in tagged_sources:
            per_pop = source["modifiers"].get("stability_gain_chance_per_pop", 0)
            if per_pop:
                extras.append({"label": source["label"], "value": round(pop_count * per_pop * 100, 4)})
    else:
        extras.append({"label": "Population", "value": pop_count * overall_total_modifiers.get("stability_gain_chance_per_pop", 0) * 100})

    extras.append({"label": "Road Usage", "value": int(road_usage) * overall_total_modifiers.get("stability_gain_chance_per_road_usage", 0) * 100})
    extras.append({"label": "Production", "value": overall_total_modifiers.get("stability_gain_chance_per_resource_production", 0) * total_production * 100})

    terrain_stability_gain = 0
    if overall_total_modifiers.get("stability_gain_chance_per_tile", 0) != 0:
        terrain_stability_gain += overall_total_modifiers.get("stability_gain_chance_per_tile", 0) * sum(territory_types.values())
    for terrain, terrain_count in territory_types.items():
        if overall_total_modifiers.get("stability_gain_chance_per_" + terrain, 0) != 0:
            terrain_stability_gain += overall_total_modifiers.get("stability_gain_chance_per_" + terrain, 0) * terrain_count
    if terrain_stability_gain != 0:
        extras.append({"label": "Territory", "value": terrain_stability_gain * 100})
    return extras


def stability_loss_extras(calculated_values, overall_total_modifiers, target, territory_types, tagged_sources=None):
    extras = []
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    minority_impact = 1 + overall_total_modifiers.get("minority_impact", 0)
    pop_count = calculated_values.get("pop_count", 0)
    road_usage = target.get("road_usage", 0)
    stability = target.get("stability", "Unknown")

    extras.append({"label": "Karma", "value": max(min(-karma * overall_total_modifiers.get("stability_loss_chance_per_negative_karma", 0), overall_total_modifiers.get("max_stability_loss_chance_per_negative_karma", 0)), 0) * 100})

    # Minorities: break down per source if possible
    if tagged_sources is not None:
        for source in tagged_sources:
            per_minority = source["modifiers"].get("stability_loss_chance_per_unique_minority", 0)
            if per_minority:
                extras.append({"label": source["label"], "value": round(unique_minority_count * minority_impact * per_minority * 100, 4)})
    else:
        extras.append({"label": "Minorities", "value": max(min(unique_minority_count * minority_impact * overall_total_modifiers.get("stability_loss_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_loss_chance_per_unique_minority", 0)), 0) * 100})

    # Population: break down per source if possible
    if tagged_sources is not None:
        for source in tagged_sources:
            per_pop = source["modifiers"].get("stability_loss_chance_per_pop", 0)
            if per_pop:
                extras.append({"label": source["label"], "value": round(pop_count * per_pop * 100, 4)})
    else:
        extras.append({"label": "Population", "value": pop_count * overall_total_modifiers.get("stability_loss_chance_per_pop", 0) * 100})

    extras.append({"label": "Road Usage", "value": int(road_usage) * overall_total_modifiers.get("stability_loss_chance_per_road_usage", 0) * 100})

    if stability == "United":
        extras.append({"label": "Stability Level", "value": overall_total_modifiers.get("stability_loss_chance_at_united", 0) * 100})
    elif stability == "Stable":
        extras.append({"label": "Stability Level", "value": overall_total_modifiers.get("stability_loss_chance_at_stable", 0) * 100})

    terrain_loss = 0
    for terrain, terrain_count in territory_types.items():
        if overall_total_modifiers.get("stability_loss_chance_per_" + terrain, 0) != 0:
            terrain_loss += overall_total_modifiers.get("stability_loss_chance_per_" + terrain, 0) * terrain_count
    if terrain_loss != 0:
        extras.append({"label": "Territory", "value": terrain_loss * 100})
    return extras


def build_resource_production_breakdown(resource_key, component_sources, overall_totals, calculated_values, target, tagged_sources=None):
    entries = []
    total_value = calculated_values.get("resource_production", {}).get(resource_key, 0)

    if tagged_sources is not None:
        entries.extend(collect_detailed_field_contributions(
            [resource_key + "_production", "resource_production"], tagged_sources
        ))
        # Jobs: break down per job type
        job_details = calculated_values.get("job_details", {})
        jobs_assigned = target.get("jobs", {})
        for job_key, count in jobs_assigned.items():
            if count and job_key in job_details:
                prod = job_details[job_key].get("production", {}).get(resource_key, 0)
                if prod:
                    label = job_details[job_key].get("display_name", job_key.replace("_", " ").title())
                    entries.append({"label": label, "value": prod * count})
    else:
        for label, source in component_sources.items():
            if not source:
                continue
            value = source.get(resource_key + "_production", 0) + source.get("resource_production", 0)
            if value:
                entries.append({"label": label.title(), "value": value})

    # Terrain: one entry per terrain type that produces this resource
    terrain_json_data = json_data["terrains"]
    territory_types = target.get("territory_types", {})
    for terrain, tile_count in territory_types.items():
        terrain_data = terrain_json_data.get(terrain, {})
        terrain_resource = terrain_data.get("resource", "none")
        # Check for swap modifiers
        for modifier, mod_value in overall_totals.items():
            if modifier.startswith(terrain + "_swap_") and modifier.endswith("_production") and mod_value > 1:
                terrain_resource = modifier.replace("_production", "").replace(terrain + "_swap_", "")
        if terrain_resource != resource_key:
            continue
        count_required = terrain_data.get("count_required", 4)
        count_required += overall_totals.get(terrain + "_terrain_count_required", 0)
        terrain_prod = tile_count // max(count_required, 1)
        if terrain_prod:
            display_name = terrain_data.get("display_name", terrain.replace("_", " ").title())
            entries.append({"label": f"Terrain: {display_name} ({tile_count} tiles)", "value": terrain_prod})
        # Extra production modifiers
        for modifier, mod_value in overall_totals.items():
            if modifier.startswith(terrain + "_extra_") and modifier.endswith("_production_per"):
                extra_resource = modifier.replace("_production_per", "").replace(terrain + "_extra_", "")
                if extra_resource == resource_key and mod_value:
                    extra_prod = tile_count // max(mod_value, 1)
                    if extra_prod:
                        display_name = terrain_data.get("display_name", terrain.replace("_", " ").title())
                        entries.append({"label": f"Terrain: {display_name} (extra)", "value": extra_prod})

    # Nodes
    nodes = overall_totals.get(resource_key + "_nodes", 0)
    if nodes:
        node_value = 2 + overall_totals.get("resource_node_value", 0) + overall_totals.get(resource_key + "_node_value", 0)
        entries.append({"label": "Nodes", "value": nodes * node_value})

    # Naval unit modifier
    naval_units = target.get("naval_unit_count", calculated_values.get("naval_unit_count", 0))
    per_naval = overall_totals.get(resource_key + "_production_per_naval_unit", 0)
    if naval_units and per_naval:
        entries.append({"label": "Naval Units", "value": int(math.floor(naval_units * per_naval))})

    # Religious homogeneity for research
    if resource_key == "research":
        homogeneity_bonus = overall_totals.get("research_production_if_religously_homogeneous", 0)
        if homogeneity_bonus:
            entries.append({"label": "Religious Homogeneity", "value": homogeneity_bonus})

    # Mill: food production from food storage
    if resource_key == "food":
        food_stockpile = target.get("resource_storage", {}).get("food", 0)
        per_stockpiled = overall_totals.get("food_production_per_stockpiled_food", 0)
        max_per_stockpiled = overall_totals.get("max_food_production_per_stockpiled_food", 0)
        if per_stockpiled > 0:
            mill_bonus = min(food_stockpile // per_stockpiled, max_per_stockpiled)
            if mill_bonus:
                entries.append({"label": "Mill (Stockpile)", "value": mill_bonus})

    # Locks
    if overall_totals.get("locks_" + resource_key + "_production", 0) > 0:
        entries.append({"label": "Production Locked", "value": -sum(e["value"] for e in entries)})

    entries.append({"label": "Total", "value": total_value})
    return entries


def build_resource_consumption_breakdown(resource_key, component_sources, overall_totals, calculated_values, target, rolling_consumption, tagged_sources=None):
    entries = []
    total_value = calculated_values.get("resource_consumption", {}).get(resource_key, 0)

    if tagged_sources is not None:
        entries.extend(collect_detailed_field_contributions(
            [resource_key + "_consumption", "resource_consumption"], tagged_sources
        ))
        # Jobs: break down per job type
        job_details = calculated_values.get("job_details", {})
        jobs_assigned = target.get("jobs", {})
        for job_key, count in jobs_assigned.items():
            if count and job_key in job_details:
                upkeep = job_details[job_key].get("upkeep", {}).get(resource_key, 0)
                if upkeep:
                    label = job_details[job_key].get("display_name", job_key.replace("_", " ").title())
                    entries.append({"label": label, "value": upkeep * count})
    else:
        for label, source in component_sources.items():
            if not source:
                continue
            value = source.get(resource_key + "_consumption", 0) + source.get("resource_consumption", 0)
            if value:
                entries.append({"label": label.title(), "value": value})

    if resource_key == "food":
        food_consumption_per_pop = 1 + overall_totals.get("food_consumption_per_pop", 0)
        consumption = rolling_consumption["pop_count"] * food_consumption_per_pop
        if food_consumption_per_pop < 1:
            consumption = math.ceil(consumption)
        else:
            consumption = math.floor(consumption)
        entries.append({"label": "Population", "value": consumption})
    elif resource_key == "research":
        tech_invest = 0
        for _, details in target.get("technologies", {}).items():
            tech_invest += details.get("investing", 0)
        if tech_invest:
            entries.append({"label": "Tech Investment", "value": tech_invest})

    entries.append({"label": "Total", "value": total_value})
    return entries


def build_money_income_breakdown(component_sources, overall_totals, schema_properties, calculated_values, target, tagged_sources=None):
    entries = []
    base_value = schema_properties.get("money_income", {}).get("base_value", 0)
    entries.append({"label": "Base", "value": base_value})

    if tagged_sources is not None:
        entries.extend(collect_detailed_field_contributions("money_income", tagged_sources))
        job_value = component_sources.get("jobs", {}).get("money_income", 0)
        if job_value:
            entries.append({"label": "Jobs", "value": job_value})
    else:
        for label, source in component_sources.items():
            if not source:
                continue
            value = source.get("money_income", 0)
            if value:
                entries.append({"label": label.title(), "value": value})

    pop_count = calculated_values.get("pop_count", target.get("pop_count", 0))
    if tagged_sources is not None:
        for source in tagged_sources:
            per_pop = source["modifiers"].get("money_income_per_pop", 0)
            if per_pop:
                entries.append({"label": source["label"], "value": round(per_pop * pop_count, 4)})
    else:
        money_income_per_pop_total = overall_totals.get("money_income_per_pop", 0)
        if money_income_per_pop_total:
            entries.append({"label": "Per Pop", "value": money_income_per_pop_total * pop_count})

    stockpile = target.get("money", 0)
    per_storage = overall_totals.get("money_income_per_money_storage", 0)
    max_stockpile = overall_totals.get("max_money_income_per_money_storage", 0)
    if per_storage > 0:
        entries.append({"label": "Stockpile", "value": min((stockpile // per_storage) * 100, max_stockpile)})

    entries.append({"label": "Total", "value": calculated_values.get("money_income", target.get("money_income", 0))})
    return entries
