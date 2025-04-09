import math
from app_core import category_data, json_data

def compute_field_effective_territory(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    administration = target.get("administration", 0)
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + (field_schema.get("effective_territory_per_admin", 0) * administration)
    
    return value

def compute_field_road_capacity(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    administration = target.get("administration", 0)
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + (field_schema.get("road_capacity_per_admin", 0) * administration)
    
    return value
    
def compute_field_karma(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    rolling_karma = int(target.get("rolling_karma", 0))
    temporary_karma = int(target.get("temporary_karma", 0))
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + rolling_karma + temporary_karma
    
    return value

def compute_disobey_chance(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.5
        case "Defiant":
            return 0.25
        case "Neutral":
            return 0.15
        case "Compliant":
            return 0.1
    
    return 0

def compute_rebellion_chance(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.25
        case "Defiant":
            return 0.15
        case "Neutral":
            return 0.05
    
    return 0

def compute_concessions_chance(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.5
        case "Defiant":
            return 0.4
        case "Neutral":
            return 0.3
        case "Compliant":
            return 0.2
        case "Loyal":
            return 0.1
    
    return 0

def compute_pop_count(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_database = category_data["pops"]["database"]
    target_id = str(target["_id"])
    
    pop_count = pop_database.count_documents({"nation": target_id})
    
    return pop_count

def compute_minority_count(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_database = category_data["pops"]["database"]
    target_id = str(target["_id"])
    
    minority_count = 0
    
    known_cultures = [target.get("primary_culture", "")]
    known_religions = [target.get("primary_religion", "")]
    
    relevant_pops = list(pop_database.find({"nation": target_id}))
    
    for pop in relevant_pops:
        if not pop.get("culture", "") in known_cultures:
            known_cultures.append(pop.get("culture", ""))
            minority_count += 1
        if not pop.get("religion", "") in known_religions:
            known_religions.append(pop.get("religion", ""))
            minority_count += 1
    
    return minority_count

def compute_district_slots(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_count = target.get("pop_count", 0)
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + math.floor(pop_count / 5)
    
    return value

def compute_unit_capacity(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_count = target.get("pop_count", 0)
    
    unit_cap_from_pops = math.ceil(pop_count * (modifier_totals.get("recruit_percentage", 0) + district_totals.get("recruit_percentage", 0) + law_totals.get("recruit_percentage", 0) + job_totals.get("recruit_percentage", 0)))
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + unit_cap_from_pops
    
    return value
    
def compute_resource_production(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    production_dict = {}
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]

    for resource in all_resources:
        specific_resource_production = 0
        modifiers_to_check = [resource["key"] + "_production", "resource_production"]
        for modifier in modifiers_to_check:
            specific_resource_production += modifier_totals.get(modifier, 0) + district_totals.get(modifier, 0) + law_totals.get(modifier, 0) + job_totals.get(modifier, 0)
        production_dict[resource["key"]] = specific_resource_production
    
    return production_dict

def compute_resource_consumption(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    consumption_dict = {}
    
    pop_count = target.get("pop_count", 0)
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        specific_resource_consumption = 0
        modifiers_to_check = [resource["key"] + "_consumption", "resource_consumption"]
        for modifier in modifiers_to_check:
            specific_resource_consumption += modifier_totals.get(modifier, 0) + district_totals.get(modifier, 0) + law_totals.get(modifier, 0) + job_totals.get(modifier, 0)
        
        if resource["key"] == "food":
            specific_resource_consumption += pop_count
        
        specific_resource_consumption = max(specific_resource_consumption, 0)
        
        consumption_dict[resource["key"]] = specific_resource_consumption
    
    return consumption_dict

def compute_resource_excess(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    excess_dict = {}
    
    production_dict = target.get("resource_production", {})
    consumption_dict = target.get("resource_consumption", {})
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        excess_dict[resource["key"]] = production_dict.get(resource["key"], 0) - consumption_dict.get(resource["key"], 0)
    
    return excess_dict

def compute_resource_storage_capacity(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    storage_dict = {}
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        specific_resource_storage = resource["base_storage"]
        modifiers_to_check = [resource["key"] + "_storage", "resource_storage"]
        for modifier in modifiers_to_check:
            specific_resource_storage += modifier_totals.get(modifier, 0) + district_totals.get(modifier, 0) + law_totals.get(modifier, 0) + job_totals.get(modifier, 0)
        storage_dict[resource["key"]] = specific_resource_storage
    
    return storage_dict

##############################################################

CUSTOM_COMPUTE_FUNCTIONS = {
    "effective_territory": compute_field_effective_territory,
    "road_capacity": compute_field_road_capacity,
    "karma": compute_field_karma,
    "disobey_chance": compute_disobey_chance,
    "rebellion_chance": compute_rebellion_chance,
    "concessions_chance": compute_concessions_chance,
    "pop_count": compute_pop_count,
    "unique_minority_count": compute_minority_count,
    "district_slots": compute_district_slots,
    "land_unit_capacity": compute_unit_capacity,
    "naval_unit_capacity": compute_unit_capacity,
    "resource_production": compute_resource_production,
    "resource_consumption": compute_resource_consumption,
    "resource_excess": compute_resource_excess,
    "resource_capacity": compute_resource_storage_capacity
}