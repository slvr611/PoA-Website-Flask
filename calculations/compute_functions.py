import math
from app_core import category_data, json_data, land_unit_json_files, naval_unit_json_files

def compute_prestige_gain(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value

    overlord = target.get("overlord", {})
    if overlord and overlord != "" and overlord != "None":
        value -= 15
    
    diplomatic_pacts = []
    
    diplomatic_pacts += list(category_data["diplo_relations"]["database"].find({"nation_1": str(target["_id"])}))
    diplomatic_pacts += list(category_data["diplo_relations"]["database"].find({"nation_2": str(target["_id"])}))

    pact_prestige_loss = 0
    for pact in diplomatic_pacts:
        if pact.get("pact_type", "") == "Defensive Pact":
            pact_prestige_loss += 2
        elif pact.get("pact_type", "") == "Military Alliance":
            pact_prestige_loss += 2
    
    print("Pact prestige loss: " + str(pact_prestige_loss))
    value -= min(pact_prestige_loss, 8)

    vassals = list(category_data["nations"]["database"].find({"overlord": str(target["_id"])}, {"_id": 1, "vassal_type": 1}))
    non_provincial_vassal_prestige_gain = 0
    provincial_vassal_prestige_gain = 0

    for vassal in vassals:
        if vassal.get("vassal_type", "") == "Provincial":
            provincial_vassal_prestige_gain += 1
        else:
            non_provincial_vassal_prestige_gain += 1
    
    print("Non-provincial vassal prestige gain: " + str(non_provincial_vassal_prestige_gain))
    print("Provincial vassal prestige gain: " + str(provincial_vassal_prestige_gain))
    value += min(non_provincial_vassal_prestige_gain, 2)
    value += min(provincial_vassal_prestige_gain, 2)

    artifacts = []
    rulers = list(category_data["characters"]["database"].find({"ruling_nation_org": str(target["_id"])}, {"_id": 1}))
    for ruler in rulers:
        artifacts += list(category_data["artifacts"]["database"].find({"owner": str(ruler["_id"])}, {"_id": 1, "rarity": 1}))
    legendary_artifact_prestige_gain = 0
    mythical_artifact_prestige_gain = 0

    for artifact in artifacts:
        if artifact.get("rarity", "") == "Legendary":
            legendary_artifact_prestige_gain += 1
        elif artifact.get("rarity", "") == "Mythical":
            mythical_artifact_prestige_gain += 2

    print("Legendary artifact prestige gain: " + str(legendary_artifact_prestige_gain))
    print("Mythical artifact prestige gain: " + str(mythical_artifact_prestige_gain))
    value += min(legendary_artifact_prestige_gain, 2)
    value += min(mythical_artifact_prestige_gain, 2)

    value += overall_total_modifiers.get(field, 0)

    return value

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
    
    value = 0

    match compliance:
        case "Rebellious":
            value = 0.5
        case "Defiant":
            value = 0.25
        case "Neutral":
            value = 0.15
        case "Compliant":
            value = 0.1

    value += overall_total_modifiers.get(field, 0)
    value = round(max(value, 0), 2)

    return value

def compute_rebellion_chance(field, target, base_value, field_schema, overall_total_modifiers):
    compliance = target.get("compliance", "None")
    
    value = 0

    match compliance:
        case "Rebellious":
            value = 0.25
        case "Defiant":
            value = 0.15 + overall_total_modifiers.get("rebellion_chance_above_rebellious", 0)
        case "Neutral":
            value = 0.05 + overall_total_modifiers.get("rebellion_chance_above_rebellious", 0)
    
    value += overall_total_modifiers.get(field, 0)
    value = round(max(value, 0), 2)

    return value

def compute_concessions_chance(field, target, base_value, field_schema, overall_total_modifiers):
    compliance = target.get("compliance", "None")
    
    value = 0

    match compliance:
        case "Rebellious":
            value = 0.5
        case "Defiant":
            value = 0.4
        case "Neutral":
            value = 0.3
        case "Compliant":
            value = 0.2
        case "Loyal":
            value = 0.1

    value += overall_total_modifiers.get(field, 0)
    value = round(max(value, 0), 2)

    return value

def compute_concessions_qty(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0)
    value *= overall_total_modifiers.get("concessions_qty_mult", 1)
    
    return value

def compute_working_pop_count(field, target, base_value, field_schema, overall_total_modifiers):
    workers_assigned = target.get("jobs", {})

    value = sum(workers_assigned.values())
    if value is None:
        value = 0

    return value

def compute_pop_count(field, target, base_value, field_schema, overall_total_modifiers):
    pop_database = category_data["pops"]["database"]
    target_id = str(target["_id"])
    
    pop_count = int(pop_database.count_documents({"nation": target_id}) + overall_total_modifiers.get(field, 0))
    
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

def compute_stability_gain_chance(field, target, base_value, field_schema, overall_total_modifiers):
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)

    karma_stability_gain = max(min(karma * overall_total_modifiers.get("stability_gain_chance_per_positive_karma", 0), overall_total_modifiers.get("max_stability_gain_chance_per_positive_karma", 0)), 0)
    minority_stability_gain = max(min(unique_minority_count * overall_total_modifiers.get("stability_gain_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_gain_chance_per_unique_minority", 0)), 0)

    if unique_minority_count == 0:
        minority_stability_gain += overall_total_modifiers.get("homogeneous_stability_gain_chance", 0)

    value = round(max(base_value + overall_total_modifiers.get(field, 0) + karma_stability_gain + minority_stability_gain, 0), 2)

    return value

