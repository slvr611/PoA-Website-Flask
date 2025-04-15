from flask_wtf import FlaskForm
from wtforms import Field, StringField, TextAreaField, IntegerField, FloatField, BooleanField, SelectField, SelectMultipleField
from wtforms import FieldList, FormField, HiddenField, SubmitField, MultipleFileField, Form
from wtforms import widgets
from wtforms.validators import DataRequired, NumberRange, Optional, ValidationError
from bson import ObjectId
from app_core import json_data, category_data
import json
import copy

class FormGenerator:
    """Singleton class to manage form generation and caching"""
    def __init__(self):
        self.form_cache = {}
        
    def get_form(self, data_type, schema, item=None, formdata=None):
        """Gets or creates a form instance"""
        if data_type == "nations":
            job_details = {}
            if item:
                job_details = item.get("job_details", {})
            return NationForm.create_form(schema, item, formdata, job_details)

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
            field = IntegerField(resource["name"], validators=[NumberRange(min=0)], default=0)
            setattr(cls, resource["key"], field)
        
        unique_resources = json_data.get("unique_resources", [])
        for resource in unique_resources:
            field = IntegerField(resource["name"], validators=[NumberRange(min=0)], default=0)
            setattr(cls, resource["key"], field)
        
        return cls

    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
                    else:
                        field.data = field_value

class NodeDict(Form):
    """Form for handling resources nodes as a dictionary"""
    
    class Meta:
        csrf = False
    
    @classmethod
    def create_form_class(cls):
        general_resources = json_data.get("general_resources", [])
        for resource in general_resources:
            field = IntegerField(resource["name"], validators=[NumberRange(min=0)], default=0)
            setattr(cls, resource["key"], field)
        
        unique_resources = json_data.get("unique_resources", [])
        for resource in unique_resources:
            field = IntegerField(resource["name"], validators=[NumberRange(min=0)], default=0)
            setattr(cls, resource["key"], field)
        
        return cls

    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
                    else:
                        field.data = field_value

class JobAssignmentDict(Form):
    """Form for handling nation modifiers as a dictionary"""
    
    class Meta:
        csrf = False

    @classmethod
    def create_form_class(cls, job_details):
        for job in job_details.keys():
            field = IntegerField(job, validators=[NumberRange(min=0)], default=0)
            setattr(cls, job, field)
        
        return cls
    
    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
                    else:
                        field.data = field_value

class ModifierForm(Form):
    """Form for handling nation/character modifiers as a dictionary"""
    
    field = StringField("Field", validators=[DataRequired()])
    value = IntegerField("Value", validators=[NumberRange()], default=1)
    duration = IntegerField("Duration", validators=[NumberRange()], default=-1)
    source = StringField("Source", validators=[DataRequired()])

    class Meta:
        csrf = False
    
    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
                    else:
                        field.data = field_value

class DistrictDict(Form):
    """Form for handling each district as a dictionary"""
    
    type = SelectField("District Type")
    node = SelectField("Node Type")

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
    
    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
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

    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
                    else:
                        field.data = field_value

class ExternalModifierForm(Form):
    """Generic form for handling non-nation modifiers based on schema"""
    class Meta:
        csrf = False

    type = SelectField("Type", choices=[("nation", "nation"), ("character", "character")])
    modifier = StringField("Modifier", validators=[])
    value = IntegerField("Value", validators=[])

