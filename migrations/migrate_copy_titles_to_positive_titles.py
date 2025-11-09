"""
Migration: Copy characters.titles -> characters.positive_titles

Usage:
    python scripts/migrate_copy_titles_to_positive_titles.py

This script uses the existing Flask app + PyMongo setup from app_core.py
to connect to the same MongoDB and update all character documents.
"""

from typing import List
import sys
from pathlib import Path
from app_core import app, mongo


def normalize_titles(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        # Ensure all elements are strings
        return [str(v) for v in value]
    # If a single string (or other), wrap into a list
    return [str(value)]


def migrate():
    updated = 0
    skipped = 0
    with app.app_context():
        cursor = mongo.db.characters.find({}, {"_id": 1, "titles": 1, "max_titles": 1})
        for doc in cursor:
            titles = normalize_titles(doc.get("titles"))
            max_titles = doc.get("max_titles", 3)
            try:
                mongo.db.characters.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"positive_titles": titles, "positive_title_slots": max_titles}},
                )
                updated += 1
            except Exception:
                skipped += 1
        print(f"Migration complete. Updated: {updated}, Skipped: {skipped}")


if __name__ == "__main__":
    migrate()
