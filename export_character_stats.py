import os

from dotenv import load_dotenv
from pymongo import MongoClient
from urllib.parse import urlparse
from app_core import json_data, category_data

projection = {
    "_id": 0,
    "name": 1,
    "rulership": 1,
    "cunning": 1,
    "charisma": 1,
    "prowess": 1,
    "magic": 1,
    "strategy": 1,
    "elderly_age": 1,
}
db = category_data["characters"]["database"]
characters = list(db.find({"health_status": {"$ne": "Dead"}}, projection).sort("name", 1))

for character in characters:
    print(f"{character['name']}, {character['rulership']}, {character['cunning']}, {character['charisma']}, {character['prowess']}, {character['magic']}, {character['strategy']}, {character['elderly_age']}")
