"""
Pytest configuration and shared fixtures for the PoA test suite.

This module must be imported before any app code so that environment variables
and the working directory are set correctly before app_core initialises.
"""
import os
import sys

# ---------------------------------------------------------------------------
# Environment setup — must happen before any app import
# ---------------------------------------------------------------------------
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Add the project root to sys.path so ``import app_core`` etc. work.
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Set the working directory to the project root so app_core can find JSON
# schema files at their relative paths (e.g. "json-data/schemas/…").
os.chdir(_project_root)

# Provide dummy values for every env var that app_core or Flask-Discord reads
# at import time.  The values are never used for real network connections in
# tests, but they must be present to avoid KeyError / None-config errors.
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/poa_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DISCORD_CLIENT_ID", "test-client-id")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DISCORD_REDIRECT_URI", "http://localhost/callback")

# ---------------------------------------------------------------------------
# Regular imports (after path/env are ready)
# ---------------------------------------------------------------------------
import pytest
import mongomock
from unittest.mock import MagicMock
from bson import ObjectId


# ---------------------------------------------------------------------------
# Session-scoped Flask app
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def flask_app():
    """Return the real Flask app in testing mode.

    Session-scoped so app_core is only imported once — importing it triggers
    schema JSON loading and index creation, which are expensive side effects.
    """
    from app_core import app
    app.config["TESTING"] = True
    return app


# ---------------------------------------------------------------------------
# Per-test isolated in-memory MongoDB
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Provide a fresh, empty mongomock database for each test.

    Each test gets its own MongoClient instance so data never leaks between
    tests.
    """
    client = mongomock.MongoClient()
    db = client["poa_test"]
    yield db
    client.close()


# ---------------------------------------------------------------------------
# Helpers that wire change_helpers to the test database
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mongo(test_db):
    """A MagicMock whose ``.db`` attribute is the mongomock test database.

    Patching ``helpers.change_helpers.mongo`` with this object redirects all
    database calls in that module to the isolated in-memory database.
    """
    m = MagicMock()
    m.db = test_db
    return m


@pytest.fixture
def fake_category_data(test_db):
    """Minimal ``category_data`` structure backed by mongomock collections.

    Only the collections exercised by the changes helpers are included.
    The schema stubs are enough for the helpers to run without errors when
    ``_calculate_and_attach_fields`` and ``propagate_updates`` are mocked out.
    """
    _empty_schema = {
        "properties": {},
        "external_calculation_requirements": {},
    }
    return {
        "nations": {
            "pluralName": "Nations",
            "singularName": "Nation",
            "database": test_db["nations"],
            "schema": _empty_schema,
        },
        "characters": {
            "pluralName": "Characters",
            "singularName": "Character",
            "database": test_db["characters"],
            "schema": _empty_schema,
        },
        "players": {
            "pluralName": "Players",
            "singularName": "Player",
            "database": test_db["players"],
            "schema": _empty_schema,
        },
        "changes": {
            "pluralName": "Changes",
            "singularName": "Change",
            "database": test_db["changes"],
            "schema": _empty_schema,
        },
    }
