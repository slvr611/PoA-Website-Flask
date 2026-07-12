import os
import sys
import datetime
from copy import deepcopy
from typing import Dict, Any, Tuple, List

from dotenv import load_dotenv
from bson import ObjectId

from app_core import json_data, category_data, mongo
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

def get_nation_prices(nation: Dict[str, Any]) -> Dict[str, float]:
    """
    Resource prices to use for valuing this nation's production: overlays the
    dynamic resource_prices of any market(s) the nation belongs to on top of
    the PRICES baseline above. Only overrides resources this script actually
    prices (PRICES' keys) — market data for other resources is ignored. A
    nation in multiple markets uses the highest price found for each resource.
    Nations not in any market (or with no market price for a resource) fall
    back to the static PRICES values unchanged.
    """
    prices = dict(PRICES)
    nation_id = str(nation.get("_id", "") or "")
    if not nation_id:
        return prices
    try:
        market_links_db = category_data["market_links"]["database"]
        my_links = list(market_links_db.find({"member": nation_id}, {"market": 1}))
        market_prices: Dict[str, float] = {}
        for link in my_links:
            market_id = link.get("market")
            if not market_id:
                continue
            market_doc = mongo.db.markets.find_one(
                {"_id": ObjectId(market_id)}, {"resource_prices": 1}
            )
            if not market_doc:
                continue
            for resource, price in (market_doc.get("resource_prices") or {}).items():
                # Multiple market memberships: take the highest price found
                # across those markets for the same resource.
                if resource in PRICES and price > 0:
                    market_prices[resource] = max(market_prices.get(resource, 0), price)
        prices.update(market_prices)
    except Exception:
        pass
    return prices


NON_RESEARCH_RESOURCE_VALUES = [
    value for key, value in PRICES.items() if key != "research"
]
AVERAGE_NON_RESEARCH_RESOURCE_VALUE = (
    sum(NON_RESEARCH_RESOURCE_VALUES) / len(NON_RESEARCH_RESOURCE_VALUES)
    if NON_RESEARCH_RESOURCE_VALUES
    else 0.0
)

