# app_core.py
from flask import Flask
from flask_pymongo import PyMongo
from flask_discord import DiscordOAuth2Session
import os
import json
import re
from dotenv import load_dotenv
import subprocess
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate
from email import encoders

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# Discord config
app.config["DISCORD_CLIENT_ID"] = os.getenv("DISCORD_CLIENT_ID")
app.config["DISCORD_CLIENT_SECRET"] = os.getenv("DISCORD_CLIENT_SECRET")
app.config["DISCORD_REDIRECT_URI"] = os.getenv("DISCORD_REDIRECT_URI")

# Mongo config
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
mongo = PyMongo(app)
discord = DiscordOAuth2Session(app)

@app.template_filter('format_discord_link')
def format_discord_link(text):
    # Pattern for Discord message links
    discord_pattern = r'(https://(?:ptb\.|canary\.)?discord\.com/channels/\d+/\d+/\d+)'
    
    def replace_link(match):
        full_url = match.group(1)
        # Extract the last part of the URL (message ID) for display
        message_id = full_url.split('/')[-1]
        return f'<a href="{full_url}" target="_blank">Discord Message ({message_id})</a>'
    
    # Replace Discord links with formatted HTML
    formatted_text = re.sub(discord_pattern, replace_link, str(text))
    return formatted_text

category_data = {
    "nations": {"pluralName": "Nations", "singularName": "Nation", "database": mongo.db.nations},
    "regions": {"pluralName": "Regions", "singularName": "Region", "database": mongo.db.regions},
    "races": {"pluralName": "Races", "singularName": "Race", "database": mongo.db.races},
    "cultures": {"pluralName": "Cultures", "singularName": "Culture", "database": mongo.db.cultures},
    "religions": {"pluralName": "Religions", "singularName": "Religion", "database": mongo.db.religions},
    "merchants": {"pluralName": "Merchants", "singularName": "Merchant", "database": mongo.db.merchants},
    "mercenaries": {"pluralName": "Mercenaries", "singularName": "Mercenary", "database": mongo.db.mercenaries},
    "factions": {"pluralName": "Factions", "singularName": "Faction", "database": mongo.db.factions},
    "characters": {"pluralName": "Characters", "singularName": "Character", "database": mongo.db.characters},
    "players": {"pluralName": "Players", "singularName": "Player", "database": mongo.db.players},
    "artifacts": {"pluralName": "Artifacts", "singularName": "Artifact", "database": mongo.db.artifacts},
    "spells": {"pluralName": "Spells", "singularName": "Spell", "database": mongo.db.spells},
    "wonders": {"pluralName": "Wonders", "singularName": "Wonder", "database": mongo.db.wonders},
    "markets": {"pluralName": "Markets", "singularName": "Market", "database": mongo.db.markets},
    "wars": {"pluralName": "Wars", "singularName": "War", "database": mongo.db.wars},
    "diplo_relations": {"pluralName": "Diplomatic Relations", "singularName": "Diplomatic Relation", "database": mongo.db.diplo_relations},
    "pops": {"pluralName": "Pops", "singularName": "Pop", "database": mongo.db.pops},
    "trades": {"pluralName": "Trades", "singularName": "Trade", "database": mongo.db.trades},
    "events": {"pluralName": "Events", "singularName": "Event", "database": mongo.db.events},
    "changes": {"pluralName": "Changes", "singularName": "Change", "database": mongo.db.changes}
}

rarity_rankings = {"Mythical": 0, "Legendary": 1, "Great": 2, "Good": 3, "Mundane": 4}

json_files = ["jobs", "nation_districts", "nation_imperial_districts", "mercenary_districts", "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts", "cities", "walls", "titles"]
land_unit_json_files = ["ancient_magical_land_units", "ancient_mundane_land_units", "ancient_unique_land_units",
                         "classical_magical_land_units", "classical_mundane_land_units", "classical_unique_land_units",
                         "imperial_generic_units", "imperial_unique_units"]
naval_unit_json_files = ["ancient_mundane_naval_units", "classical_magical_naval_units", "classical_mundane_naval_units"]
misc_unit_json_files = ["ruler_units", "void_units"]
unit_json_files = land_unit_json_files + naval_unit_json_files + misc_unit_json_files
unit_json_file_titles = ["Ancient Magical Land Units", "Ancient Mundane Land Units", "Ancient Mundane Naval Units", "Ancient Unique Land Units",
                         "Classical Magical Land Units", "Classical Magical Naval Units", "Classical Mundane Land Units", "Classical Mundane Naval Units", "Classical Unique Land Units",
                         "Imperial Generic Units", "Imperial Unique Units", "Ruler Units", "Void Units"]
json_data = {"general_resources": [
            {"key": "food", "name": "Food", "base_storage": 20},
            {"key": "wood", "name": "Wood", "base_storage": 15},
            {"key": "stone", "name": "Stone", "base_storage": 15},
            {"key": "mounts", "name": "Mounts", "base_storage": 10},
            {"key": "research", "name": "Research", "base_storage": 0},
            {"key": "magic", "name": "Magic", "base_storage": 10}
        ],
        "unique_resources": [
            {"key": "bronze", "name": "Bronze", "base_storage": 5},
            {"key": "iron", "name": "Iron", "base_storage": 0},
        ]
}

def load_json(file_path):
    with open(file_path, "r") as file:
        json_data = json.load(file)
    return json_data

