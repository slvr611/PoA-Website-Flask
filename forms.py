from flask_wtf import FlaskForm
from wtforms import Field, StringField, TextAreaField, IntegerField, FloatField, BooleanField, SelectField, SelectMultipleField
from wtforms import FieldList, FormField, HiddenField, SubmitField, MultipleFileField, Form
from wtforms import widgets
from wtforms.validators import DataRequired, NumberRange, Optional, ValidationError
from bson import ObjectId
from app_core import json_data, category_data, land_unit_json_files, naval_unit_json_files
import json
from copy import deepcopy

class FormGenerator:
    """Singleton class to manage form generation and caching"""
    def __init__(self):
        self.form_cache = {}
        
    def get_form(self, data_type, schema, item=None, formdata=None):
        """Gets or creates a form instance"""
        if data_type == "nations":
            job_details = {}
            land_unit_details = {}
            naval_unit_details = {}
            if item:
                job_details = item.get("job_details", {})
                land_unit_details = item.get("land_unit_details", {})
                naval_unit_details = item.get("naval_unit_details", {})
            return NationForm.create_form(schema, item, formdata, job_details, land_unit_details, naval_unit_details)

        elif data_type == "jobs":
            job_details = {}
            if item:
                job_details = item.get("job_details", {})
            return JobForm.create_form(schema, item, formdata, job_details)

        elif data_type == "new_character":
            return NewCharacterForm.create_form(schema, item, formdata)

        return DynamicSchemaForm.create_form(schema, item, formdata)

class ResourceStorageDict(Form):
    """Form for handling resource storage as a dictionary"""
    
    class Meta:
        csrf = False
    
    @classmethod
    def create_form_class(cls):
        general_resources = json_data.get("general_resources", [])
        for resource in general_resources:
            field = IntegerField(resource["name"], default=0)
            setattr(cls, resource["key"], field)
        
        unique_resources = json_data.get("unique_resources", [])
        for resource in unique_resources:
            field = IntegerField(resource["name"], default=0)
            setattr(cls, resource["key"], field)
        
        return cls

    def load_form_from_item(self, item, schema):
        # Handle resource storage
        if item:
            general_resources = json_data.get("general_resources", [])
            for resource in general_resources:
                resource_field = getattr(self, resource["key"], None)
                resource_field.data = int(item.get(resource["key"], 0))
            
            # Unique resources
            unique_resources = json_data.get("unique_resources", [])
            for resource in unique_resources:
                resource_field = getattr(self, resource["key"], None)
                resource_field.data = int(item.get(resource["key"], 0))


class TerritoryTerrainDict(Form):
    """Form for handling territory terrain as a dictionary"""
    
    class Meta:
        csrf = False
    
    @classmethod
    def create_form_class(cls):
        terrains = json_data.get("terrains", {})
        for terrain, details in terrains.items():
            field = IntegerField(details["display_name"], validators=[NumberRange(min=0)], default=0)
            setattr(cls, terrain, field)
        
        return cls

    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""
        if not item:
            return
        for field_name, field in self._fields.items():
                if isinstance(field, SelectField) and field.data:
                    field.data = str(field.data)
                    
                # Handle nested data structures
                if field_name in item:
                    field_value = item[field_name]
                    
                    if isinstance(field_value, ObjectId):
                        field.data = str(field_value)
                    elif isinstance(field, FieldList):
                        # Clear existing entries
                        while len(field.entries) > 0:
                            field.pop_entry()
                            
                        # Add new entries from the data
                        for value in field_value:
                            if isinstance(value, dict):
                                # For object arrays
                                field.append_entry(value)
                            elif isinstance(value, ObjectId):
                                # For linked object arrays
                                field.append_entry(str(value))
                            else:
                                # For simple value arrays
                                field.append_entry(value)
                    elif isinstance(field, FormField):
                        field.load_form_from_item(field_value, schema)
                    else:
                        
                        field.data = field_value

class JobAssignmentDict(Form):
    """Form for handling job assignment as a dictionary"""
    
    class Meta:
        csrf = False

    @classmethod
    def create_form_class(cls, job_details):
        for job in job_details.keys():
            field = IntegerField(job, validators=[NumberRange(min=0)], default=0)
            setattr(cls, job, field)
        
        return cls
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for field_name, field in self._fields.items():
                if isinstance(field, SelectField) and field.data:
                    field.data = str(field.data)
                    
                # Handle nested data structures
                if field_name in item:
                    field_value = item[field_name]
                    
                    if isinstance(field_value, ObjectId):
                        field.data = str(field_value)
                    elif isinstance(field, FieldList):
                        # Clear existing entries
                        while len(field.entries) > 0:
                            field.pop_entry()
                            
                        # Add new entries from the data
                        for value in field_value:
                            if isinstance(value, dict):
                                # For object arrays
                                field.append_entry(value)
                            elif isinstance(value, ObjectId):
                                # For linked object arrays
                                field.append_entry(str(value))
                            else:
                                # For simple value arrays
                                field.append_entry(value)
                    elif isinstance(field, FormField):
                        field.load_form_from_item(field_value, schema)
                    else:
                        field.data = field_value

class LandUnitAssignmentDict(Form):
    """Form for handling Land Unit assignment as a dictionary"""
    
    class Meta:
        csrf = False

    @classmethod
    def create_form_class(cls, unit_details):
        for unit in unit_details.keys():
            field = IntegerField(unit, validators=[NumberRange(min=0)], default=0)
            setattr(cls, unit, field)
        
        return cls
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for unit_key, unit_val in item.items():
            unit_field = getattr(self, unit_key, None)
            if unit_field:
                unit_field.data = unit_val