class BaseSchemaForm(FlaskForm):
    """Base form class with common functionality"""
    reason = StringField("Reason")
    submit = SubmitField("Save")

    def populate_select_field(self, field_name, schema, dropdown_options):
        """Populates a select field with options"""
        if not hasattr(self, field_name):
            return

        field = getattr(self, field_name)
        if not isinstance(field, SelectField):
            return

        field_schema = schema.get("properties", {}).get(field_name, {})
        none_result = field_schema.get("noneResult", "None")
        
        choices = [("", none_result)]
        if field_name in dropdown_options:
            choices += [(str(option["_id"]), option["name"]) 
                       for option in dropdown_options[field_name]]
        
        field.choices = choices

    def populate_linked_fields(self, schema, dropdown_options):
        """Populates all linked fields with their options"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("collections"):
                self.populate_select_field(field_name, schema, dropdown_options)
    
    def load_form_from_item(self, item):
        """Loads form data from a database item"""
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
                        field.load_form_from_item(field_value)
                    else:
                        field.data = field_value

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
            form = form_class(formdata=formdata)
        elif item:
            form = form_class()
            form.load_form_from_item(item)
        else:
            form = form_class()
            
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
        
        elif field_type == "linked_object":
            field = SelectField(**field_args)
            field.none_result = field_schema.get("noneResult", "None")
            return field
        
        elif field_type == "array":
            # Handle different types of arrays
            items_schema = field_schema.get("items", {})
            items_type = items_schema.get("bsonType")
            
            if field_name == "external_modifiers":
                return FieldList(FormField(ExternalModifierForm), min_entries=0)
            
            elif field_name == "modifiers":
                return FieldList(FormField(ModifierForm), min_entries=0)

            elif items_type == "string":
                # For simple string arrays
                return FieldList(StringField("Value"), min_entries=0)
                
            elif items_type == "linked_object":
                # For arrays of linked objects
                subfield = SelectField("Value")
                subfield.none_result = items_schema.get("noneResult", "None")
                return FieldList(subfield, min_entries=0)
                
            elif items_type == "object":
                # For arrays of objects, create a nested form
                print("Creating subform")
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
    stability = SelectField("Stability", choices=[])
    infamy = IntegerField("Infamy", validators=[NumberRange(min=0)], default=0)
    temporary_karma = IntegerField("Temporary Karma", validators=[NumberRange(min=0)], default=0)
    rolling_karma = IntegerField("Rolling Karma", validators=[NumberRange(min=0)], default=0)
    money = IntegerField("Money", validators=[NumberRange(min=0)], default=0)
    
    # Demographics fields
    primary_race = SelectField("Primary Race", choices=[])
    primary_culture = SelectField("Primary Culture", choices=[])
    primary_religion = SelectField("Primary Religion", choices=[])
    
    # Territory fields
    current_territory = IntegerField("Current Territory", validators=[NumberRange(min=0)], default=0)
    road_usage = IntegerField("Road Usage", validators=[NumberRange(min=0)], default=0)
    
    # Vassalship fields
    overlord = SelectField("Overlord", choices=[])
    vassal_type = SelectField("Vassal Type", choices=[])
    compliance = SelectField("Compliance", choices=[])
    
    # Lists
    districts = FieldList(FormField(DistrictDict), min_entries=0)
    cities = FieldList(FormField(CityDict), min_entries=0)
    
    # Misc fields
    origin = SelectField("Origin", choices=[])
    modifiers = FieldList(FormField(ModifierForm), min_entries=0)
    temperament = SelectField("Temperament", choices=[])

    # Add dynamic law fields from schema
    @classmethod
    def create_form_class(cls, schema, job_details):
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

        NodeDict.create_form_class()
        cls.resource_nodes = FormField(NodeDict)

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
            form.load_form_from_item(nation)
        else:
            form = form_class()
        
        # Set up default choices for select fields
        form.stability.choices = [(option, option) for option in schema["properties"].get("stability", {}).get("enum", [])] or \
                                [("Balanced", "Balanced"), ("Unstable", "Unstable"), ("Stable", "Stable")]
        form.vassal_type.choices = [(option, option) for option in schema["properties"].get("vassal_type", {}).get("enum", [])] or \
                                  [("None", "None"), ("Tributary", "Tributary"), ("Mercantile", "Mercantile")]
        form.compliance.choices = [(option, option) for option in schema["properties"].get("compliance", {}).get("enum", [])] or \
                                [("None", "None"), ("Neutral", "Neutral"), ("Loyal", "Loyal"), ("Disloyal", "Disloyal")]
        form.temperament.choices = [(option, option) for option in schema["properties"].get("temperament", {}).get("enum", [])] or \
                             [("Player", "Player"), ("Neutral", "Neutral")]
        form.origin.choices = [(option, option) for option in schema["properties"].get("origin", {}).get("enum", [])] or \
                             [("Unknown", "Unknown"), ("Settled", "Settled"), ("Conquered", "Conquered")]
        
        # Handle resource storage
        if nation: #These if nation checks prevent overwriting existing data when using an existing form.  Try to find a better way at some point
            general_resources = json_data.get("general_resources", [])
            for resource in general_resources:
                resource_field = getattr(form.resource_storage, resource["key"], None)
                resource_field.data = nation.get("resource_storage", {}).get(resource["key"], 0)
            
            # Unique resources
            unique_resources = json_data.get("unique_resources", [])
            for resource in unique_resources:
                resource_field = getattr(form.resource_storage, resource["key"], None)
                resource_field.data = nation.get("resource_storage", {}).get(resource["key"], 0)
        
        # Handle jobs
        if nation:
            for job_key, job_val in nation.get("jobs", {}).items():
                job_field = getattr(form.jobs, job_key, None)
                if job_field:
                    job_field.data = job_val
                
        # Handle districts
        if nation:
            district_slots = nation.get("district_slots", 3)
            districts = nation.get("districts", [])
            if not isinstance(districts, list):
                districts = []
            if len(districts) < district_slots:
                districts.extend([{"type": "", "node": ""}] * (district_slots - len(districts)))
            
            form.districts.entries = []
            for district in districts:
                form.districts.append_entry(district)
        
        # Handle cities
        if nation:
            city_slots = nation.get("city_slots", 1)
            cities = nation.get("cities", [])
            if not isinstance(cities, list):
                cities = []
            if len(cities) < city_slots:
                cities.extend([{"type": "", "node": "", "wall": ""}] * (city_slots - len(cities)))
            
            print(cities)

            form.cities.entries = []
            for city in cities:
                form.cities.append_entry(city)
        
        # Handle modifiers
        if nation:
            modifiers = nation.get("modifiers", [])
            
            form.modifiers.entries = []
            for modifier in modifiers:
                form.modifiers.append_entry(modifier)
        
        return form
    
    def populate_linked_fields(self, schema, dropdown_options):
        """Populates all linked fields with their options"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("collections"):
                self.populate_select_field(field_name, schema, dropdown_options)
        
        node_choices = [("", "None")]
        general_resources = json_data.get("general_resources", [])
        for resource in general_resources:
            node_choices.append((resource.get("key", resource), resource.get("name", resource)))
        
        unique_resources = json_data.get("unique_resources", [])
        for resource in unique_resources:
            node_choices.append((resource.get("key", resource), resource.get("name", resource)))

        #Handle Districts separately
        district_choices = [("", "Empty Slot")]
        districts = json_data.get("districts", {})
        for district_key, district_data in districts.items():
            district_choices.append((district_key, district_data["display_name"]))
                
        for district_field in self.districts:
            district_field.form.populate_linked_fields(type_options=district_choices, node_options=node_choices)
        
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
            form.load_form_from_item(nation)
        else:
            form = form_class()
        
        # Handle jobs
        if nation:
            for job_key, job_val in nation.get("jobs", {}).items():
                job_field = getattr(form.jobs, job_key, None)
                if job_field:
                    job_field.data = job_val
        
        return form
    
    def populate_linked_fields(self, schema, dropdown_options):
        """Populates all linked fields with their options"""
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_schema.get("collections"):
                self.populate_select_field(field_name, schema, dropdown_options)

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
            form.load_form_from_item(item)
        else:
            form = form_class()

        return form

# Global form generator instance
form_generator = FormGenerator()









