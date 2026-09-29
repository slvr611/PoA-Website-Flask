"""
For each region: total hexes, land hexes, and ocean hexes (deep_water +
shallow_water terrain). Land is everything else (total - ocean) — every
other terrain key (plains, forest, hazardous_land, hazardous_water, river,
etc.) counts as land here, since the request only asked for a land/ocean
split, not a full terrain breakdown.

hex_map_tiles.region already stores the region's plain name string
directly (not an id), so grouping is a single aggregation with no join
needed. Tiles with no region set (a handful of stray/unassigned tiles) are
reported separately under "(No Region)" rather than silently dropped.
"""
import os
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

OCEAN_TERRAINS = ["deep_water", "shallow_water"]


def get_database_from_uri(mongo_uri: str):
    parsed_uri = urlparse(mongo_uri)
    db_name = parsed_uri.path.lstrip('/')
    if '?' in db_name:
        db_name = db_name.split('?')[0]
    if not db_name:
        raise ValueError('Could not determine database name from MONGO_URI')

    client = MongoClient(mongo_uri)
    return client, client[db_name], db_name


def main():
    load_dotenv(override=True)

    mongo_uri = os.getenv('MONGO_URI')
    if not mongo_uri:
        print('Error: MONGO_URI not found in environment variables')
        return

    client, db, db_name = get_database_from_uri(mongo_uri)
    print(f'Connected to database: {db_name}')

    pipeline = [
        {"$group": {
            "_id": "$region",
            "total": {"$sum": 1},
            "ocean": {"$sum": {"$cond": [{"$in": ["$terrain", OCEAN_TERRAINS]}, 1, 0]}},
        }},
    ]
    rows = list(db.hex_map_tiles.aggregate(pipeline))

    results = []
    for row in rows:
        region_name = row["_id"] or "(No Region)"
        total = row["total"]
        ocean = row["ocean"]
        land = total - ocean
        results.append((region_name, total, land, ocean))

    results.sort(key=lambda r: r[0])

    name_width = max((len(r[0]) for r in results), default=10)
    header = f"{'Region':<{name_width}}  {'Total':>8}  {'Land':>8}  {'Ocean':>8}"
    print(header)
    print("-" * len(header))
    grand_total = grand_land = grand_ocean = 0
    for region_name, total, land, ocean in results:
        print(f"{region_name:<{name_width}}  {total:>8}  {land:>8}  {ocean:>8}")
        grand_total += total
        grand_land += land
        grand_ocean += ocean
    print("-" * len(header))
    print(f"{'TOTAL':<{name_width}}  {grand_total:>8}  {grand_land:>8}  {grand_ocean:>8}")

    client.close()


if __name__ == '__main__':
    main()