class NavalUnitAssignmentDict(Form):
    """Form for handling Naval Unit assignment as a dictionary"""
    
    class Meta:
        csrf = False

    @classmethod
    def create_form_class(cls, unit_details):
        for unit in unit_details.keys():
            field = IntegerField(unit, validators=[NumberRange(min=0)], default=0)
            setattr(cls, unit, field)
        
        return cls
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for unit_key, unit_val in item.items():
            unit_field = getattr(self, unit_key, None)
            if unit_field:
                unit_field.data = unit_val

class ModifierForm(Form):
    """Form for handling nation/character modifiers as a dictionary"""
    
    field = StringField("Field", validators=[DataRequired()])
    value = FloatField("Value", default=1)
    duration = IntegerField("Duration", validators=[NumberRange()], default=-1)
    source = StringField("Source", validators=[DataRequired()])

    class Meta:
        csrf = False
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for field_name, field in self._fields.items():
                if isinstance(field, SelectField) and field.data:
                    field.data = str(field.data)
                    
                # Handle nested data structures
                if field_name in item:
                    field_value = item[field_name]
                    
                    if isinstance(field_value, ObjectId):
                        field.data = str(field_value)
                    elif isinstance(field, FieldList):
                        # Clear existing entries
                        while len(field.entries) > 0:
                            field.pop_entry()
                            
                        # Add new entries from the data
                        for value in field_value:
                            if isinstance(value, dict):
                                # For object arrays
                                field.append_entry(value)
                            elif isinstance(value, ObjectId):
                                # For linked object arrays
                                field.append_entry(str(value))
                            else:
                                # For simple value arrays
                                field.append_entry(value)
                    elif isinstance(field, FormField):
                        field.load_form_from_item(field_value, schema)
                    else:
                        field.data = field_value

class ProgressQuestForm(Form):
    """Form for handling progress quests as a dictionary"""
    
    quest_name = StringField("Quest Name", validators=[DataRequired()])
    bonus_progress_per_tick = IntegerField("Bonus Progress Per Tick", validators=[NumberRange()], default=0)
    current_progress = IntegerField("Current Progress", validators=[NumberRange()], default=0)
    required_progress = IntegerField("Required Progress", validators=[NumberRange()], default=0)
    link = StringField("Link", validators=[DataRequired()])
    slot = SelectField("Slot", choices=[], default="no_slot")

    class Meta:
        csrf = False
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for field_name, field in self._fields.items():
                if isinstance(field, SelectField) and field.data:
                    field.data = str(field.data)
                    
                # Handle nested data structures
                if field_name in item:
                    field_value = item[field_name]
                    
                    if isinstance(field_value, ObjectId):
                        field.data = str(field_value)
                    elif isinstance(field, FieldList):
                        # Clear existing entries
                        while len(field.entries) > 0:
                            field.pop_entry()
                            
                        # Add new entries from the data
                        for value in field_value:
                            if isinstance(value, dict):
                                # For object arrays
                                field.append_entry(value)
                            elif isinstance(value, ObjectId):
                                # For linked object arrays
                                field.append_entry(str(value))
                            else:
                                # For simple value arrays
                                field.append_entry(value)
                    elif isinstance(field, FormField):
                        field.load_form_from_item(field_value, schema)
                    else:
                        field.data = field_value        

class DistrictDict(Form):
    """Form for handling each district as a dictionary"""
    
    type = SelectField("District Type", choices=[("", "None")], default="")
    node = SelectField("Node Type", choices=[("", "None")], default="")

    class Meta:
        csrf = False
    
    def populate_linked_fields(self, type_options=[], node_options=[]):
        """Populates all linked fields with their options"""
        self.type.choices = []
        self.node.choices = []
        for type in type_options:
            self.type.choices.append(type)
        for node in node_options:
            self.node.choices.append(node)
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for field_name, field in self._fields.items():
                if isinstance(field, SelectField) and field.data:
                    field.data = str(field.data)
                    
                # Handle nested data structures
                if field_name in item:
                    field_value = item[field_name]
                    
                    if isinstance(field_value, ObjectId):
                        field.data = str(field_value)
                    elif isinstance(field, FieldList):
                        # Clear existing entries
                        while len(field.entries) > 0:
                            field.pop_entry()
                            
                        # Add new entries from the data
                        for value in field_value:
                            if isinstance(value, dict):
                                # For object arrays
                                field.append_entry(value)
                            elif isinstance(value, ObjectId):
                                # For linked object arrays
                                field.append_entry(str(value))
                            else:
                                # For simple value arrays
                                field.append_entry(value)
                    elif isinstance(field, FormField):
                        field.load_form_from_item(field_value, schema)
                    else:
                        field.data = field_value

