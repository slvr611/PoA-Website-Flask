"""Print all nations within 10 tiles of each nation's territory."""

from app_core import mongo
from helpers.hex_map_helpers import get_nations_within_distance

MAX_DISTANCE = 10

nations = sorted(
    n["name"] for n in mongo.db.nations.find({}, {"name": 1, "_id": 0}) if n.get("name")
)

for nation in nations:
    nearby = get_nations_within_distance(nation, MAX_DISTANCE)
    if nearby:
        print(f"{nation}: {', '.join(nearby)}")
    else:
        print(f"{nation}: (none within {MAX_DISTANCE} tiles)")
