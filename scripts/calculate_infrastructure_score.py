import os
from typing import List, Dict, Any, Tuple

from dotenv import load_dotenv
from pymongo import MongoClient
from urllib.parse import urlparse
from app_core import json_data, category_data

# -----------------------------
# Scoring configuration
# -----------------------------
# Guideline constants (change if needed)
EMPTY_SLOT_SCORE = 0
ANCIENT_DISTRICT_WITHOUT_NODE_SCORE = 5
CLASSICAL_DISTRICT_WITHOUT_NODE_SCORE = 6
NODE_SCORE = 1
SYNERGY_SCORE = 1
GENERIC_CITY_SCORE = 6
SPECIALIZED_CITY_SCORE = 7
WONDER_SCORE = 7

def score_districts(nation: Dict[str, Any]) -> Tuple[int, int]:
    """
    Scores district slots.

    - Empty district slots => 0 (EMPTY_SLOT_SCORE)
    - District object with no node => 5 (DISTRICT_WITHOUT_NODE_SCORE)
    - District object with a node => 10 (DISTRICT_WITH_NODE_SCORE)

    Returns (total_score, total_items_counted)
    """
    districts: List[Dict[str, Any]] = nation.get("districts", []) or []
    district_slots: int = nation.get("district_slots", 0) or 0

    # If slots are missing, fall back to the number of listed districts
    if not isinstance(district_slots, int) or district_slots <= 0:
        district_slots = len(districts)

    score_total = 0
    counted = 0

    # Score filled district entries
    for d in districts:
        node = d.get("node") if isinstance(d, dict) else None
        if not d.get("type") or d.get("type") == "":
            score_total += EMPTY_SLOT_SCORE
        elif "classical" in d.get("type"):
            score_total += CLASSICAL_DISTRICT_WITHOUT_NODE_SCORE
        else:
            score_total += ANCIENT_DISTRICT_WITHOUT_NODE_SCORE
        if node:
            score_total += NODE_SCORE
        synergy_requirements = json_data["nation_districts"].get(d.get("type"), {}).get("synergy_requirement", [])
        if isinstance(synergy_requirements, str):
            synergy_requirements = [synergy_requirements]
        if node and ("any" in synergy_requirements or node in synergy_requirements):
            score_total += SYNERGY_SCORE
        counted += 1

    # Add empty slots up to district_slots
    empty_slots = max(district_slots - len(districts), 0)
    score_total += empty_slots * EMPTY_SLOT_SCORE
    counted += empty_slots

    return score_total, counted


def score_cities(nation: Dict[str, Any]) -> Tuple[int, int]:
    """
    Scores city slots.

    - Empty city slots => 0 (EMPTY_SLOT_SCORE)
    - City with no nodes => 5 (CITY_WITHOUT_NODES_SCORE)
    - City with nodes => 10 (CITY_WITH_NODES_SCORE)

    Returns (total_score, total_items_counted)
    """
    cities: List[Dict[str, Any]] = nation.get("cities", []) or []
    city_slots: int = nation.get("city_slots", 0) or 0

    # If slots are missing, fall back to the number of listed cities
    if not isinstance(city_slots, int) or city_slots <= 0:
        city_slots = len(cities)

    score_total = 0
    counted = 0

    for c in cities:
        node = None
        if isinstance(c, dict):
            node = c.get("node") or None
        if node:
            score_total += NODE_SCORE
        if c.get("type") == "generic":
            score_total += GENERIC_CITY_SCORE
        else:
            score_total += SPECIALIZED_CITY_SCORE
        counted += 1

    empty_slots = max(city_slots - len(cities), 0)
    score_total += empty_slots * EMPTY_SLOT_SCORE
    counted += empty_slots

    return score_total, counted


def score_wonders(nation: Dict[str, Any]) -> Tuple[int, int]:
    """
    Each wonder is always worth 7 (WONDER_SCORE).

    Returns (total_score, total_items_counted)
    """
    db = category_data["wonders"]["database"]
    wonder_count = db.count_documents({"owner_nation": nation["_id"]})
    return wonder_count * WONDER_SCORE, wonder_count

def score_modifiers(nation: Dict[str, Any]) -> int:
    """
    Scores modifiers.

    - If a modifier contains the string 'nodes', add 1.
    """

    score = 0
    for modifier in nation.get("modifiers", []):
        if "nodes" in modifier.get("field", ""):
            score += modifier.get("value", 0)
    return score

def compute_infrastructure_average(nation: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Computes the average score across all district slots, city slots, and wonders.

    Returns (average, details_dict)
    details_dict contains breakdowns and counts.
    """
    d_score, d_count = score_districts(nation)
    c_score, c_count = score_cities(nation)
    w_score, w_count = score_wonders(nation)
    m_score = score_modifiers(nation)

    total_score = d_score + c_score + w_score + m_score
    total_count = d_count + c_count + w_count

    avg = (total_score / total_count) if total_count > 0 else 0.0

    details = {
        "districts": {"score": d_score, "count": d_count},
        "cities": {"score": c_score, "count": c_count},
        "wonders": {"score": w_score, "count": w_count},
        "total_score": total_score,
        "total_count": total_count,
        "average": avg,
    }
    return avg, details


def main():
    # Pull nations with relevant fields only
    projection = {
        "_id": 1,
        "name": 1,
        "districts": 1,
        "district_slots": 1,
        "cities": 1,
        "city_slots": 1,
        "modifiers": 1,
        "temperament": 1,
    }
    db = category_data["nations"]["database"]
    nations = list(db.find({}, projection).sort("name", 1))

    results: List[Tuple[str, float, Dict[str, Any]]] = []
    for nation in nations:
        name = nation.get("name", "<Unnamed>")
        avg, details = compute_infrastructure_average(nation)
        results.append((name, avg, details, nation.get("temperament", "Unknown")))

    # Sort by average descending
    results.sort(key=lambda x: x[1], reverse=True)

    # Print per-nation results
    print("Nation Infrastructure Averages (including empty slots and wonders):")
    for name, avg, details, temperament in results:
        if temperament == "Player":
            print(
                f"- {name}: {avg:.2f}"
            )

if __name__ == "__main__":
    main()