class CityDict(Form):
    """Form for handling each City as a dictionary"""
    
    name = StringField("City Name")
    type = SelectField("City Type")
    node = SelectField("Node Type")
    wall = SelectField("Wall Type")

    class Meta:
        csrf = False
    
    def populate_linked_fields(self, type_options=[], node_options=[], wall_options=[]):
        """Populates all linked fields with their options"""
        self.type.choices = []
        self.node.choices = []
        self.wall.choices = []
        for type in type_options:
            self.type.choices.append(type)
        for node in node_options:
            self.node.choices.append(node)
        for wall in wall_options:
            self.wall.choices.append(wall)

    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for field_name, field in self._fields.items():
                if isinstance(field, SelectField) and field.data:
                    field.data = str(field.data)
                    
                # Handle nested data structures
                if field_name in item:
                    field_value = item[field_name]
                    
                    if isinstance(field_value, ObjectId):
                        field.data = str(field_value)
                    elif isinstance(field, FieldList):
                        # Clear existing entries
                        while len(field.entries) > 0:
                            field.pop_entry()
                            
                        # Add new entries from the data
                        for value in field_value:
                            if isinstance(value, dict):
                                # For object arrays
                                field.append_entry(value)
                            elif isinstance(value, ObjectId):
                                # For linked object arrays
                                field.append_entry(str(value))
                            else:
                                # For simple value arrays
                                field.append_entry(value)
                    elif isinstance(field, FormField):
                        field.load_form_from_item(field_value, schema)
                    else:
                        field.data = field_value

class ExternalModifierForm(Form):
    """Generic form for handling non-nation modifiers based on schema"""
    class Meta:
        csrf = False

    type = SelectField("Type", choices=[("nation", "nation"), ("character", "character")])
    modifier = StringField("Modifier", validators=[])
    value = FloatField("Value")

class IndividualTechDict(Form):
    """Form for handling individual tech investments"""
    
    class Meta:
        csrf = False
    
    investing = IntegerField("Investing", default=0)
    cost = IntegerField("Cost", default=0)
    researched = BooleanField("Researched", default=False)

    def initialize_cost(self, nation, name):
        self.cost.data = json_data.get("tech", {}).get(name, {}).get("cost", 0)
        self.cost.data += nation.get("technology_cost_modifier", 0)
        self.cost.data = max(self.cost.data, nation.get("technology_cost_minimum", 0))

    def load_form_from_item(self, name, item, schema):
        """Loads form data from a database item"""
        if item:
            self.investing.data = item.get("investing", 0)
            self.cost.data = item.get("cost", json_data.get("tech", {}).get(name, {}).get("cost", 0))
            self.researched.data = item.get("researched", False)

class OverallTechDict(Form):
    """Form for handling nation tech"""
    
    class Meta:
        csrf = False
    
    @classmethod
    def create_form_class(cls):
        for tech_id, tech_data in json_data.get("tech", {}).items():
            setattr(cls, tech_id, FormField(IndividualTechDict))
        return cls
    
    def load_form_from_item(self, nation, schema):
        """Load tech data from nation's techs"""

        if not nation:
            return

        techs = nation.get("technologies", None)

        if not (techs and isinstance(techs, dict)):
            techs = {"political_philosophy": {"researched": True}}

        for tech_id, tech_data in json_data.get("tech", {}).items():
            field = getattr(self, tech_id, None)
            if field:
                field.initialize_cost(nation, tech_id)

        for tech_id, tech_data in techs.items():
            field = getattr(self, tech_id, None)
            if field:
                field.load_form_from_item(tech_id, tech_data, schema)
    
    def to_json(self):
        """Convert form data to JSON-serializable format for JavaScript"""
        return wtform_to_json(self)

