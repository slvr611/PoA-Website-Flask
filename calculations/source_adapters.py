"""
Source adapters — each adapter collects SourceContribution objects from one
type of game-data source.  These run in parallel with the existing *_totals
pipeline; they are not yet wired into calculate_all_fields.  Once validated
against existing output they can replace the per-source totals dicts.
"""

import copy
from app_core import json_data
from calculations.source_contribution import SourceContribution
from calculations.field_calculations import (
    collect_laws,
    collect_nation_districts,
    calculate_district_details,
    collect_cities,
    collect_jobs_assigned,
    calculate_job_details,
    sum_job_totals,
)


def _resolve_modifier_type(modifier: dict) -> str:
    """Resolve a modifier document to its effective field name."""
    modifier_types_data = json_data.get("modifier_types", {})
    mod_type = modifier.get("modifier_type", "")
    if mod_type and mod_type in modifier_types_data:
        type_def = modifier_types_data[mod_type]
        field = type_def.get("field_template", mod_type)
        for extra in type_def.get("extra_fields", []):
            key = extra["key"]
            val = modifier.get(key) or ""
            field = field.replace(f"{{{key}}}", val)
        return field
    return modifier.get("field", modifier.get("key", mod_type))


def _modifiers_list_to_dict(modifiers: list) -> dict:
    totals = {}
    for m in modifiers:
        field = _resolve_modifier_type(m)
        value = m.get("value", 0)
        if field:
            totals[field] = totals.get(field, 0) + value
    return totals


# ---------------------------------------------------------------------------
# Per-source adapters
# ---------------------------------------------------------------------------

class LawAdapter:
    source_type = "law"

    @classmethod
    def collect(cls, target: dict, schema: dict) -> list:
        contributions = []
        laws_list = schema.get("laws", [])
        for law_name in laws_list:
            current_law_list = schema["properties"].get(law_name, {}).get("laws", {})
            target_law = target.get(law_name, "")
            law = current_law_list.get(target_law)
            if law:
                contributions.append(SourceContribution(
                    label=target_law,
                    source_type=cls.source_type,
                    modifiers=dict(law),
                ))
        return contributions


class DistrictAdapter:
    source_type = "district"

    @classmethod
    def collect(cls, target: dict, schema: dict, modifier_totals: dict, law_totals: dict, external_totals: dict) -> list:
        contributions = []
        district_details = calculate_district_details(target, schema.get("properties", {}), modifier_totals, law_totals, external_totals)

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

        for district in target.get("districts", []):
            if not isinstance(district, dict):
                continue
            district_type = district.get("type", "")
            if not district_type:
                continue
            district_node = district.get("node", "")
            dd = district_details.get(district_type, {})
            label = dd.get("display_name", district_type)
            mods = dict(dd.get("modifiers", {}))
            node_bonus_applied = False
            for syn in get_synergies(dd):
                if synergy_matches(district_node, syn.get("requirement", "")):
                    mods.update(syn.get("modifiers", {}))
                    if syn.get("node_active", True) and district_node and not node_bonus_applied:
                        mods[district_node + "_nodes"] = mods.get(district_node + "_nodes", 0) + 1
                        node_bonus_applied = True
            if not node_bonus_applied and district_node:
                mods[district_node + "_nodes"] = mods.get(district_node + "_nodes", 0) + 1
            if mods:
                contributions.append(SourceContribution(label=label, source_type=cls.source_type, modifiers=mods))
        return contributions


class TechnologyAdapter:
    source_type = "technology"

    @classmethod
    def collect(cls, target: dict) -> list:
        contributions = []
        tech_data = json_data.get("tech", {})
        for tech, value in target.get("technologies", {}).items():
            if value.get("researched", False):
                mods = dict(tech_data.get(tech, {}).get("modifiers", {}))
                if mods:
                    name = tech_data.get(tech, {}).get("name", tech)
                    contributions.append(SourceContribution(label=name, source_type=cls.source_type, modifiers=mods))
        return contributions


class CityAdapter:
    source_type = "city"

    @classmethod
    def collect(cls, target: dict) -> list:
        contributions = []
        city_json = json_data.get("cities", {})
        wall_json = json_data.get("walls", {})
        for city in target.get("cities", []):
            city_type = city.get("type", "")
            city_node = city.get("node", "")
            city_label = city_json.get(city_type, {}).get("display_name", city_type)
            mods = dict(city_json.get(city_type, {}).get("modifiers", {}))
            wall_mods = dict(wall_json.get(city.get("wall", ""), {}).get("modifiers", {}))
            for k, v in wall_mods.items():
                mods[k] = mods.get(k, 0) + v
            if city_node:
                mods[city_node + "_nodes"] = mods.get(city_node + "_nodes", 0) + 1
            if mods:
                contributions.append(SourceContribution(label=city_label, source_type=cls.source_type, modifiers=mods))
        return contributions


class TitleAdapter:
    source_type = "title"

    @classmethod
    def collect(cls, target: dict, target_data_type: str, schema_properties: dict) -> list:
        from calculations.field_calculations import calculate_title_modifiers
        contributions = []
        title_data = copy.deepcopy(json_data.get("positive_titles", {}))
        title_data.update(json_data.get("negative_titles", {}))
        for title_key in target.get("positive_titles", []) + target.get("negative_titles", []):
            mods = calculate_title_modifiers([title_key], target_data_type, schema_properties)
            if mods:
                label = title_data.get(title_key, {}).get("name", title_key)
                contributions.append(SourceContribution(label=label, source_type=cls.source_type, modifiers=mods))
        return contributions