def value_and_update_stock(
    resources: Dict[str, float],
    stock: Dict[str, float],
    capacity: Dict[str, float],
    prices: Dict[str, float] = PRICES,
) -> float:
    """
    Convert resource changes into money value, applying reduced value (50%)
    for production that exceeds storage capacity. Updates stock in-place.
    """
    total = 0.0
    for resource, amount in resources.items():
        if resource not in prices or amount == 0:
            continue
        price = prices[resource]
        current = float(stock.get(resource, 0) or 0)
        cap = float(capacity.get(resource, float("inf")) or float("inf"))
        if resource == "research":
            cap = 10

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
    prices: Dict[str, float] = PRICES,
) -> Tuple[float, Dict[str, float]]:
    total = 0.0
    breakdown: Dict[str, float] = {}
    for resource, amount in resources.items():
        if resource not in prices or amount == 0:
            continue
        price = prices[resource]
        current = float(stock.get(resource, 0) or 0)
        cap = float(capacity.get(resource, float("inf")) or float("inf"))
        if resource == "research":
            cap = 10

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
    nation: Dict[str, Any], prices: Dict[str, float] = PRICES
) -> Tuple[
    float,
    int,
    int,
    Dict[str, Dict[str, Dict[str, float]]],
    int,
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
    locked_jobs = {"partial_undead", "undead", "partial_vampire", "revolutionary"}
    nation_jobs = nation_copy.get("jobs", {}) or {}
    nation_copy["jobs"] = {
        job: count for job, count in nation_jobs.items() if job in locked_jobs
    }
    #nation_copy["consumption_stance"] = "Standard"
    #nation_copy["science_stance"] = "Pragmatic"
    #nation_copy["tax_stance"] = "Low"
    nation_copy["land_units"] = []
    nation_copy["naval_units"] = []
    nation_copy["support_units"] = []
    modifiers = nation_copy.get("modifiers", [])
    if isinstance(modifiers, list):
        nation_copy["modifiers"] = [
            modifier
            for modifier in modifiers
            if isinstance(modifier, dict)
            and (modifier.get("duration", -1) == -1 or modifier.get("duration", 0) >= 3)
            and modifier.get("key") != "magic_consumption"
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
    # "money" is a plain (non-calculated) field, so it never appears in `calculated`
    # (calculate_all_fields' return value only carries computed fields) — read the
    # real current amount from nation_copy, which still has it from the original doc.
    stock["money"] = float(nation_copy.get("money", 0) or 0)
    capacity = deepcopy(calculated.get("nation_resource_capacity", {}) or {})
    capacity["money"] = float(calculated.get("money_capacity", 0) or 0)

    base_resource_value, base_resource_breakdown = value_and_update_stock_with_breakdown(
        resource_excess, stock, capacity, prices
    )
    base_value = money_income + base_resource_value

    pop_count = int(calculated.get("pop_count", nation_copy.get("pop_count", 0)) or 0)
    administration = int(calculated.get("administration", nation_copy.get("administration", 0)) or 0)
    job_details = calculated.get("job_details", {}) or {}
    locked_job_count = 0
    for job in locked_jobs:
        locked_job_count += int(nation_jobs.get(job, 0) or 0)
    job_details = {job: details for job, details in job_details.items() if job not in locked_jobs}
    job_stock = {}
    land_attack = float(calculated.get("land_attack", nation_copy.get("land_attack", 0)) or 0)
    land_defense = float(calculated.get("land_defense", nation_copy.get("land_defense", 0)) or 0)
    land_unit_capacity = int(calculated.get("land_unit_capacity", nation_copy.get("land_unit_capacity", 0)) or 0)

    return (
        base_value,
        pop_count,
        administration,
        job_details,
        locked_job_count,
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
    prices: Dict[str, float] = PRICES,
) -> Tuple[float, Dict[str, float]]:
    """
    Evaluate the marginal money value of assigning one pop to the job,
    applying storage capacity diminishing returns. Returns (value, new_stock).
    """
    details = job_details.get(job_key)
    if not details:
        return float("-inf"), stock

    production = {k: v for k, v in (details.get("production", {}) or {}).items() if k in prices}
    upkeep = {k: v for k, v in (details.get("upkeep", {}) or {}).items() if k in prices}

    temp_stock = deepcopy(stock)
    value = 0.0
    value += value_and_update_stock(production, temp_stock, capacity, prices)
    if upkeep:
        negative_upkeep = {k: -v for k, v in upkeep.items()}
        value += value_and_update_stock(negative_upkeep, temp_stock, capacity, prices)

    return value, temp_stock


def simulate_optimal_assignments(
    pop_count: int,
    job_details: Dict[str, Any],
    capacity: Dict[str, float],
    stock: Dict[str, float],
    locked_job_count: int = 0,
    prices: Dict[str, float] = PRICES,
) -> Tuple[float, Dict[str, int]]:
    """
    Greedy simulation: repeatedly pick the best job given current stock/capacity,
    assign one pop, update stock, and re-evaluate until all pops are placed.
    Returns (total_value, assignments).
    """
    total_value = 0.0
    assignments: Dict[str, int] = {}
    current_stock = deepcopy(stock)

    if locked_job_count:
        original_pop_count = pop_count
        pop_count = max(pop_count - locked_job_count, 0)
        print(
            f"Locked jobs preserved: {locked_job_count} pops "
            f"(undead/partial_vampire/revolutionary). "
            f"Remaining pops to assign: {pop_count} (was {original_pop_count})."
        )

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
                prices,
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


def sum_vassal_concessions_cost(nation: Dict[str, Any]) -> float:
    db = category_data["nations"]["database"]
    overlord_id = nation.get("_id")
    overlord_id_str = str(overlord_id or "")
    if not overlord_id_str:
        return 0.0
    vassals = db.find(
        {"overlord": {"$in": [overlord_id, overlord_id_str]}},
        {"concessions_chance": 1},
    )
    base_cost = AVERAGE_NON_RESEARCH_RESOURCE_VALUE * 4
    total = 0.0
    for vassal in vassals:
        chance = float(vassal.get("concessions_chance", 0) or 0)
        total += base_cost * chance
    return total


def count_wonders(nation: Dict[str, Any]) -> int:
    db = category_data["wonders"]["database"]
    nation_id = nation.get("_id")
    nation_id_str = str(nation_id or "")
    return db.count_documents({"owner_nation": {"$in": [nation_id, nation_id_str]}})


class _Tee:
    """Minimal stdout-like object that writes to multiple streams at once,
    so script output goes to the console and a saved report simultaneously."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)

    def flush(self):
        for stream in self._streams:
            stream.flush()


def _run():
    load_dotenv(override=True)

    nations = list(category_data["nations"]["database"].find({}).sort("name", 1))

    results: List[Tuple[str, float, int, int, int, float, str]] = []
    for nation in nations:
        name = nation.get("name", "<Unnamed>")
        temperament = nation.get("temperament", "Unknown")

        print(f"Calculating {name}")
        prices = get_nation_prices(nation)
        (
            base_value,
            pop_count,
            _administration,
            job_details,
            locked_job_count,
            capacity,
            _stock,
            job_stock,
            base_resource_breakdown,
            money_income,
            land_attack,
            land_defense,
            land_unit_capacity,
        ) = compute_base_net_value(nation, prices)
        marginal_value, _assignments = simulate_optimal_assignments(
            pop_count,
            job_details,
            capacity,
            job_stock,
            locked_job_count,
            prices,
        )

        ndp = base_value + marginal_value
        vassal_count = count_vassals(nation)
        vassal_concessions_cost = sum_vassal_concessions_cost(nation)
        ndp -= vassal_concessions_cost
        print(
            f"Breakdown {name}: base={base_value:.2f}, marginal={marginal_value:.2f}, "
            f"vassal_concessions={vassal_concessions_cost:.2f}, "
            f"total={ndp:.2f}"
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
        print(
            f"{name}, {ndp:.2f}, {pop_count}, {vassal_count}, {wonder_count}, "
            f"{infrastructure_score:.2f}, {military_score:.2f}, {temperament}"
        )


def main():
    """Run the NDP calculation, writing all console output to a timestamped
    report file (in addition to printing it) under ndp_reports/."""
    output_dir = os.path.join(os.getcwd(), "ndp_reports")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"ndp_report_{timestamp}.txt")

    original_stdout = sys.stdout
    with open(output_path, "w", encoding="utf-8") as report_file:
        sys.stdout = _Tee(original_stdout, report_file)
        try:
            _run()
        finally:
            sys.stdout = original_stdout

    print(f"Report written to {output_path}")


if __name__ == "__main__":
    main()
