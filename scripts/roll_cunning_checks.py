import random
from copy import deepcopy

from app_core import category_data
from calculations.field_calculations import calculate_all_fields


def get_difficulty(calculated_character):
    elderly_age = calculated_character.get("elderly_age", 3)
    return 15 if elderly_age > 500 else 10


def main():
    db = category_data["characters"]["database"]
    characters = list(db.find({"health_status": {"$ne": "Dead"}}).sort("name", 1))

    print("Name, cunning, base roll, total roll, difficulty, pass/fail")
    for character in characters:
        working_character = deepcopy(character)
        calculated_fields = calculate_all_fields(
            working_character,
            category_data["characters"]["schema"],
            "character",
        )
        working_character.update(calculated_fields)

        name = working_character.get("name", "Unknown")
        cunning = int(working_character.get("cunning", 0) or 0)
        base_roll = random.randint(1, 20)
        total_roll = base_roll + cunning
        difficulty = get_difficulty(working_character)
        pass_fail = "Pass" if total_roll >= difficulty else "Fail"

        print(f"{name}, {cunning}, {base_roll}, {total_roll}, {difficulty}, {pass_fail}")


if __name__ == "__main__":
    main()
