import math
from app_core import category_data, json_data

def compute_field_effective_territory(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)
    
    value = base_value + overall_total_modifiers.get(field, 0) + (field_schema.get("effective_territory_per_admin", 0) * administration)
    
    return value

def compute_field_road_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)
    
    value = base_value + overall_total_modifiers.get(field, 0) + (field_schema.get("road_capacity_per_admin", 0) * administration)
    
    return value
    
def compute_field_karma(field, target, base_value, field_schema, overall_total_modifiers):
    rolling_karma = int(target.get("rolling_karma", 0))
    temporary_karma = int(target.get("temporary_karma", 0))
    
    value = base_value + overall_total_modifiers.get(field, 0) + rolling_karma + temporary_karma
    
    return value

def compute_disobey_chance(field, target, base_value, field_schema, overall_total_modifiers):
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

def compute_rebellion_chance(field, target, base_value, field_schema, overall_total_modifiers):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.25
        case "Defiant":
            return 0.15
        case "Neutral":
            return 0.05
    
    return 0

def compute_concessions_chance(field, target, base_value, field_schema, overall_total_modifiers):
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

def compute_pop_count(field, target, base_value, field_schema, overall_total_modifiers):
    pop_database = category_data["pops"]["database"]
    target_id = str(target["_id"])
    
    pop_count = pop_database.count_documents({"nation": target_id})
    
    return pop_count

def compute_minority_count(field, target, base_value, field_schema, overall_total_modifiers):
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

def compute_district_slots(field, target, base_value, field_schema, overall_total_modifiers):
    pop_count = target.get("pop_count", 0)
    
    value = base_value + overall_total_modifiers.get(field, 0)  + math.floor(pop_count / 5)
    
    return value

def compute_unit_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    pop_count = target.get("pop_count", 0)
    
    unit_cap_from_pops = math.ceil(pop_count * (overall_total_modifiers.get("recruit_percentage", 0)))
    
    value = base_value + overall_total_modifiers.get(field, 0) + unit_cap_from_pops
    
    return value
    
def compute_resource_production(field, target, base_value, field_schema, overall_total_modifiers):
    production_dict = {}
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]

    for resource in all_resources:
        specific_resource_production = 0
        modifiers_to_check = [resource["key"] + "_production", "resource_production"]
        for modifier in modifiers_to_check:
            specific_resource_production += overall_total_modifiers.get(modifier, 0)
        production_dict[resource["key"]] = specific_resource_production
    
    return production_dict

def compute_resource_consumption(field, target, base_value, field_schema, overall_total_modifiers):
    consumption_dict = {}
    
    pop_count = target.get("pop_count", 0)
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        specific_resource_consumption = 0
        modifiers_to_check = [resource["key"] + "_consumption", "resource_consumption"]
        for modifier in modifiers_to_check:
            specific_resource_consumption += overall_total_modifiers.get(modifier, 0)
        
        if resource["key"] == "food":
            specific_resource_consumption += pop_count
        
        specific_resource_consumption = max(specific_resource_consumption, 0)
        
        consumption_dict[resource["key"]] = specific_resource_consumption
    
    return consumption_dict

def compute_resource_excess(field, target, base_value, field_schema, overall_total_modifiers):
    excess_dict = {}
    
    production_dict = target.get("resource_production", {})
    consumption_dict = target.get("resource_consumption", {})
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        excess_dict[resource["key"]] = production_dict.get(resource["key"], 0) - consumption_dict.get(resource["key"], 0)
    
    return excess_dict

def compute_resource_storage_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    storage_dict = {}
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        specific_resource_storage = resource["base_storage"]
        modifiers_to_check = [resource["key"] + "_storage", "resource_storage"]
        for modifier in modifiers_to_check:
            specific_resource_storage += overall_total_modifiers.get(modifier, 0)
        storage_dict[resource["key"]] = specific_resource_storage
    
    return storage_dict

def compute_import_slots(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)

    value = base_value

    value += overall_total_modifiers.get(field, 0)
    value += overall_total_modifiers.get("trade_slots", 0)
    value += overall_total_modifiers.get("trade_slots_per_admin", 0) * administration
    
    return value