for data_type in category_data:
    category_data[data_type]["schema"] = load_json("json-data/schemas/" + data_type + ".json")["$jsonSchema"]

for file in json_files:
    json_data[file] = load_json("json-data/" + file + ".json")

for file in unit_json_files:
    json_data[file] = load_json("json-data/units/" + file + ".json")

def backup_mongodb():
    """
    Creates a backup of the MongoDB database and sends it via email.
    Returns a tuple of (success, message)
    """
    try:
        # Create backup directory if it doesn't exist
        backup_dir = os.path.join(os.getcwd(), 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        # Generate timestamp for the backup filename
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"mongodb_backup_{timestamp}"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        # Get MongoDB connection details from environment variables
        mongo_uri = os.getenv("MONGO_URI", "")
        db_name = mongo_uri.split("/")[-1]  # Extract database name from URI
        
        print(f"Backing up {db_name} to {backup_path}")

        # Run mongodump to create the backup
        cmd = [
            "mongodump",
            f"--uri={mongo_uri}",
            f"--out={backup_path}"
        ]

        print(f"Running command: {' '.join(cmd)}")
        
        subprocess.run(cmd, check=True)
        
        print("Backup created successfully")

        # Create a zip file of the backup
        zip_path = f"{backup_path}.zip"
        cmd_zip = ["zip", "-r", zip_path, backup_path]
        subprocess.run(cmd_zip, check=True)

        print("Backup zipped successfully")
        
        # Send email with the backup attached
        email_success, email_message = send_backup_email(zip_path, db_name, timestamp)
        
        return True, f"Backup created successfully at {backup_path} and {zip_path}. Email status: {email_message}"
    
    except Exception as e:
        return False, f"Backup failed: {str(e)}"

def send_backup_email(attachment_path, db_name, timestamp):
    """
    Sends an email with the backup file attached.
    """
    try:
        # Email configuration
        email_from = os.getenv("EMAIL_FROM", "")
        email_to = os.getenv("EMAIL_TO", "")
        email_password = os.getenv("EMAIL_PASSWORD", "")
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        
        if not email_from or not email_to or not email_password:
            return False, "Email configuration missing"
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = email_from
        msg['To'] = email_to
        msg['Date'] = formatdate(localtime=True)
        msg['Subject'] = f"MongoDB Backup - {db_name} - {timestamp}"
        
        # Email body
        body = f"Attached is the MongoDB backup for {db_name} created on {timestamp}."
        msg.attach(MIMEText(body))
        
        # Attach the backup file
        with open(attachment_path, 'rb') as file:
            part = MIMEBase('application', 'zip')
            part.set_payload(file.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(attachment_path)}"')
            msg.attach(part)
        
        # Send the email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(email_from, email_password)
            server.send_message(msg)
        
        return True, "Email sent successfully"
    
    except Exception as e:
        return False, f"Email failed: {str(e)}"

def restore_mongodb(backup_path=None, backup_date=None):
    """
    Restores MongoDB database from a backup.
    
    Args:
        backup_path: Direct path to the backup directory or zip file
        backup_date: Date string in format 'YYYYMMDD_HHMMSS' to find a specific backup
        
    Returns:
        tuple: (success, message)
    """
    try:
        backup_dir = os.path.join(os.getcwd(), 'backups')
        
        # If no specific backup is provided, find the most recent one
        if not backup_path and not backup_date:
            # List all backups and sort by name (which includes timestamp)
            backups = [f for f in os.listdir(backup_dir) 
                      if os.path.isdir(os.path.join(backup_dir, f)) and f.startswith('mongodb_backup_')]
            
            if not backups:
                return False, "No backups found"
            
            # Sort by timestamp (newest first)
            backups.sort(reverse=True)
            backup_path = os.path.join(backup_dir, backups[0])
            
        # If date is provided, find that specific backup
        elif backup_date:
            backup_name = f"mongodb_backup_{backup_date}"
            backup_path = os.path.join(backup_dir, backup_name)
            
            # Check if it's a zip file
            if os.path.exists(f"{backup_path}.zip") and not os.path.exists(backup_path):
                # Extract the zip file
                cmd_unzip = ["unzip", f"{backup_path}.zip", "-d", backup_dir]
                subprocess.run(cmd_unzip, check=True)
        
        # Handle zip files
        if backup_path.endswith('.zip'):
            # Extract the zip file
            cmd_unzip = ["unzip", backup_path, "-d", backup_dir]
            subprocess.run(cmd_unzip, check=True)
            # Update backup_path to the extracted directory
            backup_path = backup_path[:-4]  # Remove .zip extension
        
        # Ensure the backup directory exists
        if not os.path.exists(backup_path):
            return False, f"Backup directory not found: {backup_path}"
        
        # Get MongoDB connection details
        mongo_uri = os.getenv("MONGO_URI", "")
        db_name = mongo_uri.split("/")[-1]  # Extract database name from URI
        
        # Create a backup before restoring
        backup_mongodb()

        # Run mongorestore to restore the backup
        cmd = [
            "mongorestore",
            f"--uri={mongo_uri}",
            "--drop",  # Drop existing collections before restoring
            os.path.join(backup_path, db_name)
        ]
        
        subprocess.run(cmd, check=True)
        
        return True, f"Database restored successfully from {backup_path}"
    
    except Exception as e:
        return False, f"Restore failed: {str(e)}"
