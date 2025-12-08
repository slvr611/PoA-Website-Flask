from copy import deepcopy
from typing import Dict, Any, Tuple, List

from dotenv import load_dotenv

from app_core import json_data, category_data
from calculations.field_calculations import calculate_all_fields


PRICES = {
    "money": 1.0,
    "food": 50,
    "wood": 75,
    "stone": 75,
    "mounts": 75,
    "magic": 130,
    "research": 200,
    "bronze": 125,
    "iron": 150,
}

def value_and_update_stock(
    resources: Dict[str, float],
    stock: Dict[str, float],
    capacity: Dict[str, float],
) -> float:
    """
    Convert resource changes into money value, applying reduced value (50%)
    for production that exceeds storage capacity. Updates stock in-place.
    """
    total = 0.0
    for resource, amount in resources.items():
        if resource not in PRICES or amount == 0:
            continue
        price = PRICES[resource]
        current = float(stock.get(resource, 0) or 0)
        cap = float(capacity.get(resource, float("inf")) or float("inf"))

        if amount > 0:
            usable = max(min(cap - current, amount), 0)
            overflow = amount - usable
            total += usable * price
            total += overflow * price * 0.5
        else:
            total += amount * price

        stock[resource] = current + amount
    return total


def compute_base_net_value(
    nation: Dict[str, Any]
) -> Tuple[float, int, Dict[str, Dict[str, Dict[str, float]]], Dict[str, float], Dict[str, float]]:
    """
    Calculate the net money value of a nation's production with no pops working jobs.
    Returns (base_value, pop_count, job_details, capacity, stock).
    """
    nation_copy = deepcopy(nation)
    nation_copy["jobs"] = {}

    calculated = calculate_all_fields(
        nation_copy, category_data["nations"]["schema"], "nation"
    )
    resource_excess = calculated.get("resource_excess", {}) or {}
    money_income = float(calculated.get("money_income", 0) or 0)

    stock = deepcopy(calculated.get("resource_storage", {}) or {})
    capacity = deepcopy(calculated.get("nation_resource_capacity", {}) or {})

    base_value = money_income + value_and_update_stock(resource_excess, stock, capacity)

    pop_count = int(calculated.get("pop_count", nation_copy.get("pop_count", 0)) or 0)
    job_details = calculated.get("job_details", {}) or {}
    return base_value, pop_count, job_details, capacity, stock


def evaluate_job_marginal_value(
    job_key: str,
    job_details: Dict[str, Any],
    stock: Dict[str, float],
    capacity: Dict[str, float],
) -> Tuple[float, Dict[str, float]]:
    """
    Evaluate the marginal money value of assigning one pop to the job,
    applying storage capacity diminishing returns. Returns (value, new_stock).
    """
    details = job_details.get(job_key)
    if not details:
        return float("-inf"), stock

    production = {k: v for k, v in (details.get("production", {}) or {}).items() if k in PRICES}
    upkeep = {k: v for k, v in (details.get("upkeep", {}) or {}).items() if k in PRICES}

    temp_stock = deepcopy(stock)
    value = 0.0
    value += value_and_update_stock(production, temp_stock, capacity)
    if upkeep:
        negative_upkeep = {k: -v for k, v in upkeep.items()}
        value += value_and_update_stock(negative_upkeep, temp_stock, capacity)

    return value, temp_stock


def simulate_optimal_assignments(
    pop_count: int,
    job_details: Dict[str, Any],
    capacity: Dict[str, float],
    stock: Dict[str, float],
) -> Tuple[float, Dict[str, int]]:
    """
    Greedy simulation: repeatedly pick the best job given current stock/capacity,
    assign one pop, update stock, and re-evaluate until all pops are placed.
    Returns (total_value, assignments).
    """
    total_value = 0.0
    assignments: Dict[str, int] = {}
    current_stock = deepcopy(stock)

    for _ in range(pop_count):
        best_job = None
        best_value = float("-inf")
        best_stock_after = None

        for job_key in job_details.keys():
            value, new_stock = evaluate_job_marginal_value(job_key, job_details, current_stock, capacity)
            if value > best_value:
                best_value = value
                best_job = job_key
                best_stock_after = new_stock

        if best_job is None or best_stock_after is None:
            break

        total_value += best_value
        current_stock = best_stock_after
        assignments[best_job] = assignments.get(best_job, 0) + 1
        print(f"Assigned 1 pop to {best_job}, total value: {total_value:.2f}")
    print(f"Total value: {total_value:.2f}")
    print(f"Assignments: {assignments}")

    return total_value, assignments


def main():
    load_dotenv(override=True)

    nations = list(category_data["nations"]["database"].find({}).sort("name", 1))

    results: List[Tuple[str, float, str]] = []
    for nation in nations:
        name = nation.get("name", "<Unnamed>")
        temperament = nation.get("temperament", "Unknown")

        if temperament != "Player":
            continue
        print(f"Calculating {name}")
        base_value, pop_count, job_details, capacity, stock = compute_base_net_value(nation)
        marginal_value, _assignments = simulate_optimal_assignments(pop_count, job_details, capacity, stock)

        ndp = base_value + marginal_value
        results.append((name, ndp, temperament))

    results.sort(key=lambda x: x[1], reverse=True)

    print("Nation Net Domestic Product (money equivalent):")
    for name, ndp, temperament in results:
        if temperament == "Player":
            print(f"- {name}: {ndp:.2f}")


if __name__ == "__main__":
    main()
