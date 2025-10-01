import csv
import os
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables
load_dotenv(override=True)

def extract_nations_to_csv():
    # Get MongoDB URI from environment
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        print("Error: MONGO_URI not found in environment variables")
        return
    
    # Parse the MongoDB URI to extract database name
    parsed_uri = urlparse(mongo_uri)
    db_name = parsed_uri.path.lstrip('/')
    
    # If there's a question mark in the db_name, only take what's before it
    if '?' in db_name:
        db_name = db_name.split('?')[0]
    
    # Connect to MongoDB
    client = MongoClient(mongo_uri)
    db = client[db_name]
    
    print(f"Connected to database: {db_name}")
    
    # Get all nations
    nations = list(db.nations.find({}, {"_id": 1, "name": 1}).sort("name", 1))
    
    # Prepare data for CSV
    csv_data = []
    
    for nation in nations:
        nation_id = nation["_id"]
        nation_name = nation["name"]
        
        # Count pops for this nation
        pop_count = db.pops.count_documents({"nation": str(nation_id)})
        
        csv_data.append([nation_name, pop_count])
    
    # Write to CSV
    with open('nations_pop_count.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Nation Name', 'Pop Count'])  # Header
        writer.writerows(csv_data)
    
    print(f"Exported {len(csv_data)} nations to nations_pop_count.csv")

if __name__ == "__main__":
    extract_nations_to_csv()