def compute_export_slots(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)

    value = base_value

    value += overall_total_modifiers.get(field, 0)
    value += overall_total_modifiers.get("trade_slots", 0)
    value += overall_total_modifiers.get("trade_slots_per_admin", 0) * administration
    
    return value

def compute_remaining_import_slots(field, target, base_value, field_schema, overall_total_modifiers):
    import_slots = target.get("import_slots", 0)

    value = import_slots
    
    return value

def compute_remaining_export_slots(field, target, base_value, field_schema, overall_total_modifiers):
    export_slots = target.get("export_slots", 0)

    value = export_slots
    
    return value

def compute_land_attack(field, target, base_value, field_schema, overall_total_modifiers):
    stability = target.get("stability", "Uknown")
    high_stability = False
    low_stability = False

    if stability == "United" or stability == "Stable":
        high_stability = True
    elif stability == "Fragile" or stability == "Unsettled":
        low_stability = True

    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("land_strength", 0) + overall_total_modifiers.get("attack", 0) + overall_total_modifiers.get("strength", 0)

    if high_stability:
        value += overall_total_modifiers.get("high_stability_strength", 0)
    if low_stability:
        value += overall_total_modifiers.get("low_stability_strength", 0)
    
    return value

def compute_land_defense(field, target, base_value, field_schema, overall_total_modifiers):
    stability = target.get("stability", "Uknown")
    high_stability = False
    low_stability = False

    if stability == "United" or stability == "Stable":
        high_stability = True
    elif stability == "Fragile" or stability == "Unsettled":
        low_stability = True

    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("land_strength", 0) + overall_total_modifiers.get("defense", 0) + overall_total_modifiers.get("strength", 0)

    if high_stability:
        value += overall_total_modifiers.get("high_stability_strength", 0)
    if low_stability:
        value += overall_total_modifiers.get("low_stability_strength", 0)
    
    return value

def compute_naval_attack(field, target, base_value, field_schema, overall_total_modifiers):
    stability = target.get("stability", "Uknown")
    high_stability = False
    low_stability = False

    if stability == "United" or stability == "Stable":
        high_stability = True
    elif stability == "Fragile" or stability == "Unsettled":
        low_stability = True

    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("naval_strength", 0) + overall_total_modifiers.get("attack", 0) + overall_total_modifiers.get("strength", 0)

    if high_stability:
        value += overall_total_modifiers.get("high_stability_strength", 0)
    if low_stability:
        value += overall_total_modifiers.get("low_stability_strength", 0)
    
    return value

def compute_naval_defense(field, target, base_value, field_schema, overall_total_modifiers):
    stability = target.get("stability", "Uknown")
    high_stability = False
    low_stability = False

    if stability == "United" or stability == "Stable":
        high_stability = True
    elif stability == "Fragile" or stability == "Unsettled":
        low_stability = True

    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("naval_strength", 0) + overall_total_modifiers.get("defense", 0) + overall_total_modifiers.get("strength", 0)

    if high_stability:
        value += overall_total_modifiers.get("high_stability_strength", 0)
    if low_stability:
        value += overall_total_modifiers.get("low_stability_strength", 0)
    
    return value


def compute_mercenary_land_attack(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_land_strength", 0) + overall_total_modifiers.get("mercenary_attack", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return value

def compute_mercenary_land_defense(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_land_strength", 0) + overall_total_modifiers.get("mercenary_defense", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return value
def compute_mercenary_naval_attack(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_naval_strength", 0) + overall_total_modifiers.get("mercenary_attack", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return value

def compute_mercenary_naval_defense(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_naval_strength", 0) + overall_total_modifiers.get("mercenary_defense", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return value


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
    "resource_capacity": compute_resource_storage_capacity,
    "export_slots": compute_export_slots,
    "import_slots": compute_import_slots,
    "remaining_export_slots": compute_remaining_export_slots,
    "remaining_import_slots": compute_remaining_import_slots,
    "land_attack": compute_land_attack,
    "land_defense": compute_land_defense,
    "naval_attack": compute_naval_attack,
    "naval_defense": compute_naval_defense,
    "mercenary_land_attack": compute_mercenary_land_attack,
    "mercenary_land_defense": compute_mercenary_land_defense,
    "mercenary_naval_attack": compute_mercenary_naval_attack,
    "mercenary_naval_defense": compute_mercenary_naval_defense,
}