import math
from app_core import category_data, json_data, land_unit_json_files, naval_unit_json_files
from bson.objectid import ObjectId
import copy
from bisect import bisect_right

def compute_field(field, target, base_value, field_schema, overall_total_modifiers):
    compute_func = CUSTOM_COMPUTE_FUNCTIONS.get(field, compute_field_default)
    return compute_func(field, target, base_value, field_schema, overall_total_modifiers)

def compute_field_default(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0)
    if field_schema.get("format", None) != "percentage":
        value = int(value)
    return value

def compute_prestige_gain(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value

    #Lose 1 prestige per session since Strife started (geometric growth)
    current_session = category_data["global_modifiers"]["database"].find_one({"name": "global_modifiers"}).get("session_counter", 1)
    value -= 1 * (current_session - 55)
    #print(f"Session Prestige Decay: {value}")

    overlord = target.get("overlord", {})
    if overlord and overlord != "" and overlord != "None":
        value -= 15
        #print(f"Overlord Prestige Loss: {value}")
    
    diplomatic_pacts = []
    
    diplomatic_pacts += list(category_data["diplo_relations"]["database"].find({"nation_1": str(target.get("_id", ""))}))
    diplomatic_pacts += list(category_data["diplo_relations"]["database"].find({"nation_2": str(target.get("_id", ""))}))

    pact_prestige_loss = 0
    for pact in diplomatic_pacts:
        if pact.get("pact_type", "") == "Defensive Pact":
            pact_prestige_loss += 5
        elif pact.get("pact_type", "") == "Military Alliance":
            pact_prestige_loss += 5
    
    #print(f"Pact Prestige Loss: {pact_prestige_loss}")
    value -= min(pact_prestige_loss, 10)

    vassals = list(category_data["nations"]["database"].find({"overlord": str(target.get("_id", ""))}, {"_id": 1, "vassal_type": 1, "compliance": 1}))
    disloyal_vassal_prestige_loss = 0
    loyal_vassal_prestige_gain = 0

    for vassal in vassals:
        if vassal.get("compliance", "") == "Loyal":
            loyal_vassal_prestige_gain += 1
        elif vassal.get("compliance", "") == "Rebellious":
            disloyal_vassal_prestige_loss += 2
        elif vassal.get("compliance", "") == "Defiant":
            disloyal_vassal_prestige_loss += 2
    
    #print(f"Disloyal Vassal Prestige Loss: {disloyal_vassal_prestige_loss}")
    #print(f"Loyal Vassal Prestige Gain: {loyal_vassal_prestige_gain}")
    value -= min(disloyal_vassal_prestige_loss, 10)
    value += min(loyal_vassal_prestige_gain, 3)

    artifacts = []
    rulers = list(category_data["characters"]["database"].find({"ruling_nation_org": str(target.get("_id", ""))}, {"_id": 1}))
    for ruler in rulers:
        artifacts += list(category_data["artifacts"]["database"].find({"owner": str(ruler["_id"]), "equipped": True}, {"_id": 1, "rarity": 1}))
    mythical_artifact_prestige_gain = 0

    for artifact in artifacts:
        if artifact.get("rarity", "") == "Mythical":
            mythical_artifact_prestige_gain += 1

    #print(f"Mythical Artifact Prestige Gain: {mythical_artifact_prestige_gain}")
    value += min(mythical_artifact_prestige_gain, 1)

    value += overall_total_modifiers.get(field, 0)

    return int(value)

def compute_administration(field, target, base_value, field_schema, overall_total_modifiers):
    production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)
    research_production = production.get("research", 0)

    value = base_value + overall_total_modifiers.get(field, 0)

    admin_per_research = overall_total_modifiers.get("administration_per_research_production", 0)
    if admin_per_research > 0:
        value += research_production // admin_per_research
    
    max_admin_per_research = overall_total_modifiers.get("max_administration_per_research_production", 0)
    if max_admin_per_research > 0:
        max_admin = base_value + (research_production // max_admin_per_research)
        value = min(value, max_admin)
    
    return int(value)

def compute_field_effective_territory(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)
    
    value = base_value + overall_total_modifiers.get(field, 0) + (field_schema.get("effective_territory_per_admin", 0) * administration)
    
    return int(value)

def compute_field_current_territory(field, target, base_value, field_schema, overall_total_modifiers):
    territory_types = copy.deepcopy(target.get("territory_types", {}))

    for terrain, value in territory_types.items():
        effective_territory_multiplier = overall_total_modifiers.get(terrain + "_effective_territory_multiplier", 1) * overall_total_modifiers.get("tile_effective_territory_multiplier", 1)
        territory_types[terrain] = int(math.ceil(value * effective_territory_multiplier))

    value = sum(territory_types.values())
    
    return int(value)

def compute_field_road_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)

    value = base_value + overall_total_modifiers.get(field, 0) + (field_schema.get("road_capacity_per_admin", 0) * administration)
    
    return int(value)
    
