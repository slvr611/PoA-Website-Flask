import unittest
import json
import os
from flask import Flask
from forms import form_generator, validate_form_with_jsonschema, DynamicForm, SchemaFormGenerator

class TestDynamicForms(unittest.TestCase):
    """Test cases for the dynamic form generation system"""
    
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
        
        # Create a simplified test schema
        self.test_schema = {
            "bsonType": "object",
            "required": ["name", "type"],
            "properties": {
                "name": {
                    "bsonType": "string",
                    "label": "Name",
                    "description": "The item's name"
                },
                "type": {
                    "bsonType": "enum",
                    "label": "Type",
                    "description": "The item's type",
                    "enum": ["Type A", "Type B", "Type C"]
                },
                "value": {
                    "bsonType": "number",
                    "label": "Value",
                    "description": "The item's value"
                },
                "active": {
                    "bsonType": "boolean",
                    "label": "Active",
                    "description": "Whether the item is active"
                },
                "calculated_field": {
                    "bsonType": "number",
                    "label": "Calculated Field",
                    "description": "A calculated field",
                    "calculated": True
                }
            }
        }
        
        # Create a modified schema to test schema changes
        self.modified_schema = self.test_schema.copy()
        self.modified_schema["properties"]["new_field"] = {
            "bsonType": "string",
            "label": "New Field",
            "description": "A new field added to the schema"
        }
        self.modified_schema["properties"]["type"]["enum"].append("Type D")
        
        # Sample form data
        self.valid_data = {
            "name": "Test Item",
            "type": "Type A",
            "value": 42,
            "active": True
        }
        
        self.invalid_data = {
            "name": "",  # Required field is empty
            "type": "Invalid Type",  # Not in enum
            "value": "not a number",  # Wrong type
            "active": "not a boolean"  # Wrong type
        }

    def test_form_creation(self):
        """Test creating a form from a schema"""
        with self.app.app_context():
            form = form_generator.get_form("test", self.test_schema)
            
            # Check that the form has the expected fields
            self.assertTrue(hasattr(form, "name"))
            self.assertTrue(hasattr(form, "type"))
            self.assertTrue(hasattr(form, "value"))
            self.assertTrue(hasattr(form, "active"))
            
            # Check that calculated fields are not included
            self.assertFalse(hasattr(form, "calculated_field"))
            
            # Check field types
            from wtforms import StringField, SelectField, IntegerField, BooleanField
            self.assertIsInstance(form.name, StringField)
            self.assertIsInstance(form.type, SelectField)
            self.assertIsInstance(form.value, IntegerField)
            self.assertIsInstance(form.active, BooleanField)
            
            # Check that required fields have the DataRequired validator
            from wtforms.validators import DataRequired
            self.assertTrue(any(isinstance(v, DataRequired) for v in form.name.validators))
            self.assertTrue(any(isinstance(v, DataRequired) for v in form.type.validators))
            
            # Check enum choices
            self.assertEqual(form.type.choices, [('Type A', 'Type A'), ('Type B', 'Type B'), ('Type C', 'Type C')])

    def test_form_validation(self):
        """Test form validation"""
        with self.app.app_context():
            # Create a form with valid data
            form = form_generator.get_form("test", self.test_schema, formdata=self.valid_data)
            self.assertTrue(form.validate())
            
            # Create a form with invalid data
            form = form_generator.get_form("test", self.test_schema, formdata=self.invalid_data)
            self.assertFalse(form.validate())

    def test_schema_validation(self):
        """Test JSON schema validation"""
        with self.app.app_context():
            # Create a form with valid data
            form = form_generator.get_form("test", self.test_schema, formdata=self.valid_data)
            valid, error = validate_form_with_jsonschema(form, self.test_schema)
            self.assertTrue(valid)
            self.assertIsNone(error)
            
            # Create a form with invalid data
            form = form_generator.get_form("test", self.test_schema, formdata=self.invalid_data)
            valid, error = validate_form_with_jsonschema(form, self.test_schema)
            self.assertFalse(valid)
            self.assertIsNotNone(error)

    def test_schema_changes(self):
        """Test updating forms based on schema changes"""
        with self.app.app_context():
            # Create a form generator
            generator = SchemaFormGenerator()
            
            # Create a form from the original schema
            form1 = generator.get_form("test", self.test_schema)
            
            # Update the form for schema changes
            form2 = generator.update_form_for_schema_changes("test", self.test_schema, self.modified_schema)
            
            # Check that the new field was added
            self.assertFalse(hasattr(form1, "new_field"))
            self.assertTrue(hasattr(form2, "new_field"))
            
            # Check that the enum choices were updated
            self.assertEqual(len(form1.type.choices), 3)
            self.assertEqual(len(form2.type.choices), 4)
            self.assertEqual(form2.type.choices[3][0], "Type D")

    def test_form_caching(self):
        """Test form caching"""
        with self.app.app_context():
            # Create a form generator
            generator = SchemaFormGenerator()
            
            # Create a form and check that it's cached
            form1 = generator.get_form("test", self.test_schema)
            self.assertTrue("test" in generator.form_cache)
            
            # Create another form for the same schema and check that it uses the cached form class
            form2 = generator.get_form("test", self.test_schema)
            self.assertEqual(form1.__class__, form2.__class__)
            
            # Create a form for a different schema and check that it creates a new form class
            form3 = generator.get_form("test", self.modified_schema)
            self.assertNotEqual(form1.__class__, form3.__class__)

if __name__ == "__main__":
    unittest.main()
