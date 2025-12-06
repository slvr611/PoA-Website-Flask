from copy import deepcopy
from typing import Dict, Any, Tuple, List

from dotenv import load_dotenv

from app_core import json_data, category_data
from calculations.field_calculations import calculate_all_fields

PRICE_MAP = {
    "money": 1,
    "food": 40,
    "wood": 50,
    "stone": 50,
    "mounts": 75,
    "magic": 100,
    "research": 150,
    "bronze": 75,
    "iron": 100,
}

def resources_to_money(resources: Dict[str, int]) -> int:
    """Convert a resource dict into total money value using the provided prices."""
    total_resource_values = {resource: amount * PRICE_MAP.get(resource, 0) for resource, amount in resources.items()}
    return sum(total_resource_values.values())


def compute_base_net_value(
    nation: Dict[str, Any]
) -> Tuple[int, int, Dict[str, Dict[str, Dict[str, int]]]]:
    """
    Calculate the net money value of a nation's production with no pops working jobs.
    Returns (base_value, pop_count, job_details).
    """
    nation_copy = deepcopy(nation)
    nation_copy["jobs"] = {}

    calculated = calculate_all_fields(
        nation_copy, category_data["nations"]["schema"], "nation"
    )
    resource_excess = calculated.get("resource_excess", {}) or {}
    resource_excess["research"] = calculated.get("resource_production", {}).get("research", 0)
    money_income = int(calculated.get("money_income", 0) or 0)
    base_value = money_income + resources_to_money(resource_excess)

    pop_count = int(calculated.get("pop_count", nation_copy.get("pop_count", 0)) or 0)
    job_details = calculated.get("job_details", {}) or {}
    return base_value, pop_count, job_details


def compute_job_net_value(
    job_key: str, job_details: Dict[str, Any]
) -> int | None:
    """Compute the net money value of a single job (production - upkeep)."""
    details = job_details.get(job_key)
    if not details:
        return None

    production = {k: v for k, v in (details.get("production", {}) or {}).items() if k in PRICE_MAP}
    upkeep = {k: v for k, v in (details.get("upkeep", {}) or {}).items() if k in PRICE_MAP}

    prod_value = resources_to_money(production)
    upkeep_value = resources_to_money(upkeep)
    return prod_value - upkeep_value


def find_most_efficient_job(
    job_details: Dict[str, Any]
) -> Tuple[str | None, int]:
    """Return the job with the highest net money value and that net value."""
    best_job = None
    best_value = None

    for job_key in job_details.keys():
        net_value = compute_job_net_value(job_key, job_details)
        if net_value is None:
            continue
        if best_value is None or net_value > best_value:
            best_value = net_value
            best_job = job_key

    return best_job, int(best_value or 0)


def main():
    load_dotenv(override=True)
    nations = list(category_data["nations"]["database"].find({}).sort("name", 1))

    results: List[Tuple[str, int, str]] = []
    for nation in nations:
        name = nation.get("name", "<Unnamed>")
        temperament = nation.get("temperament", "Unknown")
        nation["resource_storage"] = {"food": 1000}

        if temperament == "Player":
            print(f"Calculating {name}")
            base_value, pop_count, job_details = compute_base_net_value(nation)
            best_job, best_job_net = find_most_efficient_job(job_details)

            ndp = base_value + (best_job_net * pop_count)
            print(f"{name} - Base Value: {base_value}, Pop Count: {pop_count}, Best Job: {best_job}, Best Job Net: {best_job_net}, NDP: {ndp}")
            results.append((name, ndp, temperament))

    results.sort(key=lambda x: x[1], reverse=True)

    print("Nation Net Domestic Product (money equivalent):")
    for name, ndp, temperament in results:
        print(f"- {name}: {int(ndp)}")


if __name__ == "__main__":
    main()