def compute_stability_loss_chance(field, target, base_value, field_schema, overall_total_modifiers):
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    
    karma_stability_loss = max(min(-karma * overall_total_modifiers.get("stability_loss_chance_per_negative_karma", 0), overall_total_modifiers.get("max_stability_loss_chance_per_negative_karma", 0)), 0)
    minority_stability_loss = max(min(unique_minority_count * overall_total_modifiers.get("stability_loss_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_loss_chance_per_unique_minority", 0)), 0)

    if unique_minority_count == 0:
        minority_stability_loss += overall_total_modifiers.get("homogeneous_stability_loss_chance", 0)

    value = round(max(base_value + overall_total_modifiers.get(field, 0) + karma_stability_loss + minority_stability_loss, 0), 2)

    return value

def compute_district_slots(field, target, base_value, field_schema, overall_total_modifiers):
    pop_count = target.get("pop_count", 0)
    
    value = base_value + overall_total_modifiers.get(field, 0)  + math.floor(pop_count / 5)
    
    return value

def compute_unit_count(field, target, base_value, field_schema, overall_total_modifiers):
    unit_field = field.replace("_count", "s")
    units_assigned = target.get(unit_field, {})
    
    value = sum(units_assigned.values())
    if value is None:
        value = 0

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
        
        if resource["key"] == "research":
            pop_database = category_data["pops"]["database"]
            pops = pop_database.find({"nation": str(target["_id"])})
            religiously_homogeneous = True
            for pop in pops:
                if pop.get("religion", "") != target.get("primary_religion", ""):
                    religiously_homogeneous = False
                    break
            
            if religiously_homogeneous:
                specific_resource_production += overall_total_modifiers.get("research_production_if_religously_homogeneous", 0)

        specific_resource_production = max(specific_resource_production, 0)

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
            food_consumption_per_pop = 1 + overall_total_modifiers.get("food_consumption_per_pop", 0)
            food_consumption = pop_count * food_consumption_per_pop
            if food_consumption_per_pop < 1:
                food_consumption = math.ceil(food_consumption)
            else:
                food_consumption = math.floor(food_consumption)
            specific_resource_consumption += food_consumption
        
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

    value *= 1 + overall_total_modifiers.get("trade_slots_mult", 0)

    value = int(math.ceil(value))
    
    return value

def compute_export_slots(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)

    value = base_value

    value += overall_total_modifiers.get(field, 0)
    value += overall_total_modifiers.get("trade_slots", 0)
    value += overall_total_modifiers.get("trade_slots_per_admin", 0) * administration

    value *= (1 + overall_total_modifiers.get("trade_slots_mult", 0))
    value = int(math.ceil(value))
    
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

