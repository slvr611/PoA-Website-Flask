import math
import copy
from copy import deepcopy
from app_core import mongo, json_data, category_data, land_unit_json_files, naval_unit_json_files
from calculations.compute_functions import compute_pop_count, compute_field
from bson.objectid import ObjectId
from app_core import json_data

def calculate_all_fields(target, schema, target_data_type, return_breakdowns=False):
    schema_properties = schema.get("properties", {})

    external_modifiers = collect_external_requirements(target, schema, target_data_type)
    external_modifiers_total = sum_external_modifier_totals(external_modifiers)

    modifiers = collect_modifiers(target)
    modifier_totals = sum_modifier_totals(modifiers)

    laws = collect_laws(target, schema)
    law_totals = sum_law_totals(laws)

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

    city_totals = {}
    tech_totals = {}
    loose_node_totals = {}
    territory_terrain_totals = {}
    job_details = {}
    job_totals = {}
    land_unit_details = {}
    naval_unit_details = {}
    unit_totals = {}
    prestige_modifiers = {}
    title_modifiers = {}

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
        land_unit_details = calculate_unit_details(target, "land", land_unit_json_files, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)

        naval_units_assigned = collect_naval_units_assigned(target)
        naval_unit_details = calculate_unit_details(target, "naval", naval_unit_json_files, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total)

        unit_totals = sum_all_unit_totals(land_units_assigned, land_unit_details, naval_units_assigned, naval_unit_details, external_modifiers_total)

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

    attributes_to_precalculate = ["administration", "effective_territory", "current_territory", "road_capacity", "effective_pop_capacity", "pop_count"]

    overall_total_modifiers = {}
    calculated_values = {"district_details": district_details, "job_details": job_details, "land_unit_details": land_unit_details, "naval_unit_details": naval_unit_details}
    for d in [external_modifiers_total, modifier_totals, district_totals, tech_totals, loose_node_totals, territory_terrain_totals, city_totals, law_totals, job_totals, unit_totals, prestige_modifiers, title_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
    
    for field in attributes_to_precalculate:
        base_value = schema_properties.get(field, {}).get("base_value", 0)
        calculated_values[field] = compute_field(
            field, target, base_value, schema_properties.get(field, {}),
            overall_total_modifiers
        )
        target[field] = calculated_values[field]

    effective_territory_modifiers = calculate_effective_territory_modifiers(target, schema_properties)

    road_capacity_modifiers = calculate_road_capacity_modifiers(target)

    effective_pop_capacity_modifiers = calculate_effective_pop_capacity_modifiers(target)

    for d in [effective_territory_modifiers, road_capacity_modifiers, effective_pop_capacity_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
    
    overall_total_modifiers = parse_meta_modifiers(target, overall_total_modifiers)

    #print(overall_total_modifiers)

    for field, field_schema in schema_properties.items():
        if isinstance(field_schema, dict) and field_schema.get("calculated") and field not in calculated_values.keys():
            base_value = field_schema.get("base_value", 0)
            calculated_values[field] = compute_field(
                field, target, base_value, field_schema,
                overall_total_modifiers
            )
            target[field] = calculated_values[field]
    
    # Calculate progress per session for progress quests
    if "progress_quests" in target:
        target["progress_quests"] = compute_field(
            "progress_quests", target, 0, {},
            overall_total_modifiers
        )
        calculated_values["progress_quests"] = target["progress_quests"]
        
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
        )
        return calculated_values, breakdowns

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
        print(specific_title_data)
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
            all_resource_production = 0
            all_resource_upkeep = 0
            all_resource_production_multiplier = 1
            all_resource_upkeep_multiplier = 1
            all_resource_minimum_production = 1
            all_resource_minimum_production += modifier_totals.get("minimum_" + job + "_resource_production", 0) + district_totals.get("minimum_" + job + "_resource_production", 0) + city_totals.get("minimum_" + job + "_resource_production", 0) + law_totals.get("minimum_" + job + "_resource_production", 0) + external_modifiers_total.get("minimum_" + job + "_resource_production", 0)
            all_resource_minimum_production += modifier_totals.get("minimum_job_resource_production", 0) + district_totals.get("minimum_job_resource_production", 0) + city_totals.get("minimum_job_resource_production", 0) + law_totals.get("minimum_job_resource_production", 0) + external_modifiers_total.get("minimum_job_resource_production", 0)
            all_resource_minimum_production = max(all_resource_minimum_production, 0)
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
                        new_production = int(math.ceil(new_production))
                    elif all_resource_production_multiplier > 1:
                        new_production = int(math.floor(new_production))
                    else:
                        new_production = int(round(new_production))
                    new_production = max(new_production, all_resource_minimum_production)
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
    
    return meets_requirements

def collect_land_units_assigned(target):
    return target.get("land_units", {})

def collect_naval_units_assigned(target):
    return target.get("naval_units", {})

def check_unit_requirements(target, unit_details):
    requirements = unit_details.get("requirements", {})
    meets_requirements = True
    district_types = [district.get("type", "") for district in target.get("districts", [])]

    check_name = False
    check_defensive_pact = False
    check_military_alliance = False

    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = False
            for district in value:
                if district in district_types:
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
            meets_requirements = False # TODO: Implement the check for artifacts
        elif requirement == "name":
            check_name = True
        elif requirement == "empire" and target.get("empire", False):
            meets_requirements = False,
        elif requirement == "defensive_pact":
            check_defensive_pact = True
        elif requirement == "military_alliance":
            check_military_alliance = True
        elif requirement == "mercenary":
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

def calculate_unit_details(target, unit_type, unit_json_files, modifier_totals, district_totals, tech_totals, city_totals, law_totals, external_modifiers_total):
    unit_details = {}
    for unit_file in unit_json_files:
        unit_details.update(json_data[unit_file])

    modifier_sources = [modifier_totals, district_totals, city_totals, tech_totals, law_totals, external_modifiers_total]
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

def sum_all_unit_totals(land_units_assigned, land_unit_details, naval_units_assigned, naval_unit_details, external_modifiers_total):
    land_unit_totals = sum_unit_totals(land_units_assigned, land_unit_details, land_unit_json_files)
    naval_unit_totals = sum_unit_totals(naval_units_assigned, naval_unit_details, naval_unit_json_files)
    totals = {}
    for key, value in land_unit_totals.items():
        totals[key] = value
    for key, value in naval_unit_totals.items():
        totals[key] = totals.get(key, 0) + value
    return totals

def sum_unit_totals(units_assigned, unit_details, unit_json_files):
    if not units_assigned:
        return {}

    original_unit_details = {}
    for unit_file in unit_json_files:
        original_unit_details.update(json_data[unit_file])

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


def compute_nation_breakdowns(target, schema_properties, component_sources, overall_total_modifiers, calculated_values):
    """
    Build tooltip-friendly breakdowns for a nation's key fields.
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
    }

    # Stability Gain Chance
    stability_gain_base = schema_properties.get("stability_gain_chance", {}).get("base_value", 0)
    stability_gain_mods = collect_field_contributions("stability_gain_chance", component_sources, overall_total_modifiers)
    stability_gain_extra = stability_gain_extras(calculated_values, overall_total_modifiers, target, territory_types)
    breakdowns["stability_gain_chance"].append({"label": "Base", "value": stability_gain_base * 100})
    breakdowns["stability_gain_chance"].extend(convert_percentage_contribs(stability_gain_mods))
    breakdowns["stability_gain_chance"].extend(convert_percentage_contribs(stability_gain_extra))
    breakdowns["stability_gain_chance"].append({"label": "Total", "value": round(calculated_values.get("stability_gain_chance", 0) * 100, 2)})

    # Stability Loss Chance
    stability_loss_base = schema_properties.get("stability_loss_chance", {}).get("base_value", 0)
    stability_loss_mods = collect_field_contributions("stability_loss_chance", component_sources, overall_total_modifiers)
    stability_loss_extra = stability_loss_extras(calculated_values, overall_total_modifiers, target, territory_types)
    breakdowns["stability_loss_chance"].append({"label": "Base", "value": stability_loss_base * 100})
    breakdowns["stability_loss_chance"].extend(convert_percentage_contribs(stability_loss_mods))
    breakdowns["stability_loss_chance"].extend(convert_percentage_contribs(stability_loss_extra))
    breakdowns["stability_loss_chance"].append({"label": "Total", "value": round(calculated_values.get("stability_loss_chance", 0) * 100, 2)})

    # Resource Production
    for resource in json_data["general_resources"] + json_data["unique_resources"]:
        key = resource["key"]
        breakdowns["resource_production"][key] = build_resource_production_breakdown(
            key, component_sources, overall_total_modifiers, calculated_values, target
        )

    # Resource Consumption
    for resource in json_data["general_resources"] + json_data["unique_resources"]:
        key = resource["key"]
        breakdowns["resource_consumption"][key] = build_resource_consumption_breakdown(
            key, component_sources, overall_total_modifiers, calculated_values, target, rolling_consumption
        )

    # Money Income
    breakdowns["money_income"] = build_money_income_breakdown(
        component_sources, overall_total_modifiers, schema_properties, calculated_values, target
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


def convert_percentage_contribs(items):
    converted = []
    for item in items:
        converted.append({"label": item["label"], "value": round(item["value"], 4)})
    return converted


def stability_gain_extras(calculated_values, overall_total_modifiers, target, territory_types):
    extras = []
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    minority_impact = 1 + overall_total_modifiers.get("minority_impact", 0)
    pop_count = calculated_values.get("pop_count", 0)
    road_usage = target.get("road_usage", 0)

    total_production = sum(calculated_values.get("resource_production", {}).values())

    extras.append({"label": "Karma", "value": max(min(karma * overall_total_modifiers.get("stability_gain_chance_per_positive_karma", 0), overall_total_modifiers.get("max_stability_gain_chance_per_positive_karma", 0)), 0) * 100})
    extras.append({"label": "Minorities", "value": max(min(unique_minority_count * minority_impact * overall_total_modifiers.get("stability_gain_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_gain_chance_per_unique_minority", 0)), 0) * 100})
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


def stability_loss_extras(calculated_values, overall_total_modifiers, target, territory_types):
    extras = []
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    minority_impact = 1 + overall_total_modifiers.get("minority_impact", 0)
    pop_count = calculated_values.get("pop_count", 0)
    road_usage = target.get("road_usage", 0)
    stability = target.get("stability", "Unknown")

    extras.append({"label": "Karma", "value": max(min(-karma * overall_total_modifiers.get("stability_loss_chance_per_negative_karma", 0), overall_total_modifiers.get("max_stability_loss_chance_per_negative_karma", 0)), 0) * 100})
    extras.append({"label": "Minorities", "value": max(min(unique_minority_count * minority_impact * overall_total_modifiers.get("stability_loss_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_loss_chance_per_unique_minority", 0)), 0) * 100})
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


def build_resource_production_breakdown(resource_key, component_sources, overall_totals, calculated_values, target):
    entries = []
    total_value = calculated_values.get("resource_production", {}).get(resource_key, 0)

    for label, source in component_sources.items():
        if not source:
            continue
        value = source.get(resource_key + "_production", 0) + source.get("resource_production", 0)
        if value:
            entries.append({"label": label.title(), "value": value})

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

    # Locks
    if overall_totals.get("locks_" + resource_key + "_production", 0) > 0:
        entries.append({"label": "Production Locked", "value": -sum(e["value"] for e in entries)})

    entries.append({"label": "Total", "value": total_value})
    return entries


def build_resource_consumption_breakdown(resource_key, component_sources, overall_totals, calculated_values, target, rolling_consumption):
    entries = []
    total_value = calculated_values.get("resource_consumption", {}).get(resource_key, 0)

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


def build_money_income_breakdown(component_sources, overall_totals, schema_properties, calculated_values, target):
    entries = []
    base_value = schema_properties.get("money_income", {}).get("base_value", 0)
    entries.append({"label": "Base", "value": base_value})

    for label, source in component_sources.items():
        if not source:
            continue
        value = source.get("money_income", 0)
        if value:
            entries.append({"label": label.title(), "value": value})

    money_income_per_pop_total = overall_totals.get("money_income_per_pop", 0)
    if money_income_per_pop_total:
        entries.append({"label": "Per Pop", "value": money_income_per_pop_total * calculated_values.get("pop_count", target.get("pop_count", 0))})

    stockpile = target.get("money", 0)
    per_storage = overall_totals.get("money_income_per_money_storage", 0)
    max_stockpile = overall_totals.get("max_money_income_per_money_storage", 0)
    if per_storage > 0:
        entries.append({"label": "Stockpile", "value": min((stockpile // per_storage) * 100, max_stockpile)})

    entries.append({"label": "Total", "value": calculated_values.get("money_income", target.get("money_income", 0))})
    return entries
