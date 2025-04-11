import math
import copy
from app_core import mongo, json_data
from calculations.compute_functions import CUSTOM_COMPUTE_FUNCTIONS

def calculate_all_fields(target, schema):
    schema_properties = schema.get("properties", {})

    modifiers = collect_modifiers(target)
    modifier_totals = sum_modifier_totals(modifiers)

    laws = collect_laws(target, schema)
    law_totals = sum_law_totals(laws)

    districts = collect_districts(target)
    district_totals = sum_district_totals(districts)

    cities = collect_cities(target)
    city_totals = sum_city_totals(cities)

    nodes = collect_nodes(target)
    node_totals = sum_node_totals(nodes)

    jobs_assigned = collect_jobs_assigned(target)
    job_details = calculate_job_details(target, modifier_totals, district_totals, city_totals, node_totals, law_totals)
    job_totals = sum_job_totals(jobs_assigned, job_details)

    overall_total_modifiers = {}
    for d in [modifier_totals, district_totals, city_totals, node_totals, law_totals, job_totals]:
        for key, value in d.items():
            overall_total_modifiers[key] = overall_total_modifiers.get(key, 0) + value

    calculated_values = {"job_details": job_details}

    for field, field_schema in schema_properties.items():
        if isinstance(field_schema, dict) and field_schema.get("calculated"):
            base_value = field_schema.get("base_value", 0)
            calculated_values[field] = compute_field(
                field, target, base_value, field_schema,
                overall_total_modifiers
            )
            target[field] = calculated_values[field]

    return calculated_values

def compute_field(field, target, base_value, field_schema, overall_total_modifiers):
    compute_func = CUSTOM_COMPUTE_FUNCTIONS.get(field, compute_field_default)
    return compute_func(field, target, base_value, field_schema, overall_total_modifiers)

def compute_field_default(field, target, base_value, field_schema, overall_total_modifiers):
    return base_value + overall_total_modifiers.get(field, 0)

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

def collect_districts(target):
    nation_districts = target.get("districts", [])
    collected_modifiers = []
    district_json_data = json_data["districts"]
    for district in nation_districts:
        district_type = district.get("type", "")
        district_node = district.get("node", "")
        district_modifiers = district_json_data.get(district_type, {}).get("modifiers", {})
        collected_modifiers.append(district_modifiers)
        if district_node == district_json_data.get(district_type, {}).get("synergy_requirement", ""):
            collected_modifiers.append(district_json_data.get(district_type, {}).get("synergy_modifiers", {}))
            if district_json_data.get(district_type, {}).get("synergy_node_active", True):
                collected_modifiers.append({district_node + "_production": 1})
        elif district_node != "":
            collected_modifiers.append({district_node + "_production": 1})

    return collected_modifiers

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
        collected_modifiers.append({city_node + "_production": 1})

    return collected_modifiers

def collect_nodes(target):
    nodes = target.get("resource_nodes", {})
    collected_modifiers = []
    for node_key, node_qty in nodes.items():
        collected_modifiers.append({node_key + "_production": node_qty}) #TODO: Add Node Value

    return collected_modifiers

def collect_jobs_assigned(target):
    return target.get("jobs", {})

def calculate_job_details(target, modifier_totals, district_totals, city_totals, node_totals, law_totals):
    job_details = json_data["jobs"]
    modifier_sources = [modifier_totals, district_totals, city_totals, law_totals, node_totals]
    district_types = []
    for district in target.get("districts", []):
        district_types.append(district["type"])

    new_job_details = {}
    for job, details in job_details.items():
        if "requirements" not in details or ("district" in details["requirements"] and details["requirements"]["district"] in district_types):
            new_details = copy.deepcopy(details)
            for source in modifier_sources:
                for modifier, value in source.items():
                    if modifier.startswith(job):
                        resource = modifier.replace(job + "_", "").replace("_production", "").replace("_upkeep", "")
                        if modifier.endswith("production"):
                            new_details.setdefault("production", {})[resource] = new_details.get("production", {}).get(resource, 0) + value
                        elif modifier.endswith("upkeep"):
                            new_details.setdefault("upkeep", {})[resource] = new_details.get("upkeep", {}).get(resource, 0) + value
                    elif modifier.startswith("job"):
                        resource = modifier.replace("job_", "").replace("_production", "").replace("_upkeep", "")
                        if modifier.endswith("production"):
                            new_details.setdefault("production", {})[resource] = new_details.get("production", {}).get(resource, 0) + value
                        elif modifier.endswith("upkeep"):
                            new_details.setdefault("upkeep", {})[resource] = new_details.get("upkeep", {}).get(resource, 0) + value
            new_job_details[job] = new_details

    return new_job_details

def sum_modifier_totals(modifiers):
    totals = {}
    for m in modifiers:
        totals[m["field"]] = totals.get(m["field"], 0) + m["value"]
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

def sum_node_totals(nodes):
    totals = {}
    for d in nodes:
        for key, value in d.items():
            totals[key] = totals.get(key, 0) + value
    return totals

def sum_job_totals(jobs_assigned, job_details):
    totals = {}
    for job, count in jobs_assigned.items():
        for field, val in job_details.get(job, {}).get("production", {}).items():
            totals[field + "_production"] = totals.get(field, 0) + (val * count)
        for field, val in job_details.get(job, {}).get("upkeep", {}).items():
            totals[field + "_consumption"] = totals.get(field, 0) + (val * count)
    return totals