class ModifierAdapter:
    """Custom admin-defined modifiers stored on the entity's modifiers[] array."""
    source_type = "modifier"

    @classmethod
    def collect(cls, target: dict, target_data_type: str = None) -> list:
        contributions = []
        scope_defs = json_data.get("scope_definitions", {})
        for m in target.get("modifiers", []):
            scope = m.get("scope", "")
            if target_data_type and scope:
                target_type = scope_defs.get(scope, {}).get("target_type", target_data_type)
                if target_type != target_data_type:
                    continue
            field = _resolve_modifier_type(m)
            value = m.get("value", 0)
            source_label = m.get("source", "Custom Modifier")
            if field:
                contributions.append(SourceContribution(
                    label=source_label,
                    source_type=cls.source_type,
                    modifiers={field: value},
                ))
        return contributions


class UnitAdapter:
    """Contributions from assigned units (land, naval, support)."""
    source_type = "unit"

    @classmethod
    def collect(cls, target: dict, unit_type: str, unit_details: dict) -> list:
        contributions = []
        key = f"{unit_type}_units"
        assigned = target.get(key, {})
        unit_json = json_data.get("units", {})

        for unit_key, count in assigned.items():
            if count <= 0:
                continue
            details = unit_details.get(unit_key, unit_json.get(unit_key, {}))
            mods = {}
            for stat in details.get("modifiers", {}):
                mods[stat] = mods.get(stat, 0) + details["modifiers"][stat] * count
            label = details.get("display_name", unit_key)
            if mods:
                contributions.append(SourceContribution(label=label, source_type=cls.source_type, modifiers=mods))
        return contributions


class JobAdapter:
    """Contributions from assigned jobs."""
    source_type = "job"

    @classmethod
    def collect(cls, target: dict, job_details: dict) -> list:
        contributions = []
        jobs_assigned = target.get("jobs", {})
        for job_key, count in jobs_assigned.items():
            if count <= 0:
                continue
            details = job_details.get(job_key, {})
            production = details.get("production", {})
            upkeep = details.get("upkeep", {})
            mods = {}
            for resource, value in production.items():
                mods[resource + "_production"] = mods.get(resource + "_production", 0) + int(value * count)
            for resource, value in upkeep.items():
                mods[resource + "_upkeep"] = mods.get(resource + "_upkeep", 0) + int(value * count)
            label = details.get("display_name", job_key)
            if mods:
                contributions.append(SourceContribution(label=label, source_type=cls.source_type, modifiers=mods))
        return contributions


class ExternalModifierAdapter:
    """Legacy external_modifiers arrays (cross-entity effects)."""
    source_type = "external"

    @classmethod
    def collect(cls, target: dict, target_data_type: str) -> list:
        contributions = []
        for em in target.get("external_modifiers", []):
            field = em.get("modifier", "")
            em_type = em.get("type", "")
            value = em.get("value", 0)
            if em_type == target_data_type and field:
                contributions.append(SourceContribution(
                    label=em.get("source", "External"),
                    source_type=cls.source_type,
                    modifiers={field: value},
                ))
        return contributions


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def collect_all_contributions(target: dict, target_data_type: str, schema: dict,
                               modifier_totals: dict = None, law_totals: dict = None,
                               external_totals: dict = None,
                               unit_details: dict = None) -> list:
    """
    Gather every SourceContribution for a target entity.
    modifier_totals / law_totals / external_totals are pre-computed dicts
    needed by DistrictAdapter; pass them if already available.
    """
    modifier_totals = modifier_totals or {}
    law_totals = law_totals or {}
    external_totals = external_totals or {}
    unit_details = unit_details or {}
    contributions = []

    contributions.extend(ModifierAdapter.collect(target, target_data_type))
    contributions.extend(LawAdapter.collect(target, schema))
    if target_data_type in ("nation", "nation_jobs"):
        contributions.extend(DistrictAdapter.collect(target, schema, modifier_totals, law_totals, external_totals))
        contributions.extend(TechnologyAdapter.collect(target))
        contributions.extend(CityAdapter.collect(target))
    contributions.extend(TitleAdapter.collect(target, target_data_type, schema.get("properties", {})))
    contributions.extend(ExternalModifierAdapter.collect(target, target_data_type))

    return contributions


def sum_contributions(contributions: list) -> dict:
    """Collapse contributions into a single totals dict."""
    totals = {}
    for c in contributions:
        for field, value in c.modifiers.items():
            totals[field] = totals.get(field, 0) + value
    return totals


def build_field_breakdown(field: str, contributions: list) -> list:
    """
    Return all contributions that affect a given field, for tooltip display.
    Replaces _build_unit_tagged_sources and compute_nation_breakdowns for
    the field-level breakdown path.
    """
    return [
        {"label": c.label, "source_type": c.source_type, "value": c.modifiers[field]}
        for c in contributions
        if field in c.modifiers
    ]