def compute_age_status(field, target, base_value, field_schema, overall_total_modifiers):
    age = target.get("age", 1)
    
    value = "Adult"
    if age < 1:
        value = "Child"
    elif age >= target.get("elderly_age", 3):
        value = "Elderly"

    return value

def compute_stat(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("stats", 0)
    ignore_elderly = overall_total_modifiers.get("ignore_elderly", 0) > 0

    age_status = target.get("age_status", "Adult")
    if age_status == "Child":
        value -= 1
    elif age_status == "Elderly" and not ignore_elderly:
        value -= 1
    
    return value

def compute_death_chance(field, target, base_value, field_schema, overall_total_modifiers):
    age = target.get("age", 1)
    elderly_age = target.get("elderly_age", 3)

    value = round(max((age - elderly_age) * 0.2, 0), 2)
    value += overall_total_modifiers.get(field, 0)

    return value

def compute_heal_chance(field, target, base_value, field_schema, overall_total_modifiers):
    prowess = target.get("prowess", 0)

    value = round(max(base_value + overall_total_modifiers.get(field, 0) + max(prowess * field_schema.get("heal_chance_per_prowess", 0), 0.1), 0), 2)

    return value

def compute_magic_point_income(field, target, base_value, field_schema, overall_total_modifiers):
    magic = target.get("magic", 0)

    value = max(base_value + magic + overall_total_modifiers.get(field, 0), 0)

    return value

def compute_magic_point_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    magic = target.get("magic", 0)

    value = max(base_value + overall_total_modifiers.get(field, 0) + (magic * field_schema.get("magic_point_capacity_per_magic", 0)), 0)

    return value

def compute_budget_spent(field, target, base_value, field_schema, overall_total_modifiers):
    value = overall_total_modifiers.get(field, 0)

    combined_json_data = {}
    for file_name in land_unit_json_files:
        combined_json_data.update(json_data[file_name])
    
    for file_name in naval_unit_json_files:
        combined_json_data.update(json_data[file_name])

    units = target.get("units", [])
    for unit in units:
        value += combined_json_data.get(unit, {}).get("recruitment_cost", {}).get("money", 0)
    
    print("Budget spent: " + str(value))
    
    return value

def compute_hiring_cost(field, target, base_value, field_schema, overall_total_modifiers):
    value = overall_total_modifiers.get(field, 0)
    
    value += target.get("upkeep", 0)
    
    value += target.get("budget_spent", 0)

    value *= 1 + overall_total_modifiers.get("hiring_cost_mult", 0)

    print("Hiring cost: " + str(value))

    return value

##############################################################

CUSTOM_COMPUTE_FUNCTIONS = {
    "prestige_gain": compute_prestige_gain,
    "effective_territory": compute_field_effective_territory,
    "road_capacity": compute_field_road_capacity,
    "karma": compute_field_karma,
    "disobey_chance": compute_disobey_chance,
    "rebellion_chance": compute_rebellion_chance,
    "concessions_chance": compute_concessions_chance,
    "concessions_qty": compute_concessions_qty,
    "working_pop_count": compute_working_pop_count,
    "pop_count": compute_pop_count,
    "unique_minority_count": compute_minority_count,
    "stability_gain_chance": compute_stability_gain_chance,
    "stability_loss_chance": compute_stability_loss_chance,
    "district_slots": compute_district_slots,
    "land_unit_count": compute_unit_count,
    "naval_unit_count": compute_unit_count,
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
    "age_status": compute_age_status,
    "rulership": compute_stat,
    "cunning": compute_stat,
    "charisma": compute_stat,
    "prowess": compute_stat,
    "magic": compute_stat,
    "strategy": compute_stat,
    "death_chance": compute_death_chance,
    "heal_chance": compute_heal_chance,
    "magic_point_income": compute_magic_point_income,
    "magic_point_capacity": compute_magic_point_capacity,
    "budget_spent": compute_budget_spent,
    "hiring_cost": compute_hiring_cost,

}