def compute_field_karma(field, target, base_value, field_schema, overall_total_modifiers):
    rolling_karma = int(target.get("rolling_karma", 0))
    temporary_karma = int(target.get("temporary_karma", 0))
    
    value = base_value + overall_total_modifiers.get(field, 0) + rolling_karma + temporary_karma
    
    return int(value)

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
    
    return int(value)

def compute_working_pop_count(field, target, base_value, field_schema, overall_total_modifiers):
    workers_assigned = target.get("jobs", {})

    if not workers_assigned:
        return 0

    value = sum(workers_assigned.values())
    if value is None:
        value = 0

    return int(value)

def compute_pop_count(field, target, base_value, field_schema, overall_total_modifiers):
    pop_database = category_data["pops"]["database"]
    target_id = str(target.get("_id", ""))
    
    pop_count = int(pop_database.count_documents({"nation": target_id}) + overall_total_modifiers.get(field, 0))
    
    return int(pop_count)

def compute_minority_count(field, target, base_value, field_schema, overall_total_modifiers):
    pop_database = category_data["pops"]["database"]
    target_id = str(target.get("_id", ""))
    
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
    
    minority_count += overall_total_modifiers.get(field, 0)
    
    return int(minority_count)

def compute_stability_gain_chance(field, target, base_value, field_schema, overall_total_modifiers):
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    minority_impact = 1 + overall_total_modifiers.get("minority_impact", 0)
    pop_count = target.get("pop_count", 0)
    road_usage = target.get("road_usage", 0)
    war = False
    territory_types = target.get("territory_types", {})

    production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)

    total_production = sum(production.values())

    stability_gain_chance_from_resource_production = overall_total_modifiers.get("stability_gain_chance_per_resource_production", 0) * total_production

    karma_stability_gain = max(min(karma * overall_total_modifiers.get("stability_gain_chance_per_positive_karma", 0), overall_total_modifiers.get("max_stability_gain_chance_per_positive_karma", 0)), 0)
    minority_stability_gain = max(min(unique_minority_count * minority_impact * overall_total_modifiers.get("stability_gain_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_gain_chance_per_unique_minority", 0)), 0)
    pop_stability_gain = pop_count * overall_total_modifiers.get("stability_gain_chance_per_pop", 0)
    road_stability_gain = int(road_usage) * overall_total_modifiers.get("stability_gain_chance_per_road_usage", 0)
    war_stability_gain = 0
    if war:
        war_stability_gain = overall_total_modifiers.get("stability_gain_chance_while_at_war", 0)
    else:
        war_stability_gain = overall_total_modifiers.get("stability_gain_chance_while_at_peace", 0)

    if unique_minority_count == 0:
        minority_stability_gain += overall_total_modifiers.get("homogeneous_stability_gain_chance", 0)

    foreign_religious_pop_stability_gain = 0

    if overall_total_modifiers.get("stability_gain_chance_per_foreign_religious_pop", 0) != 0:
        pop_database = category_data["pops"]["database"]
        religion_id = str(target.get("primary_religion", ""))
        nation_id = str(target.get("_id", ""))
        
        foreign_religious_pop_count = pop_database.count_documents({"nation": {"$ne": nation_id}, "religion": religion_id})
        
        foreign_religious_pop_stability_gain = foreign_religious_pop_count * overall_total_modifiers.get("stability_gain_chance_per_foreign_religious_pop", 0)
        foreign_religious_pop_stability_gain = max(foreign_religious_pop_stability_gain, overall_total_modifiers.get("max_stability_gain_chance_per_foreign_religious_pop", 0))
    
    terrain_stability_gain = 0

    for terrain, terrain_count in territory_types.items():
        if overall_total_modifiers.get("stability_gain_chance_per_tile", 0) != 0 or overall_total_modifiers.get("stability_gain_chance_per_" + terrain, 0) != 0:
            terrain_stability_gain += (overall_total_modifiers.get("stability_gain_chance_per_tile", 0) + overall_total_modifiers.get("stability_gain_chance_per_" + terrain, 0)) * terrain_count
    
    value = round(min(max(base_value + overall_total_modifiers.get(field, 0) + karma_stability_gain + minority_stability_gain + pop_stability_gain + stability_gain_chance_from_resource_production + road_stability_gain + war_stability_gain + foreign_religious_pop_stability_gain + terrain_stability_gain, 0), 1), 2)

    return value

