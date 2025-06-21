#!/bin/bash
cd /mnt/persist/workspace
source .venv/bin/activate
export FLASK_APP=app.py
export FLASK_ENV=development
export SECRET_KEY=test_secret_key_for_setup
export MONGO_URI=mongodb://localhost:27017/test_db
exec "$@"
