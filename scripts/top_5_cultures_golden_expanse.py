import os
from collections import Counter
from urllib.parse import urlparse

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient


def get_database_from_uri(mongo_uri: str):
    parsed_uri = urlparse(mongo_uri)
    db_name = parsed_uri.path.lstrip('/')
    if '?' in db_name:
        db_name = db_name.split('?')[0]
    if not db_name:
        raise ValueError('Could not determine database name from MONGO_URI')

    client = MongoClient(mongo_uri)
    return client, client[db_name], db_name


def try_object_id(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def main():
    load_dotenv(override=True)

    mongo_uri = os.getenv('MONGO_URI')
    if not mongo_uri:
        print('Error: MONGO_URI not found in environment variables')
        return

    client, db, db_name = get_database_from_uri(mongo_uri)
    print(f'Connected to database: {db_name}')

    region_name = 'Golden Expanse'
    region = db.regions.find_one({'name': {'$regex': r'^golden expanse$', '$options': 'i'}}, {'_id': 1, 'name': 1})
    if not region:
        print(f"Region '{region_name}' not found.")
        return

    region_id = region['_id']
    region_id_str = str(region_id)

    nations = list(
        db.nations.find(
            {'region': {'$in': [region_id, region_id_str]}},
            {'_id': 1, 'name': 1}
        )
    )

    if not nations:
        print(f"No nations found in region '{region.get('name', region_name)}'.")
        return

    nation_id_strings = [str(nation['_id']) for nation in nations]
    nation_id_objects = [nation['_id'] for nation in nations]

    pops = list(
        db.pops.find(
            {'nation': {'$in': nation_id_strings + nation_id_objects}},
            {'culture': 1}
        )
    )

    total_pops = len(pops)
    culture_counter = Counter(pop.get('culture', 'Unknown') for pop in pops)

    if not culture_counter:
        print(f"No pops found in nations within region '{region.get('name', region_name)}'.")
        return

    culture_ids = []
    for culture_key in culture_counter.keys():
        culture_obj_id = try_object_id(culture_key)
        if culture_obj_id:
            culture_ids.append(culture_obj_id)

    culture_name_map = {}
    if culture_ids:
        cultures = db.cultures.find({'_id': {'$in': culture_ids}}, {'name': 1})
        for culture in cultures:
            culture_name_map[str(culture['_id'])] = culture.get('name', str(culture['_id']))

    top_five = culture_counter.most_common(5)

    print(f"Region: {region.get('name', region_name)}")
    print(f'Nations in region: {len(nations)}')
    print(f'Total pops in region nations: {total_pops}')
    print('Top 5 cultures by pop count:')

    for index, (culture_key, count) in enumerate(top_five, start=1):
        display_name = culture_name_map.get(str(culture_key), str(culture_key))
        print(f'{index}. {display_name}: {count}')

    client.close()


if __name__ == '__main__':
    main()