def compute_stability_loss_chance(field, target, base_value, field_schema, overall_total_modifiers):
    karma = target.get("karma", 0)
    unique_minority_count = target.get("unique_minority_count", 0)
    minority_impact = 1 + overall_total_modifiers.get("minority_impact", 0)
    pop_count = target.get("pop_count", 0)
    stability = target.get("stability", "Unknown")
    road_usage = target.get("road_usage", 0)
    war = False

    karma_stability_loss = max(min(-karma * overall_total_modifiers.get("stability_loss_chance_per_negative_karma", 0), overall_total_modifiers.get("max_stability_loss_chance_per_negative_karma", 0)), 0)
    minority_stability_loss = max(min(unique_minority_count * minority_impact * overall_total_modifiers.get("stability_loss_chance_per_unique_minority", 0), overall_total_modifiers.get("max_stability_loss_chance_per_unique_minority", 0)), 0)
    pop_stability_loss = pop_count * overall_total_modifiers.get("stability_loss_chance_per_pop", 0)
    road_stability_loss = int(road_usage) * overall_total_modifiers.get("stability_loss_chance_per_road_usage", 0)
    war_stability_loss = 0
    if war:
        war_stability_loss = overall_total_modifiers.get("stability_loss_chance_while_at_war", 0)
    else:
        war_stability_loss = overall_total_modifiers.get("stability_loss_chance_while_at_peace", 0)

    if unique_minority_count == 0:
        minority_stability_loss += overall_total_modifiers.get("homogeneous_stability_loss_chance", 0)

    stab_loss_chance_from_stability = 0
    if stability == "United":
        stab_loss_chance_from_stability = overall_total_modifiers.get("stability_loss_chance_at_united", 0)
    elif stability == "Stable":
        stab_loss_chance_from_stability = overall_total_modifiers.get("stability_loss_chance_at_stable", 0)
    
    stability_loss_chance_per_bloodthirsty_pop = overall_total_modifiers.get("stability_loss_chance_per_bloodthirsty_pop", 0)
    if stability_loss_chance_per_bloodthirsty_pop > 0:
        pop_database = category_data["pops"]["database"]
        target_id = str(target.get("_id", ""))
        
        pops = pop_database.find({"nation": target_id})
        pop_races = [pop.get("race", "") for pop in pops]
        bloodthirsty_pop_count = 0
        pop_races_set = list(set(copy.deepcopy(pop_races)))
        pop_race_ids = [ObjectId(race) for race in pop_races_set]
        races = category_data["races"]["database"].find({"_id": {"$in": pop_race_ids}}, {"_id": 1, "negative_trait": 1})
        race_dict = {str(race["_id"]): race["negative_trait"] for race in races}
        for race in pop_races:
            if race_dict.get(race, "") == "Bloodthirsty":
                bloodthirsty_pop_count += 1
        
        pop_stability_loss += bloodthirsty_pop_count * stability_loss_chance_per_bloodthirsty_pop

    value = round(min(max(base_value + overall_total_modifiers.get(field, 0) + karma_stability_loss + minority_stability_loss + pop_stability_loss + stab_loss_chance_from_stability + road_stability_loss + war_stability_loss, 0), 2), 2)

    return value

def compute_passive_expansion_chance(field, target, base_value, field_schema, overall_total_modifiers):
    current_territory = target.get("current_territory", 0)
    effective_territory = target.get("effective_territory", 0)
    value = base_value + overall_total_modifiers.get(field, 0)
    if current_territory > effective_territory:
        value += overall_total_modifiers.get("passive_expansion_chance_above_effective_territory", 0)
    value = min(value, overall_total_modifiers.get("max_passive_expansion_chance", 1))
    value = round(max(value, 0), 2)

    return value

