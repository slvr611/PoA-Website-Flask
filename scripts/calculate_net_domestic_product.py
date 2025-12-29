from copy import deepcopy
from typing import Dict, Any, Tuple, List

from dotenv import load_dotenv

from app_core import json_data, category_data
from calculations.field_calculations import calculate_all_fields
from calculate_infrastructure_score import compute_infrastructure_average

PRICES = {
    "money": 1.0,
    "food": 50,
    "wood": 75,
    "stone": 75,
    "mounts": 75,
    "magic": 100,
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
        if resource == "research":
            cap = 10
        if resource == "money":
            cap = float("inf")

        if amount > 0:
            usable = max(min(cap - current, amount), 0)
            overflow = amount - usable
            total += usable * price
            total += overflow * price * 0.5
        else:
            total += amount * price

        stock[resource] = current + amount
    return total


def value_and_update_stock_with_breakdown(
    resources: Dict[str, float],
    stock: Dict[str, float],
    capacity: Dict[str, float],
) -> Tuple[float, Dict[str, float]]:
    total = 0.0
    breakdown: Dict[str, float] = {}
    for resource, amount in resources.items():
        if resource not in PRICES or amount == 0:
            continue
        price = PRICES[resource]
        current = float(stock.get(resource, 0) or 0)
        cap = float(capacity.get(resource, float("inf")) or float("inf"))
        if resource == "research":
            cap = 10
        if resource == "money":
            cap = float("inf")

        if amount > 0:
            usable = max(min(cap - current, amount), 0)
            overflow = amount - usable
            value = usable * price + overflow * price * 0.5
        else:
            value = amount * price

        total += value
        breakdown[resource] = breakdown.get(resource, 0.0) + value
        stock[resource] = current + amount
    return total, breakdown


def compute_base_net_value(
    nation: Dict[str, Any]
) -> Tuple[
    float,
    int,
    int,
    Dict[str, Dict[str, Dict[str, float]]],
    Dict[str, float],
    Dict[str, float],
    Dict[str, float],
    Dict[str, float],
    float,
    float,
    float,
    int,
]:
    """
    Calculate the net money value of a nation's production with no pops working jobs.
    Returns (base_value, pop_count, job_details, capacity, stock).
    """
    nation_copy = deepcopy(nation)
    nation_copy["jobs"] = {}
    modifiers = nation_copy.get("modifiers", [])
    if isinstance(modifiers, list):
        nation_copy["modifiers"] = [
            modifier
            for modifier in modifiers
            if isinstance(modifier, dict)
            and (modifier.get("duration", -1) == -1 or modifier.get("duration", 0) >= 3)
        ]
    technologies = nation_copy.get("technologies", {})
    if isinstance(technologies, dict):
        for details in technologies.values():
            if isinstance(details, dict):
                details["investing"] = 0
    elif isinstance(technologies, list):
        for details in technologies:
            if isinstance(details, dict):
                details["investing"] = 0
    storage = deepcopy(nation_copy.get("resource_storage", {}) or {})
    storage["food"] = 1_000_000_000
    nation_copy["resource_storage"] = storage

    calculated = calculate_all_fields(
        nation_copy, category_data["nations"]["schema"], "nation"
    )
    resource_excess = calculated.get("resource_excess", {}) or {}
    money_income = float(calculated.get("money_income", 0) or 0)

    stock = deepcopy(calculated.get("resource_storage", {}) or {})
    capacity = deepcopy(calculated.get("nation_resource_capacity", {}) or {})

    base_resource_value, base_resource_breakdown = value_and_update_stock_with_breakdown(
        resource_excess, stock, capacity
    )
    base_value = money_income + base_resource_value

    pop_count = int(calculated.get("pop_count", nation_copy.get("pop_count", 0)) or 0)
    administration = int(calculated.get("administration", nation_copy.get("administration", 0)) or 0)
    job_details = calculated.get("job_details", {}) or {}
    job_stock = {}
    land_attack = float(calculated.get("land_attack", nation_copy.get("land_attack", 0)) or 0)
    land_defense = float(calculated.get("land_defense", nation_copy.get("land_defense", 0)) or 0)
    land_unit_capacity = int(calculated.get("land_unit_capacity", nation_copy.get("land_unit_capacity", 0)) or 0)

    return (
        base_value,
        pop_count,
        administration,
        job_details,
        capacity,
        stock,
        job_stock,
        base_resource_breakdown,
        money_income,
        land_attack,
        land_defense,
        land_unit_capacity,
    )


def evaluate_job_marginal_value(
    job_key: str,
    job_details: Dict[str, Any],
    stock: Dict[str, float],
    capacity: Dict[str, float],
    vassal_rate_total: int,
    assignments: Dict[str, int],
    base_administration: int,
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
    if job_key == "bureaucrat" and vassal_rate_total:
        current_count = assignments.get("bureaucrat", 0)
        current_total = base_administration + current_count
        current_effective = apply_admin_diminishing_returns(current_total)
        next_effective = apply_admin_diminishing_returns(current_total + 1)
        value += (next_effective - current_effective) * vassal_rate_total

    return value, temp_stock


def simulate_optimal_assignments(
    pop_count: int,
    job_details: Dict[str, Any],
    capacity: Dict[str, float],
    stock: Dict[str, float],
    vassal_rate_total: int,
    base_administration: int,
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
            value, new_stock = evaluate_job_marginal_value(
                job_key,
                job_details,
                current_stock,
                capacity,
                vassal_rate_total,
                assignments,
                base_administration,
            )
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


def count_vassals(nation: Dict[str, Any]) -> int:
    db = category_data["nations"]["database"]
    overlord_id = str(nation.get("_id", "") or "")
    if not overlord_id:
        return 0
    return db.count_documents({"overlord": overlord_id})


def sum_vassal_type_rates(nation: Dict[str, Any]) -> int:
    vassal_type_rates = {
        "Martial": 0,
        "Mercantile": 100,
        "Protectorate": 0,
        "Provincial": 125,
        "Tributary": 125,
        "Enclave": 0,
    }
    db = category_data["nations"]["database"]
    overlord_id = str(nation.get("_id", "") or "")
    if not overlord_id:
        return 0
    vassals = db.find({"overlord": overlord_id}, {"vassal_type": 1})
    return sum(vassal_type_rates.get(vassal.get("vassal_type", ""), 0) for vassal in vassals)


def sum_vassal_military_bonus(nation: Dict[str, Any]) -> int:
    vassal_type_rates = {
        "Martial": 7,
        "Mercantile": 4,
        "Protectorate": 5,
        "Provincial": 6,
        "Tributary": 0,
        "Enclave": 0,
    }
    db = category_data["nations"]["database"]
    overlord_id = nation.get("_id")
    overlord_id_str = str(overlord_id or "")
    if not overlord_id_str:
        return 0
    vassals = db.find(
        {"overlord": {"$in": [overlord_id, overlord_id_str]}},
        {"vassal_type": 1, "land_unit_capacity": 1},
    )
    total = 0
    for vassal in vassals:
        rate = vassal_type_rates.get(vassal.get("vassal_type", ""), 0)
        capacity = int(vassal.get("land_unit_capacity", 0) or 0)
        total += rate * capacity
    return total


def apply_admin_diminishing_returns(administration: int) -> float:
    if administration <= 10:
        return float(administration)
    return 10 + (administration - 10) * 0.5


def count_wonders(nation: Dict[str, Any]) -> int:
    db = category_data["wonders"]["database"]
    nation_id = nation.get("_id")
    nation_id_str = str(nation_id or "")
    return db.count_documents({"owner_nation": {"$in": [nation_id, nation_id_str]}})


def main():
    load_dotenv(override=True)

    nations = list(category_data["nations"]["database"].find({}).sort("name", 1))

    results: List[Tuple[str, float, int, int, int, float, str]] = []
    for nation in nations:
        name = nation.get("name", "<Unnamed>")
        temperament = nation.get("temperament", "Unknown")

        if temperament != "Player":
            continue
        print(f"Calculating {name}")
        (
            base_value,
            pop_count,
            administration,
            job_details,
            capacity,
            _stock,
            job_stock,
            base_resource_breakdown,
            money_income,
            land_attack,
            land_defense,
            land_unit_capacity,
        ) = compute_base_net_value(nation)
        vassal_rate_total = sum_vassal_type_rates(nation)
        marginal_value, _assignments = simulate_optimal_assignments(
            pop_count, job_details, capacity, job_stock, vassal_rate_total, administration
        )

        ndp = base_value + marginal_value
        vassal_count = count_vassals(nation)
        effective_admin = apply_admin_diminishing_returns(administration)
        admin_bonus = effective_admin * vassal_rate_total
        ndp += admin_bonus
        print(
            f"Breakdown {name}: base={base_value:.2f}, marginal={marginal_value:.2f}, "
            f"admin_bonus={admin_bonus:.2f}, total={ndp:.2f}"
        )
        if base_resource_breakdown:
            resource_parts = ", ".join(
                f"{resource}={value:.2f}" for resource, value in sorted(base_resource_breakdown.items())
            )
            print(f"Base resources {name}: money={money_income:.2f}, {resource_parts}")
        else:
            print(f"Base resources {name}: money={money_income:.2f}")
        military_score = (10 + land_attack + land_defense) * land_unit_capacity
        military_score += sum_vassal_military_bonus(nation)
        wonder_count = count_wonders(nation)
        infrastructure_score, _details = compute_infrastructure_average(nation)
        results.append(
            (
                name,
                ndp,
                pop_count,
                vassal_count,
                wonder_count,
                infrastructure_score,
                military_score,
                temperament,
            )
        )

    results.sort(key=lambda x: x[1], reverse=True)

    print("Nation Net Domestic Product (money equivalent):")
    for (
        name,
        ndp,
        pop_count,
        vassal_count,
        wonder_count,
        infrastructure_score,
        military_score,
        temperament,
    ) in results:
        if temperament == "Player":
            print(
                f"{name}, {ndp:.2f}, {pop_count}, {vassal_count}, {wonder_count}, "
                f"{infrastructure_score:.2f}, {military_score:.2f}"
            )


if __name__ == "__main__":
    main()