class BaseSchemaForm(FlaskForm):
    """Base form class with common functionality"""
    reason = TextAreaField("Reason")
    submit = SubmitField("Save")
    
    def __init__(self, *args, **kwargs):
        self._schema = kwargs.pop('schema', None)
        super().__init__(*args, **kwargs)
    
    def validate_progress_quests(self, field):
        """Validate progress quest slot assignments"""
        if not hasattr(self, 'progress_quests'):
            return
            
        # Get available slots for this entity
        nation_data = {}
        for field_name, form_field in self._fields.items():
            if hasattr(form_field, 'data') and form_field.data is not None:
                nation_data[field_name] = form_field.data
        
        # Use the schema passed to the form
        schema = self._schema
        if not schema:
            # Fallback to the old method if schema wasn't passed
            from helpers.data_helpers import get_data_on_category
            schema, _ = get_data_on_category("nations")
        
        # Check if this is a nation or another entity type
        entity_type = schema.get("title", "").lower()
        if entity_type != "nations":
            # Non-nation entities: simple validation
            available_slot_options = self.get_available_slots(nation_data, schema)
            valid_slot_values = [slot[0] for slot in available_slot_options]
            
            # Update slot choices
            for quest in self.progress_quests:
                if hasattr(quest, 'slot'):
                    quest.slot.choices = available_slot_options
            
            # Validate max 3 progress slots
            progress_slot_count = 0
            for quest in self.progress_quests:
                if hasattr(quest, 'slot') and quest.slot.data:
                    slot_type = quest.slot.data
                    
                    # Check if slot type is valid
                    if slot_type not in valid_slot_values:
                        slot_name = slot_type.replace("_", " ").title()
                        raise ValidationError(f"Invalid slot type '{slot_name}' for this entity type.")
                    
                    if quest.slot.data == "1_progress_slot":
                        progress_slot_count += 1
            
            if progress_slot_count > 3:
                raise ValidationError("Non-nation entities can only have 3 progress slots maximum.")
            return
        
        # Nation-specific validation
        available_slot_options = self.get_available_slots(nation_data, schema)
        valid_slot_values = [slot[0] for slot in available_slot_options]
        
        # Update slot choices for all progress quest fields
        for quest in self.progress_quests:
            if hasattr(quest, 'slot'):
                quest.slot.choices = available_slot_options
        
        # Get centralization law modifiers for slot limits
        centralization_law = nation_data.get("centralization_law", "Standard")
        centralization_modifiers = schema.get("properties", {}).get("centralization_law", {}).get("laws", {}).get(centralization_law, {})
        
        # Count used slots by type
        slot_usage = {}
        for quest in self.progress_quests:
            if hasattr(quest, 'slot') and quest.slot.data:
                slot_type = quest.slot.data
                
                # Check if slot type is valid
                if slot_type not in valid_slot_values:
                    slot_name = slot_type.replace("_", " ").title()
                    raise ValidationError(f"Invalid slot type '{slot_name}' for current centralization law.")
                
                if slot_type != "no_slot" and not slot_type.endswith("_spell_slot"):
                    slot_usage[slot_type] = slot_usage.get(slot_type, 0) + 1
        
        # Validate against limits using the schema data
        for slot_type, used_count in slot_usage.items():
            slot_key = slot_type.replace("_slot", "_slots")
            available_count = centralization_modifiers.get(slot_key, 0)
            if used_count > available_count:
                slot_name = slot_type.replace("_", " ").title()
                raise ValidationError(f"Too many quests assigned to {slot_name}. Available: {available_count}, Used: {used_count}")

    def get_available_slots(self, nation, schema):
        """Get available slot options based on nation's centralization law and modifiers"""
        from calculations.field_calculations import calculate_all_fields
        
        # Check if this is actually a nation or another entity type
        entity_type = schema.get("title", "").lower()
        if entity_type != "nations":
            # Non-nation entities get 3 basic progress slots + spell slots
            return [
                ("no_slot", "No Slot"),
                ("1_progress_slot", "1 Progress Slot"),
                ("tier_1_spell_slot", "Tier 1 Spell Slot"),
                ("tier_2_spell_slot", "Tier 2 Spell Slot"),
                ("tier_3_spell_slot", "Tier 3 Spell Slot")
            ]
        
        # Recalculate nation to get current modifiers
        calculated_nation = calculate_all_fields(nation, schema, "nation")
        
        # Build available slots
        available_slots = [("no_slot", "No Slot")]
        
        # Add progress slots based on centralization
        slot_types = [
            ("0_progress_slots", "0 Progress Slot"),
            ("1_progress_slots", "1 Progress Slot"), 
            ("2_progress_slots", "2 Progress Slot"),
            ("3_progress_slots", "3 Progress Slot"),
            ("4_progress_slots", "4 Progress Slot")
        ]
        
        print("Calculated Nation:", calculated_nation)

        for slot_key, slot_name in slot_types:
            slot_count = calculated_nation.get(slot_key, 0)
            print(f"Slot Key: {slot_key}, Slot Count: {slot_count}")
            if slot_count > 0:
                available_slots.append((slot_key.replace("_slots", "_slot"), slot_name))
        
        # Always add spell slots
        available_slots.extend([
            ("tier_1_spell_slot", "Tier 1 Spell Slot"),
            ("tier_2_spell_slot", "Tier 2 Spell Slot"),
            ("tier_3_spell_slot", "Tier 3 Spell Slot")
        ])
        
        return available_slots

    def populate_select_field(self, field_name, field, schema, dropdown_options):
        """Populates a select field with options"""

        if isinstance(field, FieldList):
            for entry in field.entries:
                self.populate_select_field(field_name, entry, schema, dropdown_options)
            return

        if not isinstance(field, SelectField):
            return

        field_schema = schema.get("properties", {}).get(field_name, {})
        none_result = field_schema.get("noneResult", "None")
        default_options = deepcopy(field_schema.get("default_options", []))

        if field_name == "owner": #For artifacts
            regions = category_data["regions"]["database"].find({}, {"name": 1}).sort("name", 1)
            for region in regions:
                default_options.append("Lost in " + region["name"])
        
        choices = [("", none_result)] + [(option, option) for option in default_options]
        if field_name == "districts":
            choices += [(district, json_data["mercenary_districts"][district]["display_name"]) for district in json_data["mercenary_districts"]]
        
        elif field_name == "titles":
            choices += [(title, json_data["titles"][title]["display_name"]) for title in json_data["titles"]]
        
        elif field_name == "land_units" or field_name == "naval_units":
            combined_data = {}
            json_files = []
            if field_name == "land_units":
                json_files = land_unit_json_files
            else:
                json_files = naval_unit_json_files
            for file_name in json_files:
                combined_data.update(json_data[file_name])
            choices += [(key, data.get("display_name", key))
                        for key, data in combined_data.items()]
        
        elif field_name in dropdown_options:
            for option in dropdown_options[field_name]:
                name = ""
                if "name" in dropdown_options[field_name][0]:
                    name = option["name"]
                elif "display_name" in dropdown_options[field_name][0]:
                    name = option["display_name"]
                else:
                    name = option["_id"]
                choices += [(str(option["_id"]), name)]
        
        field.choices = choices

    def populate_linked_fields(self, schema, dropdown_options):
        """Populates all linked fields with their options"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("collections") and field_name in self._fields:
                field = getattr(self, field_name)
                if field:
                    self.populate_select_field(field_name, self[field_name], schema, dropdown_options)
            elif field_schema.get("bsonType") == "array" and (field_schema.get("items", {}).get("bsonType") == "json_district_enum" or field_schema.get("items", {}).get("bsonType") == "json_unit_enum"):
                field = getattr(self, field_name)
                for entry in field.entries:
                    self.populate_select_field(field_name, entry, schema, dropdown_options)
            elif field_schema.get("bsonType") == "json_resource_enum":
                resources = json_data.get("general_resources", []) + json_data.get("unique_resources", [])
                node_choices = [("", "None"), ("luxury", "Luxury")]
                for resource in resources:
                    node_choices.append((resource.get("key", resource), resource.get("name", resource)))
                field = getattr(self, field_name)
                field.choices = node_choices
            
        # Populate progress quest slots for ALL entity types
        if hasattr(self, 'progress_quests'):
            # Get current form data for slot calculation
            nation_data = {}
            for field_name, field in self._fields.items():
                if hasattr(field, 'data') and field.data is not None:
                    nation_data[field_name] = field.data
            
            available_slots = self.get_available_slots(nation_data, schema)
            
            for quest_field in self.progress_quests:
                if hasattr(quest_field, 'slot'):
                    quest_field.slot.choices = available_slots
    
    def load_form_from_item(self, item, schema):
        """Loads form data from a database item with proper type conversion"""

        if not item:
            return

        for field_name, field in self._fields.items():
            if isinstance(field, SelectField) and field.data:
                field.data = str(field.data)
                
            # Handle nested data structures
            field_value = item.get(field_name, None)
            
            if isinstance(field_value, ObjectId):
                field.data = str(field_value)
            elif isinstance(field, FieldList):
                # Clear existing entries
                while len(field.entries) > 0:
                    field.pop_entry()
                
                if field_name == "districts":
                    districts = item.get("districts", [])
                    max_districts = schema.get("properties", {}).get(field_name, {}).get("max_length", 0)
                    if isinstance(max_districts, str):
                        max_districts = item.get(max_districts, 0)
                    
                    if len(districts) < max_districts:
                        districts.extend([""] * (max_districts - len(districts)))
                    
                    if len(districts) > max_districts:
                        districts = districts[:max_districts]

                    for district in districts:
                        field.append_entry(district)
                
                elif field_name == "cities":
                    cities = item.get("cities", [])
                    max_cities = schema.get("properties", {}).get(field_name, {}).get("max_length", 0)
                    if isinstance(max_cities, str):
                        max_cities = int(item.get(max_cities, 0))
                    
                    if len(cities) < max_cities:
                        cities.extend([""] * (max_cities - len(cities)))
                    
                    if len(cities) > max_cities: #TODO: Add support for going over maximum cities
                        cities = cities[:max_cities]
                    
                    for cities in cities:
                        field.append_entry(cities)
                
                elif field_name == "titles":
                    titles = item.get("titles", [])
                    max_titles = schema.get("properties", {}).get(field_name, {}).get("max_length", 0)
                    if isinstance(max_titles, str):
                        max_titles = int(item.get(max_titles, 0))

                    if len(titles) < max_titles:
                        titles.extend([""] * (max_titles - len(titles)))
                    
                    if len(titles) > max_titles:
                        titles = titles[:max_titles]
                    
                    for title in titles:
                        field.append_entry(title)
                    
                elif field_name == "land_units" or field_name == "naval_units":
                    units = item.get(field_name, [])
                    max_units = schema.get("properties", {}).get(field_name, {}).get("max_length", 0)
                    if isinstance(max_units, str):
                        max_units = item.get(max_units, 0)
                    
                    if len(units) < max_units:
                        units.extend([""] * (max_units - len(units)))
                    
                    for unit in units:
                        field.append_entry(unit)
                
                elif field_value is not None:
                    # Add new entries from the data
                    for value in field_value:
                        if isinstance(value, dict):
                            # For object arrays
                            field.append_entry(value)
                        elif isinstance(value, ObjectId):
                            # For linked object arrays
                            field.append_entry(str(value))
                        else:
                            # For simple value arrays
                            field.append_entry(value)
            elif isinstance(field, FormField):
                if field_name == "technologies":
                    field.load_form_from_item(item, schema)
                else:
                    field.load_form_from_item(field_value, schema)
            else:
                # Type conversion based on field type
                try:
                    if isinstance(field, IntegerField) and not isinstance(field_value, int):
                        field.data = int(field_value)
                    elif isinstance(field, FloatField) and not isinstance(field_value, float):
                        field.data = float(field_value)
                    elif isinstance(field, BooleanField) and not isinstance(field_value, bool):
                        field.data = bool(field_value)
                    elif isinstance(field, StringField) and not isinstance(field_value, str):
                        field.data = str(field_value)
                    else:
                        field.data = field_value
                except (ValueError, TypeError):
                    # If conversion fails, use the original value
                    field.data = field_value

    def validate(self, extra_validators=None):
        """Override validate to update progress quest slot choices before validation"""
        # Update progress quest slot choices based on current form data
        if hasattr(self, 'progress_quests'):
            # Get current form data
            nation_data = {}
            for field_name, field in self._fields.items():
                if hasattr(field, 'data') and field.data is not None:
                    nation_data[field_name] = field.data
            
            # Use the schema passed to the form
            schema = self._schema
            if not schema:
                # Fallback to the old method if schema wasn't passed
                from helpers.data_helpers import get_data_on_category
                schema, _ = get_data_on_category("nations")
            
            available_slots = self.get_available_slots(nation_data, schema)
            
            for quest_field in self.progress_quests:
                if hasattr(quest_field, 'slot'):
                    quest_field.slot.choices = available_slots
        
        # Now run the normal validation
        return super().validate(extra_validators)

class DynamicSchemaForm(BaseSchemaForm):
    """Dynamic form generated from JSON schema"""
    
    @classmethod
    def create_form_class(cls, schema):
        """Creates a new form class with fields from schema"""
        form_class = type('GeneratedSchemaForm', (cls,), {})
        
        for field_name, field_schema in schema.get("properties", {}).items():
            if not field_schema.get("calculated", False):
                field = cls.create_field_from_schema(field_name, field_schema, schema)
                if field:
                    setattr(form_class, field_name, field)
                
        return form_class

    @classmethod
    def create_form(cls, schema, item=None, formdata=None):
        """Creates and populates a form instance"""
        form_class = cls.create_form_class(schema)
        
        if formdata:
            form = form_class(formdata=formdata, schema=schema)
        elif item:
            form = form_class(schema=schema)
            form.load_form_from_item(item, schema)
        else:
            form = form_class(schema=schema)
        
        return form

    @classmethod
    def create_field_from_schema(cls, field_name, field_schema, schema):
        """Creates a WTForms field based on schema definition"""
        field_type = field_schema.get("bsonType")
        required = field_name in schema.get("required", [])
        validators = [DataRequired()] if required else [Optional()]
    
        field_args = {
            "label": field_schema.get("label", field_name),
            "description": field_schema.get("description", ""),
            "validators": validators,
            "default": field_schema.get("default")
        }

        if field_type == "string":
            if field_schema.get("long_text"):
                return TextAreaField(**field_args)
            return StringField(**field_args)
        
        elif field_type == "number":
            return IntegerField(**field_args)
        
        elif field_type == "boolean":
            return BooleanField(**field_args)
        
        elif field_type == "enum":
            field_args["choices"] = [(v, v) for v in field_schema.get("enum", [])]

            return SelectField(**field_args)
        
        elif field_type == "json_district_enum":
            field_args["choices"] = [("", "None")] + [(key, data.get("display_name", key))
                for key, data in json_data[field_schema["json_data"]].items()]
            return SelectField(**field_args)
        
        elif field_type == "json_unit_enum":
            combined_data = {}
            json_files = land_unit_json_files + naval_unit_json_files
            for file_name in json_files:
                combined_data.update(json_data[file_name])
            field_args["choices"] = [("", "None")] + [(key, data.get("display_name", key))
                for key, data in combined_data.items()]
            return SelectField(**field_args)
        
        elif field_type == "json_resource_enum":
            resources = json_data.get("general_resources", []) + json_data.get("unique_resources", [])
            node_choices = [("", "None"), ("luxury", "Luxury")]
            for resource in resources:
                node_choices.append((resource.get("key", resource), resource.get("name", resource)))
            field_args["choices"] = node_choices
            return SelectField(**field_args)
        
        elif field_type == "linked_object":
            field = SelectField(**field_args)
            field.none_result = field_schema.get("noneResult", "None")
            field.default_options = field_schema.get("default_options", [])
            return field
        
        elif field_type == "object":
            if field_name == "resource_storage":
                ResourceStorageDict.create_form_class()
                return FormField(ResourceStorageDict)
        
        elif field_type == "array" and not field_schema.get("queryTargetAttribute"): # Hide arrays that are purely to display all objects with a certain variable linked to this object
            # Handle different types of arrays
            items_schema = field_schema.get("items", {})
            items_type = items_schema.get("bsonType")
            
            if field_name == "external_modifiers":
                return FieldList(FormField(ExternalModifierForm), min_entries=0)
            
            elif field_name == "modifiers":
                return FieldList(FormField(ModifierForm), min_entries=0)
            
            elif field_name == "progress_quests":
                return FieldList(FormField(ProgressQuestForm), min_entries=0)

            elif items_type == "string":
                # For simple string arrays
                return FieldList(StringField("Value"), min_entries=0)
            
            elif items_type == "json_district_enum":
                subfield = SelectField("Value")
                subfield.choices = [("", "None")] + [(key, data.get("display_name", key))
                    for key, data in json_data[items_schema["json_data"]].items()]
                
                return FieldList(subfield, min_entries=0)

            elif items_type == "json_unit_enum":
                subfield = SelectField("Value")

                combined_data = {}
                for file_name in items_schema["json_data"]:
                    combined_data.update(json_data[file_name])
                subfield.choices = [("", "None")] + [(key, data.get("display_name", key))
                for key, data in combined_data.items()]
                
                return FieldList(subfield, min_entries=0)
                
            elif items_type == "linked_object":
                # For arrays of linked objects
                # subfield = SelectField("Value")
                # subfield.none_result = items_schema.get("noneResult", "None")
                # subfield.default_options = items_schema.get("default_options", [])
                # return FieldList(subfield, min_entries=0)
                pass
                
            elif items_type == "object":
                # For arrays of objects, create a nested form
                class DynamicSubForm(FlaskForm):
                    class Meta:
                        csrf = False
                        
                # Add fields from the items schema
                for prop_name, prop_schema in items_schema.get("properties", {}).items():
                    field = cls.create_field_from_schema(prop_name, prop_schema, items_schema)
                    if field:
                        setattr(DynamicSubForm, prop_name, field)
                        
                return FieldList(FormField(DynamicSubForm), min_entries=0)
        
        return None

class NationForm(BaseSchemaForm):
    """Specialized form for nations"""
    # Basic fields
    name = StringField("Name", validators=[DataRequired()])
    region = SelectField("Region", choices=[])
    prestige = IntegerField("Prestige", validators=[NumberRange(min=0)], default=0)
    stability = SelectField("Stability", choices=[], default="Balanced")
    infamy = IntegerField("Infamy", validators=[NumberRange(min=0)], default=0)
    temporary_karma = IntegerField("Temporary Karma", default=0)
    money = IntegerField("Money", default=0)
    
    # Demographics fields
    primary_race = SelectField("Primary Race", choices=[])
    primary_culture = SelectField("Primary Culture", choices=[])
    primary_religion = SelectField("Primary Religion", choices=[])
    
    # Territory fields
    road_usage = IntegerField("Road Usage", validators=[NumberRange(min=0)], default=0)
    
    # Vassalship fields
    overlord = SelectField("Overlord", choices=[])
    vassal_type = SelectField("Vassal Type", choices=[])
    compliance = SelectField("Compliance", choices=[])
    concessions = HiddenField("Concessions")
    
    # Lists
    districts = FieldList(FormField(DistrictDict), min_entries=0)
    imperial_district = FormField(DistrictDict)
    cities = FieldList(FormField(CityDict), min_entries=0)
    
    # Misc fields
    origin = SelectField("Origin", choices=[])
    modifiers = FieldList(FormField(ModifierForm), min_entries=0)
    temperament = SelectField("Temperament", choices=[], default="Neutral")

    progress_quests = FieldList(FormField(ProgressQuestForm), min_entries=0)

    notes = TextAreaField("Notes")

    reason = TextAreaField("Reason")

    # Add dynamic law fields from schema
    @classmethod
    def create_form_class(cls, schema, job_details, land_unit_details, naval_unit_details):
        """Creates a form class with additional fields from schema"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("bsonType") == "enum" and not hasattr(cls, field_name):
                choices = [(v, v) for v in field_schema.get("enum", [])]
                field = SelectField(
                    field_schema.get("label", field_name),
                    choices=choices,
                    default=field_schema.get("default")
                )
                setattr(cls, field_name, field)

        ResourceStorageDict.create_form_class()
        cls.resource_storage = FormField(ResourceStorageDict)

        TerritoryTerrainDict.create_form_class()
        cls.territory_types = FormField(TerritoryTerrainDict)

        JobAssignmentDict.create_form_class(job_details)
        cls.jobs = FormField(JobAssignmentDict)

        OverallTechDict.create_form_class()
        cls.technologies = FormField(OverallTechDict)

        LandUnitAssignmentDict.create_form_class(land_unit_details)
        cls.land_units = FormField(LandUnitAssignmentDict)
        NavalUnitAssignmentDict.create_form_class(naval_unit_details)
        cls.naval_units = FormField(NavalUnitAssignmentDict)

        return cls
    
    @classmethod
    def create_form(cls, schema, nation=None, formdata=None, job_details={}, land_unit_details={}, naval_unit_details={}):
        """Creates and populates a nation form"""
        # First create the form class with all fields
        form_class = cls.create_form_class(schema, job_details, land_unit_details, naval_unit_details)
        
        # Create form instance
        if formdata:
            form = form_class(formdata=formdata)
        elif nation:
            form = form_class()
            form.load_form_from_item(nation, schema)
        else:
            form = form_class()
        
        return form
    
    def populate_linked_fields(self, schema, dropdown_options):
        """Populates all linked fields with their options"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("bsonType") == "linked_object":
                self.populate_select_field(field_name, self[field_name], schema, dropdown_options)
            elif field_schema.get("bsonType") == "enum":
                choices = [(v, v) for v in field_schema.get("enum", [])]
                field = getattr(self, field_name)
                field.choices = choices
        
        # Populate progress quest slots based on nation's centralization - do this FIRST
        if hasattr(self, 'progress_quests'):
            # Get current form data for slot calculation
            nation_data = {}
            for field_name, field in self._fields.items():
                if hasattr(field, 'data') and field.data is not None:
                    nation_data[field_name] = field.data
            
            available_slots = self.get_available_slots(nation_data, schema)
            
            for quest_field in self.progress_quests:
                if hasattr(quest_field, 'slot'):
                    quest_field.slot.choices = available_slots
        
        node_choices = [("", "None"), ("luxury", "Luxury")]
        general_resources = json_data.get("general_resources", [])
        for resource in general_resources:
            node_choices.append((resource.get("key", resource), resource.get("name", resource)))
        
        unique_resources = json_data.get("unique_resources", [])
        for resource in unique_resources:
            node_choices.append((resource.get("key", resource), resource.get("name", resource)))

        #Handle Districts separately
        district_choices = [("", "Empty Slot")]
        districts = json_data.get("nation_districts", {})
        for district_key, district_data in districts.items():
            district_choices.append((district_key, district_data["display_name"]))
                
        for district_field in self.districts:
            district_field.form.populate_linked_fields(type_options=district_choices, node_options=node_choices)
        
        #Handle Imperial Districts separately
        imperial_district_choices = [("", "Empty Slot")]
        imperial_districts = json_data.get("nation_imperial_districts", {})
        for district_key, district_data in imperial_districts.items():
            imperial_district_choices.append((district_key, district_data["display_name"]))
        
        self.imperial_district.form.populate_linked_fields(type_options=imperial_district_choices, node_options=node_choices)
        
        #Handle Cities separately        
        city_choices = [("", "Empty Slot")]
        cities = json_data.get("cities", {})
        for city_key, city_data in cities.items():
            city_choices.append((city_key, city_data["display_name"]))
        
        wall_choices = [("", "No Walls")]
        walls = json_data.get("walls", {})
        for wall_key, wall_data in walls.items():
            wall_choices.append((wall_key, wall_data["display_name"]))
                
        for city_field in self.cities:
            city_field.form.populate_linked_fields(type_options=city_choices, node_options=node_choices, wall_options=wall_choices)

class JobForm(BaseSchemaForm):
    """Form to change jobs without needing to request a full nation edit"""
    # Add dynamic law fields from schema
    @classmethod
    def create_form_class(cls, schema, job_details):
        """Creates a form class with additional fields from schema"""
        JobAssignmentDict.create_form_class(job_details)
        cls.jobs = FormField(JobAssignmentDict)

        return cls
    
    @classmethod
    def create_form(cls, schema, nation=None, formdata=None, job_details={}):
        """Creates and populates a nation form"""
        # First create the form class with all fields
        form_class = cls.create_form_class(schema, job_details)
        
        # Create form instance
        if formdata:
            form = form_class(formdata=formdata)
        elif nation:
            form = form_class()
            form.load_form_from_item(nation.get("jobs", {}), schema)
        else:
            form = form_class()
        
        return form

    def load_form_from_item(self, item, schema):
        """Loads form data from a database item"""

        if not item:
            return

        for job_key, job_val in item.items():
                job_field = getattr(self.jobs, job_key, None)
                if job_field:
                    job_field.data = job_val
    
    def populate_linked_fields(self, schema, dropdown_options):
        """Populates all linked fields with their options"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("collections"):
                self.populate_select_field(field_name, self[field_name], schema, dropdown_options)