def compute_district_slots(field, target, base_value, field_schema, overall_total_modifiers):
    pop_count = target.get("pop_count", 0)

    pop_count -= overall_total_modifiers.get("district_pop_requirement", 0)

    district_slot_pop_requirements = json_data["district_slot_pop_requirements"]
    overcap_pops_per_district_slot = json_data["overcap_pops_per_district_slot"]

    if pop_count > district_slot_pop_requirements[len(district_slot_pop_requirements) - 1]:
        pop_count -= district_slot_pop_requirements[len(district_slot_pop_requirements) - 1]
        pop_value = len(district_slot_pop_requirements) + math.floor(pop_count / overcap_pops_per_district_slot)
    else:
        pop_value = bisect_right(district_slot_pop_requirements, pop_count)
    
    value = base_value + overall_total_modifiers.get(field, 0) + pop_value
    
    return int(value)

def compute_unit_count(field, target, base_value, field_schema, overall_total_modifiers):
    unit_field = field.replace("_count", "s")
    units_assigned = target.get(unit_field, {})

    if not units_assigned:
        return 0
    
    value = sum(units_assigned.values())
    if value is None:
        value = 0

    return int(value)

def compute_unit_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    pop_count = target.get("pop_count", 0)
    
    unit_cap_from_pops = math.ceil(pop_count * (overall_total_modifiers.get("recruit_percentage", 0)))
    
    value = base_value + overall_total_modifiers.get(field, 0) + unit_cap_from_pops
    
    return int(value)

