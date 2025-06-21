#!/bin/bash

# Update system packages
sudo apt-get update -qq

# Install Python 3 and pip if not already installed
sudo apt-get install -y python3 python3-pip python3-venv python3-dev

# Change to the workspace directory
cd /mnt/persist/workspace

# Remove any existing virtual environment
rm -rf .venv

# Create a fresh virtual environment using Python 3
python3 -m venv .venv

# Verify the virtual environment was created correctly
echo "Virtual environment created successfully"
ls -la .venv/bin/ | head -3

# Activate virtual environment
source .venv/bin/activate

# Verify we're using the virtual environment Python
echo "Using Python: $(which python)"
echo "Python version: $(python --version)"

# Upgrade pip
pip install --upgrade pip

# Install Python dependencies from requirements.txt
pip install -r requirements.txt

# Create necessary directories and JSON files
mkdir -p static/images/maps json-data/schemas json-data/units templates routes helpers calculations backups summaries

# Create minimal JSON schema files for all categories
for category in nations regions races cultures religions merchants mercenaries factions characters players artifacts spells wonders markets market_links wars diplo_relations pops trades events changes; do
cat > json-data/schemas/${category}.json << EOF
{
  "\$jsonSchema": {
    "bsonType": "object",
    "required": ["name"],
    "properties": {
      "name": {
        "bsonType": "string",
        "label": "Name",
        "description": "Name of the ${category}"
      }
    }
  }
}
EOF
done

# Create minimal JSON data files
for file in jobs tech nation_districts nation_imperial_districts mercenary_districts merchant_production_districts merchant_specialty_districts merchant_luxury_districts cities terrains walls titles; do
cat > json-data/${file}.json << EOF
{
  "test_${file}": {
    "display_name": "Test ${file}",
    "requirements": {},
    "effects": {}
  }
}
EOF
done

# Create unit JSON files
for file in ancient_magical_land_units ancient_mundane_land_units ancient_unique_land_units classical_magical_land_units classical_mundane_land_units classical_unique_land_units imperial_generic_units imperial_unique_units ancient_mundane_naval_units classical_magical_naval_units classical_mundane_naval_units ruler_units void_units; do
cat > json-data/units/${file}.json << EOF
{
  "test_${file}": {
    "display_name": "Test ${file}",
    "requirements": {},
    "stats": {}
  }
}
EOF
done

# Set environment variables
export FLASK_APP=app.py
export FLASK_ENV=development
export SECRET_KEY=test_secret_key_for_setup
export MONGO_URI=mongodb://localhost:27017/test_db

# Add to profile for future sessions
echo "# Flask development environment" >> $HOME/.profile
echo "cd /mnt/persist/workspace" >> $HOME/.profile
echo "source /mnt/persist/workspace/.venv/bin/activate" >> $HOME/.profile
echo "export FLASK_APP=app.py" >> $HOME/.profile
echo "export FLASK_ENV=development" >> $HOME/.profile
echo "export SECRET_KEY=test_secret_key_for_setup" >> $HOME/.profile
echo "export MONGO_URI=mongodb://localhost:27017/test_db" >> $HOME/.profile

# Test imports with the virtual environment Python
echo "Testing Flask application imports:"
python -c "
import sys
print('✓ Python executable:', sys.executable)

import flask
print('✓ Flask imported successfully, version:', flask.__version__)

import app
print('✓ App module imported successfully')

from app_core import app as flask_app
print('✓ App core imported successfully')

print('✓ All imports successful - Flask application is ready!')
"

# Create a wrapper script for running tests with the virtual environment
cat > run_tests.sh << 'EOF'
#!/bin/bash
cd /mnt/persist/workspace
source .venv/bin/activate
export FLASK_APP=app.py
export FLASK_ENV=development
export SECRET_KEY=test_secret_key_for_setup
export MONGO_URI=mongodb://localhost:27017/test_db
exec "$@"
EOF

chmod +x run_tests.sh

echo "Setup completed successfully!"
echo "Virtual environment: $(pwd)/.venv"
echo "Python version: $(.venv/bin/python --version)"
echo "Flask version: $(.venv/bin/python -c 'import flask; print(flask.__version__)')"