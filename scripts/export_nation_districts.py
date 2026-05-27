"""
Export all player nations and their districts to a CSV file.
Run from the project root: python export_nation_districts.py
"""

import json
import csv
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(override=True)

# Load district display name maps from all relevant JSON files
DISTRICT_JSON_FILES = [
    "json-data/nation_imperial_districts.json",
]

district_names = {}
for path in DISTRICT_JSON_FILES:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, val in data.items():
            district_names[key] = val.get("display_name", key)

# Connect to MongoDB
client = MongoClient(os.getenv("MONGO_URI"))
db = client["PoAWebsiteFlask"]

nations = list(db.nations.find({"temperament": "Player"}).sort("name", 1))

OUTPUT_FILE = "nation_districts_export.csv"

with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["Nation", "District #", "District Type", "District Display Name", "Node"])

    for nation in nations:
        name = nation.get("name", "(unnamed)")
        districts = nation.get("districts", [])
        imperial = nation.get("imperial_district")

        if not districts and not imperial:
            writer.writerow([name, "", "", "(no districts)", ""])
            continue

        for i, d in enumerate(districts, start=1):
            dtype = d.get("type", "")
            dname = district_names.get(dtype, dtype)
            node = d.get("node", "")
            writer.writerow([name, i, dtype, dname, node])

        if imperial:
            dtype = imperial.get("type", "")
            dname = district_names.get(dtype, dtype)
            node = imperial.get("node", "")
            writer.writerow([name, "Imperial", dtype, dname, node])

print(f"Exported {len(nations)} player nations to {OUTPUT_FILE}")
