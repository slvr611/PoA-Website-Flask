import random

from app_core import category_data, character_stats


def main():
    db = category_data["characters"]["database"]
    characters = list(db.find({"health_status": {"$ne": "Dead"}}).sort("name", 1))

    print("Name, stat, 1d4 roll")
    for character in characters:
        name = character.get("name", "Unknown")
        stat = random.choice(character_stats)
        roll = random.randint(1, 4)
        print(f"{name}, {stat}, {roll}")


if __name__ == "__main__":
    main()