class NewCharacterForm(BaseSchemaForm):
    """Form for creating a new character"""
    name = StringField("Name", validators=[DataRequired()])
    
    player = SelectField("Player", choices=[])
    creator = SelectField("Creator", choices=[])

    character_type = SelectField("Character Type", choices=[])
    character_subtype = SelectField("Character Subtype", choices=[])

    race = SelectField("Race", choices=[])
    culture = SelectField("Culture", choices=[])
    religion = SelectField("Religion", choices=[])
    
    ruling_nation_org = SelectField("Ruling Nation/Organization", choices=[])
    region = SelectField("Region", choices=[])

    strengths = SelectMultipleField("Strengths", 
        choices=[
            ('rulership', 'Rulership'),
            ('cunning', 'Cunning'),
            ('charisma', 'Charisma'),
            ('prowess', 'Prowess'),
            ('magic', 'Magic'),
            ('strategy', 'Strategy')
        ],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False)
    )

    weaknesses = SelectMultipleField("Weaknesses", 
        choices=[
            ('rulership', 'Rulership'),
            ('cunning', 'Cunning'),
            ('charisma', 'Charisma'),
            ('prowess', 'Prowess'),
            ('magic', 'Magic'),
            ('strategy', 'Strategy')
        ],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False)
    )

    random_stats = IntegerField("Random Stats", validators=[NumberRange(min=0)], default=0)

    titles = FieldList(SelectField("Titles", choices=[]), min_entries=3)

    positive_quirk = SelectField("Positive Quirk", choices=[])
    negative_quirk = SelectField("Negative Quirk", choices=[])

    modifiers = FieldList(FormField(ModifierForm), min_entries=0)

    submit = SubmitField("Create Character")

    @classmethod
    def create_form_class(cls, schema):
        """Creates a form class with additional fields from schema"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("bsonType") == "enum" and not field_schema.get("calculated", False):
                choices = [(v, v) for v in field_schema.get("enum", [])]
                field = SelectField(
                    field_schema.get("label", field_name),
                    choices=choices,
                    default=field_schema.get("default")
                )
                setattr(cls, field_name, field)

        return cls


    @classmethod
    def create_form(cls, schema, item=None, formdata=None):
        """Creates and populates a nation form"""
        # First create the form class with all fields
        form_class = cls.create_form_class(schema)
        
        # Create form instance
        if formdata:
            form = form_class(formdata=formdata)
        elif item:
            form = form_class()
            form.load_form_from_item(item, schema)
        else:
            form = form_class()

        return form

class ConcessionDict(Form):
    """Form for handling nation concessions as a dictionary"""
    resource = StringField("Resource")
    amount = IntegerField("Amount", default=0)
    
    class Meta:
        csrf = False

# Global form generator instance
form_generator = FormGenerator()



def wtform_to_json(form):
    """Convert a WTForm to a JSON-serializable dictionary
    
    Args:
        form: A WTForm instance
        
    Returns:
        dict: A JSON-serializable dictionary with field data and metadata
    """
    result = {}
    
    for field_name, field in form._fields.items():
        # Skip CSRF token and other special fields
        if field_name in ('csrf_token', 'submit'):
            continue
            
        # Handle nested FormField
        if hasattr(field, 'form'):
            if isinstance(field, FormField):
                result[field_name] = wtform_to_json(field.form)
            elif isinstance(field, FieldList) and len(field.entries) > 0:
                result[field_name] = [wtform_to_json(entry) for entry in field.entries]
        else:
            # Regular field
            field_data = {
                'data': field.data,
                'id': field.id,
                'name': field.name,
                'type': field.type
            }
            
            # Add choices for select fields
            if hasattr(field, 'choices') and field.choices:
                field_data['choices'] = [{'value': value, 'label': label} 
                                        for value, label in field.choices]
                
            result[field_name] = field_data
    
    return result
