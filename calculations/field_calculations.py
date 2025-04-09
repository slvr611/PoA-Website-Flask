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

    districts = collect_districts(target, schema)
    district_totals = sum_district_totals(districts)

    jobs_assigned = collect_jobs_assigned(target, schema)
    job_details = calculate_job_details(target, modifier_totals, district_totals, law_totals)
    job_totals = sum_job_totals(jobs_assigned, job_details)

    calculated_values = {"job_details": job_details}

    for field, field_schema in schema_properties.items():
        if isinstance(field_schema, dict) and field_schema.get("calculated"):
            base_value = field_schema.get("base_value", 0)
            calculated_values[field] = compute_field(
                field, target, base_value, field_schema,
                modifier_totals, district_totals, law_totals, job_totals
            )
            target[field] = calculated_values[field]

    return calculated_values

def compute_field(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compute_func = CUSTOM_COMPUTE_FUNCTIONS.get(field, compute_field_default)
    return compute_func(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals)

def compute_field_default(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    return base_value + \
           modifier_totals.get(field, 0) + \
           district_totals.get(field, 0) + \
           law_totals.get(field, 0) + \
           job_totals.get(field, 0)

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

def collect_districts(target, schema):
    district_names = target.get("districts", [])
    return [json_data["districts"].get(name, {}).get("modifiers", {}) for name in district_names]

def collect_jobs_assigned(target, schema):
    return {}  # TODO

def calculate_job_details(target, modifier_totals, district_totals, law_totals):
    job_details = json_data["jobs"]
    district_details = json_data["districts"]
    modifier_sources = [modifier_totals, district_totals, law_totals]
    district_types = [district_details.get(d, {}).get("type", "") for d in target.get("districts", [])]

    new_job_details = {}
    for job, details in job_details.items():
        if "requirements" not in details or \
           ("district" in details["requirements"] and details["requirements"]["district"] in district_types):
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

def sum_job_totals(jobs_assigned, job_details):
    totals = {}
    for job, count in jobs_assigned.items():
        for field, val in job_details.get(job, {}).items():
            totals[field] = totals.get(field, 0) + (val * count)
    return totals