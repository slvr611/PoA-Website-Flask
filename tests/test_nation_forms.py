import unittest
import json
import os
from flask import Flask
from forms import EnhancedNationEditForm, ResourceStorageForm, JobAssignmentForm

class TestEnhancedNationForms(unittest.TestCase):
    """Test cases for the enhanced nation form system"""
    
    def setUp(self):
        """Set up test environment"""
        # Create a Flask app for the test context
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test_secret_key'
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
        
        # Load a sample schema for testing
        with open("json-data/schemas/nations.json", "r") as f:
            self.nations_schema = json.load(f)["$jsonSchema"]
        
        # Load sample JSON data
        self.json_data = {
            "districts": {
                "farm": {"display_name": "Farm"},
                "mine": {"display_name": "Mine"},
                "dock": {"display_name": "Dock"}
            },
            "general_resources": [
                {"key": "food", "name": "Food", "base_storage": 20},
                {"key": "wood", "name": "Wood", "base_storage": 15}
            ],
            "unique_resources": [
                {"key": "bronze", "name": "Bronze", "base_storage": 5}
            ]
        }
        
        # Sample nation data
        self.nation_data = {
            "name": "Test Nation",
            "region": "5f7d1b5e9d3e2a1b8c7d6e5f",
            "stability": "Balanced",
            "infamy": 10,
            "temporary_karma": 5,
            "rolling_karma": 2,
            "money": 1000,
            "primary_race": "5f7d1b5e9d3e2a1b8c7d6e5f",
            "primary_culture": "5f7d1b5e9d3e2a1b8c7d6e5f",
            "primary_religion": "5f7d1b5e9d3e2a1b8c7d6e5f",
            "current_territory": 15,
            "road_usage": 3,
            "overlord": "",
            "vassal_type": "None",
            "compliance": "None",
            "origin": "Settled",
            "government_type": "Autocracy",
            "succession_type": "Inherited",
            "resource_storage": {
                "food": 10,
                "wood": 5,
                "bronze": 2
            },
            "jobs": {
                "farmer": 5,
                "miner": 3
            },
            "districts": ["farm", "mine", ""],
            "district_slots": 3,
            "job_details": {
                "farmer": {
                    "display_name": "Farmer",
                    "upkeep": {"food": 1},
                    "production": {"food": 3}
                },
                "miner": {
                    "display_name": "Miner",
                    "upkeep": {"food": 1},
                    "production": {"stone": 2}
                }
            }
        }
        
        # Sample dropdown options
        self.dropdown_options = {
            "region": [{"_id": "5f7d1b5e9d3e2a1b8c7d6e5f", "name": "Test Region"}],
            "primary_race": [{"_id": "5f7d1b5e9d3e2a1b8c7d6e5f", "name": "Test Race"}],
            "primary_culture": [{"_id": "5f7d1b5e9d3e2a1b8c7d6e5f", "name": "Test Culture"}],
            "primary_religion": [{"_id": "5f7d1b5e9d3e2a1b8c7d6e5f", "name": "Test Religion"}],
            "overlord": [{"_id": "5f7d1b5e9d3e2a1b8c7d6e5f", "name": "Test Overlord"}]
        }

    def test_form_creation(self):
        """Test creating a form from nation data"""
        with self.app.app_context():
            form = EnhancedNationEditForm.create_from_nation(
                self.nation_data, 
                self.nations_schema, 
                self.json_data,
                self.dropdown_options
            )
            
            # Check that the form has the expected fields
            self.assertTrue(hasattr(form, "name"))
            self.assertTrue(hasattr(form, "region"))
            self.assertTrue(hasattr(form, "stability"))
            self.assertTrue(hasattr(form, "infamy"))
            self.assertTrue(hasattr(form, "resource_storage"))
            self.assertTrue(hasattr(form, "jobs"))
            self.assertTrue(hasattr(form, "districts"))
            
            # Check field values
            self.assertEqual(form.name.data, "Test Nation")
            self.assertEqual(form.infamy.data, 10)
            self.assertEqual(form.stability.data, "Balanced")
            
            # Check resource storage fields
            self.assertEqual(len(form.resource_storage), 3)
            self.assertEqual(form.resource_storage[0].key.data, "food")
            self.assertEqual(form.resource_storage[0].value.data, 10)
            
            # Check job fields
            self.assertEqual(len(form.jobs), 2)
            job_keys = [job.key.data for job in form.jobs]
            self.assertIn("farmer", job_keys)
            self.assertIn("miner", job_keys)
            
            # Check district fields
            self.assertEqual(len(form.districts), 3)
            self.assertEqual(form.districts[0].data, "farm")
            self.assertEqual(form.districts[1].data, "mine")
            self.assertEqual(form.districts[2].data, "")

    def test_form_to_dict(self):
        """Test converting form data to dictionary"""
        with self.app.app_context():
            form = EnhancedNationEditForm.create_from_nation(
                self.nation_data, 
                self.nations_schema, 
                self.json_data,
                self.dropdown_options
            )
            
            # Convert form to dictionary
            form_dict = form.to_dict()
            
            # Check basic fields
            self.assertEqual(form_dict["name"], "Test Nation")
            self.assertEqual(form_dict["infamy"], 10)
            self.assertEqual(form_dict["stability"], "Balanced")
            
            # Check resource storage
            self.assertEqual(form_dict["resource_storage"]["food"], 10)
            self.assertEqual(form_dict["resource_storage"]["wood"], 5)
            self.assertEqual(form_dict["resource_storage"]["bronze"], 2)
            
            # Check jobs
            self.assertEqual(form_dict["jobs"]["farmer"], 5)
            self.assertEqual(form_dict["jobs"]["miner"], 3)
            
            # Check districts
            self.assertEqual(form_dict["districts"], ["farm", "mine"])

    def test_resource_storage_form(self):
        """Test the ResourceStorageForm"""
        with self.app.app_context():
            form = ResourceStorageForm(key="food", name="Food", value=10)
            self.assertEqual(form.key.data, "food")
            self.assertEqual(form.name.data, "Food")
            self.assertEqual(form.value.data, 10)

    def test_job_assignment_form(self):
        """Test the JobAssignmentForm"""
        with self.app.app_context():
            form = JobAssignmentForm(key="farmer", display_name="Farmer", value=5)
            self.assertEqual(form.key.data, "farmer")
            self.assertEqual(form.display_name.data, "Farmer")
            self.assertEqual(form.value.data, 5)

if __name__ == "__main__":
    unittest.main()
