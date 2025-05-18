import math
import copy
from app_core import mongo, json_data, category_data, land_unit_json_files, naval_unit_json_files
from calculations.compute_functions import CUSTOM_COMPUTE_FUNCTIONS
from bson.objectid import ObjectId
from app_core import json_data

def calculate_all_fields(target, schema, target_data_type):
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

        territory_terrain_totals = collect_territory_terrain(target)

        jobs_assigned = collect_jobs_assigned(target)
        job_details = calculate_job_details(target, district_details, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total)
        job_totals = sum_job_totals(jobs_assigned, job_details)

        land_units_assigned = collect_land_units_assigned(target)
        land_unit_details = calculate_unit_details(target, "land", land_unit_json_files, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total)

        naval_units_assigned = collect_naval_units_assigned(target)
        naval_unit_details = calculate_unit_details(target, "naval", naval_unit_json_files, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total)

        unit_totals = sum_all_unit_totals(land_units_assigned, land_unit_details, naval_units_assigned, naval_unit_details, external_modifiers_total)

        prestige_modifiers = {}
        if target.get("empire", False):
            prestige_modifiers = calculate_prestige_modifiers(target, schema_properties)
    elif target_data_type == "nation_jobs":
        job_details = calculate_job_details(target, district_details, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total)
    elif target_data_type == "character":
        title_modifiers = calculate_title_modifiers(target, target_data_type, schema_properties)

    attributes_to_precalculate = ["administration", "effective_territory", "current_territory", "road_capacity", "effective_pop_capacity", "pop_count"]

    overall_total_modifiers = {}
    calculated_values = {"district_details": district_details, "job_details": job_details, "land_unit_details": land_unit_details, "naval_unit_details": naval_unit_details}
    for d in [external_modifiers_total, modifier_totals, district_totals, tech_totals, territory_terrain_totals, city_totals, law_totals, job_totals, unit_totals, prestige_modifiers, title_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
    
    for field in attributes_to_precalculate:
        base_value = schema_properties.get(field, {}).get("base_value", 0)
        calculated_values[field] = compute_field(
            field, target, base_value, schema_properties.get(field, {}),
            overall_total_modifiers
        )
        target[field] = calculated_values[field]

    effective_territory_modifiers = calculate_effective_territory_modifiers(target)

    road_capacity_modifiers = calculate_road_capacity_modifiers(target)

    effective_pop_capacity_modifiers = calculate_effective_pop_capacity_modifiers(target)

    for d in [effective_territory_modifiers, road_capacity_modifiers, effective_pop_capacity_modifiers]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value

    for field, field_schema in schema_properties.items():
        if isinstance(field_schema, dict) and field_schema.get("calculated") and field not in calculated_values.keys():
            base_value = field_schema.get("base_value", 0)
            calculated_values[field] = compute_field(
                field, target, base_value, field_schema,
                overall_total_modifiers
            )
            target[field] = calculated_values[field]
    
    if target_data_type == "nation":
        food_consumption_per_pop = 1 + overall_total_modifiers.get("food_consumption_per_pop", 0)
        food_consumption = calculated_values.get("pop_count", 0) * food_consumption_per_pop
        if food_consumption_per_pop < 1:
            food_consumption = math.ceil(food_consumption)
        else:
            food_consumption = math.floor(food_consumption)
        
        excess_food = calculated_values.get("resource_production", {}).get("food", 0) + target.get("resource_storage", {}).get("food", 0)

        if excess_food < food_consumption / 2:
            #Nation is Starving
            overall_total_modifiers["strength"] = overall_total_modifiers.get("strength", 0) - 2
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + 0.25
            modifier_totals["job_resource_production"] = modifier_totals.get("job_resource_production", 0) - 1
            modifier_totals["minimum_job_resource_production"] = -1
            modifier_totals["hunter_food_production"] = modifier_totals.get("hunter_food_production", 0) + 1
            modifier_totals["farmer_food_production"] = modifier_totals.get("farmer_food_production", 0) + 1
            modifier_totals["fisherman_food_production"] = modifier_totals.get("fisherman_food_production", 0) + 1
            modifier_totals["locks_research_production"] = 1

        elif excess_food < food_consumption:
            #Nation is Underfed
            overall_total_modifiers["strength"] = overall_total_modifiers.get("strength", 0) - 1
            modifier_totals["stability_loss_chance"] = modifier_totals.get("stability_loss_chance", 0) + 0.1
            modifier_totals["job_resource_production"] = modifier_totals.get("job_resource_production", 0) - 1
            modifier_totals["minimum_job_resource_production"] = -1
            modifier_totals["hunter_food_production"] = modifier_totals.get("hunter_food_production", 0) + 1
            modifier_totals["farmer_food_production"] = modifier_totals.get("farmer_food_production", 0) + 1
            modifier_totals["fisherman_food_production"] = modifier_totals.get("fisherman_food_production", 0) + 1

        if excess_food < food_consumption:
            job_details = calculate_job_details(target, district_details, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total)
            job_totals = sum_job_totals(target.get("jobs", {}), job_details)
            calculated_values["job_details"] = job_details

            overall_total_modifiers = {}
            for d in [external_modifiers_total, modifier_totals, district_totals, territory_terrain_totals, city_totals, law_totals, job_totals, unit_totals, prestige_modifiers, title_modifiers]:
                for key, value in d.items():
                    overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value
            
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

    return calculated_values

def compute_field(field, target, base_value, field_schema, overall_total_modifiers):
    compute_func = CUSTOM_COMPUTE_FUNCTIONS.get(field, compute_field_default)
    return compute_func(field, target, base_value, field_schema, overall_total_modifiers)

def compute_field_default(field, target, base_value, field_schema, overall_total_modifiers):
    return base_value + overall_total_modifiers.get(field, 0)

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

    ignore_nodes = law_totals.get("ignore_nodes", 0)
    
    for district in nation_districts:
        if isinstance(district, dict):
            district_type = district.get("type", "")
            district_node = district.get("node", "")
            district_modifiers = district_details.get(district_type, {}).get("modifiers", {})
            collected_modifiers.append(district_modifiers)
            if district_node == district_details.get(district_type, {}).get("synergy_requirement", ""):
                collected_modifiers.append(district_details.get(district_type, {}).get("synergy_modifiers", {}))
                if district_details.get(district_type, {}).get("synergy_node_active", True) and district_node != "luxury" and ignore_nodes < 1:
                    collected_modifiers.append({district_node + "_production": 2})
            elif district_node != "" and district_node != "luxury" and ignore_nodes < 1:
                collected_modifiers.append({district_node + "_production": 2})

    imperial_district_json_data = json_data["nation_imperial_districts"]

    if target.get("empire", False):
        imperial_district = target.get("imperial_district", {})
        imperial_district_type = imperial_district.get("type", "")
        imperial_district_node = imperial_district.get("node", "")
        imperial_district_synergy_node = imperial_district_json_data.get(imperial_district_type, {}).get("synergy_requirement", "")
        imperial_district_synergy_node_active = imperial_district_json_data.get(imperial_district_type, {}).get("synergy_node_active", True)
        imperial_district_modifiers = imperial_district_json_data.get(imperial_district_type, {}).get("modifiers", {})
        collected_modifiers.append(imperial_district_modifiers)
        if imperial_district_node == imperial_district_synergy_node or (imperial_district_synergy_node == "any" and imperial_district_node != ""):
            collected_modifiers.append(imperial_district_json_data.get(imperial_district_type, {}).get("synergy_modifiers", {}))
            if imperial_district_synergy_node_active and ignore_nodes < 1:
                collected_modifiers.append({imperial_district_node + "_production": 2})
        elif imperial_district_node != "" and ignore_nodes < 1:
            collected_modifiers.append({imperial_district_node + "_production": 2})

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

def calculate_title_modifiers(target, target_data_type, schema_properties):
    title_modifiers = {}
    titles = target.get("titles", [])
    for title in titles:
        title_data = json_data["titles"].get(title, {})
        for key, value in title_data.get("modifiers", {}).items():
            if key.startswith(target_data_type + "_"):
                temp_key = key.replace(target_data_type + "_", "")
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
        collected_modifiers.append({city_node + "_production": 2})

    return collected_modifiers

def sum_tech_totals(technologies):
    tech_totals = {}
    for tech, value in technologies.items():
        if value.get("researched", False):
            modifiers = json_data["tech"].get(tech, {}).get("modifiers", {})
            for key, value in modifiers.items():
                tech_totals[key] = tech_totals.get(key, 0) + value
    return tech_totals

def collect_territory_terrain(target):
    territory_types = target.get("territory_types", {})
    total_modifiers = {}
    terrain_json_data = json_data["terrains"]
    for terrain, value in territory_types.items():
        terrain_modifier = terrain_json_data.get(terrain, {}).get("resource", "none") + "_production"
        terrain_count_required = terrain_json_data.get(terrain, {}).get("count_required", 4)
        total_modifiers[terrain_modifier] = total_modifiers.get(terrain_modifier, 0) + value // terrain_count_required
    return total_modifiers

def collect_jobs_assigned(target):
    return target.get("jobs", {})

def calculate_job_details(target, district_details, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total):
    job_details = json_data["jobs"]
    modifier_sources = [modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total]
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
        if ("requirements" not in details or ("district" in details["requirements"] and details["requirements"]["district"] in district_types)) and not locked:
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

def collect_land_units_assigned(target):
    return target.get("land_units", {})

def collect_naval_units_assigned(target):
    return target.get("naval_units", {})

def check_unit_requirements(target, unit_details):
    requirements = unit_details.get("requirements", {})
    meets_requirements = True
    district_types = [district.get("type", "") for district in target.get("districts", [])]
    for requirement, value in requirements.items():
        if requirement == "district":
            has_district = False
            for district in value:
                if district in district_types:
                    has_district = True
            if not has_district:
                meets_requirements = False
        elif requirement == "research":
            meets_requirements = False
        elif requirement == "spell":
            meets_requirements = False
        elif requirement == "artifact":
            meets_requirements = False # TODO: Implement the check for artifacts
        elif requirement == "name" and target.get("name", "") not in value:
            meets_requirements = False
        elif requirement == "empire" and target.get("empire", False):
            meets_requirements = False
        # TODO: Add defensive pacts and mil alliances
    return meets_requirements

def calculate_unit_details(target, unit_type, unit_json_files, modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total):
    unit_details = {}
    for unit_file in unit_json_files:
        unit_details.update(json_data[unit_file])

    modifier_sources = [modifier_totals, district_totals, city_totals, law_totals, external_modifiers_total]
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
                    if modifier.startswith(unit) or modifier.startswith("unit") or modifier.startswith(unit_type + "_unit"):
                        resource = modifier.replace(unit + "_", "").replace(unit_type + "_unit_", "").replace("unit_", "").replace("_upkeep", "")
                        if resource != "resource" and modifier.endswith("upkeep"):
                            new_details.setdefault("upkeep", {})[resource] = new_details.get("upkeep", {}).get(resource, 0) + value
                        elif modifier.endswith("resource_upkeep"):
                            all_resource_upkeep = all_resource_upkeep + value
                        elif modifier.endswith("resource_upkeep_mult"):
                            all_resource_upkeep_multiplier = all_resource_upkeep_multiplier * value

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

            new_unit_details[unit] = new_details
    
    return new_unit_details

def collect_external_requirements(target, schema, target_data_type):
    external_reqs = schema.get("external_calculation_requirements", {})
    collected_modifiers = []
    
    for field, required_fields in external_reqs.items():

        field_schema = schema["properties"].get(field, {})

        collections = field_schema.get("collections")
        if not collections:
            continue

        for collection in collections:
            linked_object_schema = category_data.get(collection, {}).get("schema", {})

            if field_schema.get("queryTargetAttribute"):
                query_target = field_schema["queryTargetAttribute"]
                linked_objects = list(mongo.db[collection].find({query_target: str(target["_id"])}))
                for object in linked_objects:
                    collected_modifiers.extend(collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type))
            else:
                object_id = target.get(field)
                if not object_id:
                    continue
                
                object = mongo.db[collection].find_one({"_id": ObjectId(object_id)})
                collected_modifiers.extend(collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type))
    
    return collected_modifiers