def compute_money_income(field, target, base_value, field_schema, overall_total_modifiers):
    pop_count = target.get("pop_count", 0)

    value = base_value + overall_total_modifiers.get(field, 0) + (pop_count * overall_total_modifiers.get("money_income_per_pop", 0))

    money_stockpile = target.get("money", 0)
    money_income_per_money_storage = overall_total_modifiers.get("money_income_per_money_storage", 0)
    max_money_income_per_stockpile = overall_total_modifiers.get("max_money_income_per_money_storage", 0)
    if money_income_per_money_storage > 0:
        value += min((money_stockpile // money_income_per_money_storage) * 100, max_money_income_per_stockpile)  #money_income_per_stockpile gives $100 per x amount in stockpile
    
    return int(value)
    
def compute_resource_production(field, target, base_value, field_schema, overall_total_modifiers):
    production_dict = {}

    production_of_available_nodes = overall_total_modifiers.get("production_of_available_nodes", 0)  #TODO:  Figure out how to calculate which nodes the nation has for this modifier
    ignore_nodes = target.get("ignore_nodes", 0)
    resource_node_value = overall_total_modifiers.get("resource_node_value", 0)
    naval_unit_count = 0
    if "naval_unit_count" in target:
        naval_unit_count = compute_unit_count("naval_unit_count", target, 0, {}, overall_total_modifiers)

    all_resources = json_data["general_resources"] + json_data["unique_resources"]

    for resource in all_resources:
        specific_resource_production = 0
        specific_resource_node_value = resource_node_value + overall_total_modifiers.get(resource["key"] + "_node_value", 0)
        modifiers_to_check = [resource["key"] + "_production", "resource_production"]
        for modifier in modifiers_to_check:
            specific_resource_production += overall_total_modifiers.get(modifier, 0)

        if ignore_nodes < 1:
            resource_nodes = overall_total_modifiers.get(resource["key"] + "_nodes", 0)
            specific_resource_production += resource_nodes * (2 + specific_resource_node_value)
        

        if overall_total_modifiers.get(resource["key"] + "_production_per_naval_unit", 0) > 0:
            specific_resource_production += int(math.floor(naval_unit_count * overall_total_modifiers.get(resource["key"] + "_production_per_naval_unit", 0)))
        
        if resource["key"] == "research":
            pop_database = category_data["pops"]["database"]
            pops = pop_database.find({"nation": str(target.get("_id", ""))})
            religiously_homogeneous = True
            for pop in pops:
                if pop.get("religion", "") != target.get("primary_religion", ""):
                    religiously_homogeneous = False
                    break
            
            if religiously_homogeneous:
                specific_resource_production += overall_total_modifiers.get("research_production_if_religously_homogeneous", 0)

        specific_resource_production = max(specific_resource_production, 0)

        if overall_total_modifiers.get("locks_" + resource["key"] + "_production", 0) > 0:
            specific_resource_production = 0

        production_dict[resource["key"]] = int(specific_resource_production)
    
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
        elif resource["key"] == "research":
            technologies = target.get("technologies", {})
            for tech, details in technologies.items():
                specific_resource_consumption += details.get("investing", 0)
        
        specific_resource_consumption = max(specific_resource_consumption, 0)
        
        consumption_dict[resource["key"]] = int(specific_resource_consumption)
    
    return consumption_dict

def compute_resource_excess(field, target, base_value, field_schema, overall_total_modifiers):
    excess_dict = {}
    
    production_dict = target.get("resource_production", {})
    consumption_dict = target.get("resource_consumption", {})
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        excess_dict[resource["key"]] = int(production_dict.get(resource["key"], 0) - consumption_dict.get(resource["key"], 0))
    
    return excess_dict

def compute_nation_resource_storage_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    storage_dict = {}
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        specific_resource_storage = resource["base_storage"]
        specific_resource_storage += overall_total_modifiers.get(resource["key"] + "_storage_capacity", 0)
        if specific_resource_storage > 0:
            specific_resource_storage += overall_total_modifiers.get("resource_storage_capacity", 0)
        storage_dict[resource["key"]] = int(specific_resource_storage)
    
    return storage_dict

def compute_market_resource_storage_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    storage_dict = {}
    
    all_resources = json_data["general_resources"] + json_data["unique_resources"]
    
    for resource in all_resources:
        specific_resource_storage = 0
        specific_resource_storage += overall_total_modifiers.get(resource["key"] + "_storage_capacity", 0)
        specific_resource_storage += overall_total_modifiers.get("resource_storage_capacity", 0)
        storage_dict[resource["key"]] = int(specific_resource_storage)
    
    return storage_dict

def compute_import_slots(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)

    value = base_value

    value += overall_total_modifiers.get(field, 0)
    value += overall_total_modifiers.get("trade_slots", 0)
    value += overall_total_modifiers.get("trade_slots_per_admin", 0) * administration

    value *= 1 + overall_total_modifiers.get("trade_slots_mult", 0)

    value = math.ceil(value)
    
    return int(value)

def compute_export_slots(field, target, base_value, field_schema, overall_total_modifiers):
    administration = target.get("administration", 0)

    value = base_value

    value += overall_total_modifiers.get(field, 0)
    value += overall_total_modifiers.get("trade_slots", 0)
    value += overall_total_modifiers.get("trade_slots_per_admin", 0) * administration

    value *= (1 + overall_total_modifiers.get("trade_slots_mult", 0))
    value = math.ceil(value)
    
    return int(value)

def compute_remaining_import_slots(field, target, base_value, field_schema, overall_total_modifiers):
    import_slots = target.get("import_slots", 0)

    value = import_slots
    
    return int(value)

def compute_remaining_export_slots(field, target, base_value, field_schema, overall_total_modifiers):
    export_slots = target.get("export_slots", 0)

    value = export_slots
    
    return int(value)

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
    
    return int(value)

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
    
    return int(value)

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
    
    return int(value)

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
    
    return int(value)


def compute_mercenary_land_attack(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_land_strength", 0) + overall_total_modifiers.get("mercenary_attack", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return int(value)

def compute_mercenary_land_defense(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_land_strength", 0) + overall_total_modifiers.get("mercenary_defense", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return int(value)
def compute_mercenary_naval_attack(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_naval_strength", 0) + overall_total_modifiers.get("mercenary_attack", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return int(value)

def compute_mercenary_naval_defense(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("mercenary_naval_strength", 0) + overall_total_modifiers.get("mercenary_defense", 0) + overall_total_modifiers.get("mercenary_strength", 0)
    
    return int(value)

##############################################################

def compute_age_status(field, target, base_value, field_schema, overall_total_modifiers):
    age = target.get("age", 1)
    
    value = "Adult"
    if age < 1:
        value = "Child"
    elif age >= target.get("elderly_age", 3):
        if overall_total_modifiers.get("ignore_elderly", 0) > 0:
            value = "Adult"
        else:
            value = "Elderly"

    return value

def compute_stat_cap(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("stat_cap", 0)

    return int(value)

def compute_stat(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("stats", 0)
    ignore_elderly = overall_total_modifiers.get("ignore_elderly", 0) > 0
    ignore_elderly_strengths = overall_total_modifiers.get("ignore_elderly_strengths", 0) > 0
    cap = target.get(field + "_cap", 6)
    strengths = target.get("strengths", [])

    age_status = target.get("age_status", "Adult")
    if age_status == "Child":
        value -= 1
    elif age_status == "Elderly" and not ignore_elderly and not (ignore_elderly_strengths and field in strengths):
        value -= 1
    
    value = min(value, cap)
    
    return int(value)

def compute_death_chance(field, target, base_value, field_schema, overall_total_modifiers):
    age = target.get("age", 1)
    elderly_age = target.get("elderly_age", 3)

    value = round(max((age - elderly_age) * (0.2 + overall_total_modifiers.get("death_chance_per_elderly_age", 0)), 0), 2)

    if overall_total_modifiers.get("elderly_death_start_early", 0) > 0 and age >= elderly_age:
        value += 0.2 + overall_total_modifiers.get("death_chance_per_elderly_age", 0)

    cunning = compute_stat("cunning", target, 0, {}, overall_total_modifiers)
    value += cunning * overall_total_modifiers.get("death_chance_per_cunning", 0)

    value += overall_total_modifiers.get(field, 0)

    value *= overall_total_modifiers.get("death_chance_multiplier", 1)

    minimum_death_chance = overall_total_modifiers.get("minimum_death_chance", 0)

    minimum_death_chance += overall_total_modifiers.get("minimum_death_chance_per_cunning", 0) * cunning

    value = max(value, minimum_death_chance)

    return value

def compute_heal_chance(field, target, base_value, field_schema, overall_total_modifiers):
    prowess = target.get("prowess", 0)

    minimum_heal_chance = 0
    minimum_heal_chance += overall_total_modifiers.get("minimum_heal_chance_per_prowess", 0) * prowess + overall_total_modifiers.get("minimum_heal_chance", 0)

    value = round(max(base_value + overall_total_modifiers.get(field, 0) + max(prowess * field_schema.get("heal_chance_per_prowess", 0), 0.1), minimum_heal_chance), 2)

    return value

def compute_stat_gain_chance(field, target, base_value, field_schema, overall_total_modifiers):
    BASE_STAT_GAIN_CHANCE_PER_CUNNING = 0.1
    value = base_value + overall_total_modifiers.get(field, 0)

    cunning = compute_stat("cunning", target, 0, {}, overall_total_modifiers)
    value += cunning * (BASE_STAT_GAIN_CHANCE_PER_CUNNING + overall_total_modifiers.get("stat_gain_chance_per_cunning", 0))

    if compute_field_default("elderly_age", target, 3, {}, overall_total_modifiers) > 500:
        value += overall_total_modifiers.get("immortal_stat_gain_chance", 0)

    value = round(max(min(value, 1), 0), 2)

    return value

def compute_magic_point_income(field, target, base_value, field_schema, overall_total_modifiers):
    magic = target.get("magic", 0)

    value = max(base_value + magic + overall_total_modifiers.get(field, 0), 0)

    return int(value)

def compute_magic_point_capacity(field, target, base_value, field_schema, overall_total_modifiers):
    magic = target.get("magic", 0)

    value = max(base_value + overall_total_modifiers.get(field, 0) + (magic * field_schema.get("magic_point_capacity_per_magic", 0)), 0)

    return int(value)

def compute_used_artifact_slots(field, target, base_value, field_schema, overall_total_modifiers):
    artifacts = category_data["artifacts"]["database"].find({"owner": str(target.get("_id", ""))})
    value = 0
    for artifact in artifacts:
        value += artifact.get("artifact_slot_usage", 1)
    
    return int(value)

def compute_artifact_loss_chance(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0)

    artifact_slots = target.get("artifact_slots", 12)
    used_artifact_slots = target.get("used_artifact_slots", compute_used_artifact_slots("used_artifact_slots", target, None, None, overall_total_modifiers))
    artifact_loss_chance_per_slot = target.get("artifact_loss_chance_per_slot", 0.02)

    value += max(0, (used_artifact_slots - artifact_slots) * artifact_loss_chance_per_slot)

    value = round(min(max(value, 0), 1), 2)
    
    return value

def compute_budget(field, target, base_value, field_schema, overall_total_modifiers):
    value = base_value + overall_total_modifiers.get(field, 0) + overall_total_modifiers.get("budget", 0)

    return int(value)

def compute_budget_spent(field, target, base_value, field_schema, overall_total_modifiers):
    value = overall_total_modifiers.get(field, 0)

    combined_json_data = {}
    for file_name in land_unit_json_files:
        combined_json_data.update(json_data[file_name])
    
    for file_name in naval_unit_json_files:
        combined_json_data.update(json_data[file_name])

    units = []
    if field == "land_budget_spent":
        units = target.get("land_units", [])
    elif field == "naval_budget_spent":
        units = target.get("naval_units", [])
    else:
        return value

    for unit in units:
        value += combined_json_data.get(unit, {}).get("recruitment_cost", {}).get("money", 0)
    
    return int(value)

def compute_hiring_cost(field, target, base_value, field_schema, overall_total_modifiers):
    value = overall_total_modifiers.get(field, 0)
    
    value += target.get("upkeep", 0)
    
    value += target.get("land_budget_spent", 0) + target.get("naval_budget_spent", 0)

    value *= 1 + overall_total_modifiers.get("hiring_cost_mult", 0)

    value = value / 5

    value = int(round(value, 0))

    value *= 5

    return int(value)

def compute_progress_per_session(field, target, base_value, field_schema, overall_total_modifiers):
    """Calculate progress per session for each progress quest"""
    from app_core import json_data
    
    progress_quests = target.get("progress_quests", [])
    
    # Update each quest with its calculated progress per session
    for quest in progress_quests:
        slot = quest.get("slot", "no_slot")
        bonus_progress = quest.get("bonus_progress_per_tick", 0)
        
        # Get progress from slot type using json_data
        slot_progress = json_data.get("slot_types", {}).get(slot, {}).get("progress_per_tick", 0)
        
        if slot != "no_slot":
            # Calculate total progress per session
            total_progress = slot_progress + bonus_progress
        else:
            total_progress = 0
        
        # Store the calculated value in the quest
        quest["total_progress_per_tick"] = max(int(total_progress), 0)
    
    return progress_quests

##############################################################

CUSTOM_COMPUTE_FUNCTIONS = {
    "prestige_gain": compute_prestige_gain,
    "administration": compute_administration,
    "effective_territory": compute_field_effective_territory,
    "current_territory": compute_field_current_territory,
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
    "passive_expansion_chance": compute_passive_expansion_chance,
    "district_slots": compute_district_slots,
    "land_unit_count": compute_unit_count,
    "naval_unit_count": compute_unit_count,
    "land_unit_capacity": compute_unit_capacity,
    "naval_unit_capacity": compute_unit_capacity,
    "money_income": compute_money_income,
    "resource_production": compute_resource_production,
    "resource_consumption": compute_resource_consumption,
    "resource_excess": compute_resource_excess,
    "nation_resource_capacity": compute_nation_resource_storage_capacity,
    "market_resource_capacity": compute_market_resource_storage_capacity,
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
    "rulership_cap": compute_stat_cap,
    "cunning_cap": compute_stat_cap,
    "charisma_cap": compute_stat_cap,
    "prowess_cap": compute_stat_cap,
    "magic_cap": compute_stat_cap,
    "strategy_cap": compute_stat_cap,
    "rulership": compute_stat,
    "cunning": compute_stat,
    "charisma": compute_stat,
    "prowess": compute_stat,
    "magic": compute_stat,
    "strategy": compute_stat,
    "death_chance": compute_death_chance,
    "heal_chance": compute_heal_chance,
    "stat_gain_chance": compute_stat_gain_chance,
    "magic_point_income": compute_magic_point_income,
    "magic_point_capacity": compute_magic_point_capacity,
    "used_artifact_slots": compute_used_artifact_slots,
    "artifact_loss_chance": compute_artifact_loss_chance,
    "land_budget": compute_budget,
    "naval_budget": compute_budget,
    "land_budget_spent": compute_budget_spent,
    "naval_budget_spent": compute_budget_spent,
    "hiring_cost": compute_hiring_cost,
    "progress_quests": compute_progress_per_session,
    "progress_per_session": compute_progress_per_session,
}