def collect_external_modifiers_from_object(object, required_fields, linked_object_schema, target_data_type):
    collected_modifiers = []

    for req_field in required_fields:
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
                    if key.startswith(target_data_type + "_"):
                        collected_modifiers.append({key.replace(target_data_type + "_", ""): value})
            
            elif field_type == "array" and req_field == "modifiers":
                for modifier in object[req_field]:
                    if modifier.get("field").startswith(target_data_type + "_"):
                        field = modifier["field"].replace(target_data_type + "_", "")
                        collected_modifiers.append({field: modifier["value"]})
            
            elif field_type == "array" and req_field == "titles":
                calculated_title_modifiers = calculate_title_modifiers(object, target_data_type, linked_object_schema["properties"])
                for key, value in calculated_title_modifiers.items():
                    collected_modifiers.append({key: value})
    
    return collected_modifiers

def calculate_effective_territory_modifiers(target):
    effective_territory = int(target.get("effective_territory", 0))
    current_territory = int(target.get("current_territory", 0))

    over_capacity = current_territory - effective_territory

    modifiers = {}

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

def sum_job_totals(jobs_assigned, job_details):
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
            if field == "money":
                field = "money_income"
            elif field in general_resources or field in unique_resources:
                field = field + "_production"
            
            total_value = val * count
            
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
            if field == "prestige":
                field = "prestige_income"
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