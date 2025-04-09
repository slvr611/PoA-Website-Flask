from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, IntegerField, FloatField, BooleanField, SelectField
from wtforms import FieldList, FormField, HiddenField, SubmitField, MultipleFileField
from wtforms.validators import DataRequired, NumberRange, Optional, ValidationError
import json
import copy

def populate_select_field(form, field_name, schema, dropdown_options):
    """
    Populates a select field on the form.
    - form: the form instance (e.g., an instance of EnhancedNationEditForm)
    - field_name: the name of the field to populate (e.g., "region")
    - schema: the JSON schema dictionary for your item.
    - dropdown_options: a dictionary mapping field names to lists of options (from the database).
    
    This sets the field's choices to include a default option from the schema ("noneResult")
    and then any additional options from dropdown_options. It also sets the default
    value from the nation data (already in form.data).
    """
    field_schema = schema.get("properties", {}).get(field_name, {})
    none_result = field_schema.get("noneResult", "None")
    choices = [("", none_result)]
    if field_name in dropdown_options:
        choices += [(str(option["_id"]), option["name"]) for option in dropdown_options[field_name]]
    
    # Set choices on the field
    field = getattr(form, field_name)
    field.choices = choices

    # Set the current value properly (assume nation values are stored as string IDs)
    data_val = field.data
    if data_val:
        field.data = str(data_val)
    else:
        field.data = ""

def to_int(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        value = value.strip()
        return int(value) if value else 0
    except (ValueError, AttributeError):
        return 0


class KeyValueForm(FlaskForm):
    """Form for key-value pairs"""
    key = HiddenField("Key")
    value = IntegerField("Value", validators=[DataRequired()])
    
    class Meta:
        # Disable CSRF for nested forms
        csrf = False

class StringFieldForm(FlaskForm):
    """Form for string fields"""
    value = StringField("Value")
    
    class Meta:
        # Disable CSRF for nested forms
        csrf = False

class SchemaForm(FlaskForm):
    """Base class for all schema-based forms"""
    reason = StringField("Reason")
    submit = SubmitField("Save")

class ResourceStorageForm(FlaskForm):
    """Form for resource storage entries"""
    key = HiddenField("Key")
    name = HiddenField("Name")
    value = IntegerField("Storage", validators=[NumberRange(min=0)], filters=[to_int])
    
    class Meta:
        # Disable CSRF for nested forms
        csrf = False

class JobAssignmentForm(FlaskForm):
    """Form for job assignments"""
    key = HiddenField("Key")
    display_name = HiddenField("Display Name")
    value = IntegerField("Assigned Pops", validators=[NumberRange(min=0)], filters=[to_int])
    
    class Meta:
        # Disable CSRF for nested forms
        csrf = False

class DynamicSchemaField:
    """Helper class to store schema field information"""
    def __init__(self, name, attrs, schema):
        self.name = name
        self.attrs = attrs
        self.schema = schema
        self.field_type = attrs.get('bsonType')
        self.required = name in schema.get('required', [])
        self.label = attrs.get('label', name)
        self.description = attrs.get('description', '')
        self.default = attrs.get('default')
        self.collection = attrs.get('collection')
        self.enum_values = attrs.get('enum', [])
        self.long_text = attrs.get('long_text', False)
        self.calculated = attrs.get('calculated', False)
        self.none_result = attrs.get('noneResult', 'None')

class DynamicForm(SchemaForm):
    """Form that can be constructed at runtime based on JSON schema"""
    
    @classmethod
    def create_from_schema(cls, schema, **kwargs):
        """Create a form class from a JSON schema"""
        form_class = type('DynamicSchemaForm', (cls,), {})
        
        # Add fields based on schema
        for field_name, field_attrs in schema.get('properties', {}).items():
            if field_attrs.get('calculated', False):
                continue  # Skip calculated fields
                
            schema_field = DynamicSchemaField(field_name, field_attrs, schema)
            form_field = create_field_from_schema_field(schema_field)
            
            if form_field:
                setattr(form_class, field_name, form_field)
        
        return form_class(**kwargs)
    
    @classmethod
    def update_from_schema_changes(cls, old_schema, new_schema):
        """Update a form class based on schema changes"""
        # Create a copy of the old form class
        updated_form_class = type('UpdatedDynamicSchemaForm', (cls,), {})
        
        # Copy existing fields from old schema
        for field_name, field_attrs in old_schema.get('properties', {}).items():
            if field_attrs.get('calculated', False):
                continue  # Skip calculated fields
            
            # Check if field still exists in new schema
            if field_name in new_schema.get('properties', {}):
                old_field = DynamicSchemaField(field_name, field_attrs, old_schema)
                old_form_field = create_field_from_schema_field(old_field)
                
                if old_form_field:
                    setattr(updated_form_class, field_name, old_form_field)
        
        # Add new fields from new schema
        for field_name, field_attrs in new_schema.get('properties', {}).items():
            if field_attrs.get('calculated', False):
                continue  # Skip calculated fields
            
            # If field is new or has changed
            if field_name not in old_schema.get('properties', {}) or old_schema['properties'][field_name] != field_attrs:
                new_field = DynamicSchemaField(field_name, field_attrs, new_schema)
                new_form_field = create_field_from_schema_field(new_field)
                
                if new_form_field:
                    setattr(updated_form_class, field_name, new_form_field)
        
        return updated_form_class

def create_field_from_schema_field(schema_field):
    """Create a WTForms field from a DynamicSchemaField"""
    validators = []
    if schema_field.required:
        validators.append(DataRequired())
    else:
        validators.append(Optional())
    
    # Handle different field types
    if schema_field.field_type == 'string':
        if schema_field.long_text:
            return TextAreaField(schema_field.label, validators=validators, 
                                description=schema_field.description,
                                default=schema_field.default)
        return StringField(schema_field.label, validators=validators, 
                          description=schema_field.description,
                          default=schema_field.default)
    
    elif schema_field.field_type == 'number':
        return IntegerField(schema_field.label, validators=validators, 
                           description=schema_field.description,
                           default=schema_field.default)
    
    elif schema_field.field_type == 'boolean':
        return BooleanField(schema_field.label, description=schema_field.description,
                           default=schema_field.default)
    
    elif schema_field.field_type == 'enum':
        choices = [(option, option) for option in schema_field.enum_values]
        return SelectField(
            schema_field.label, 
            choices=choices, 
            validators=validators,
            description=schema_field.description,
            default=schema_field.default
        )
    
    elif schema_field.field_type == 'linked_object':
        # This will be populated with choices from the database later
        field = SelectField(
            schema_field.label, 
            validators=validators,
            description=schema_field.description
        )
        # Store the none_result as an attribute on the field
        field.none_result = schema_field.none_result
        field.collection = schema_field.collection
        return field
    
    # For complex types like arrays, we'll handle them separately
    return None

class SchemaFormGenerator:
    """Generator for creating forms from schemas with caching"""
    
    def __init__(self):
        self.form_cache = {}  # Cache for generated form classes
        self.schema_cache = {}  # Cache for schemas
    
    def get_form(self, data_type, schema, item=None, **kwargs):
        """Get a form for a specific data type, using cache if available"""
        # Check if we have a custom form class for this data type
        custom_form = self._get_custom_form_class(data_type)
        if custom_form:
            if item:
                return custom_form(obj=item, **kwargs)
            return custom_form(**kwargs)
        
        # Check if schema has changed since last cache
        schema_hash = self._hash_schema(schema)
        cached_schema_hash = self.schema_cache.get(data_type)
        
        if data_type in self.form_cache and cached_schema_hash == schema_hash:
            # Use cached form class
            form_class = self.form_cache[data_type]
        else:
            # Create new form class and update cache
            form_class = DynamicForm.create_from_schema(schema).__class__
            self.form_cache[data_type] = form_class
            self.schema_cache[data_type] = schema_hash
        
        # Create instance of the form
        if item:
            return form_class(obj=item, **kwargs)
        return form_class(**kwargs)
    
    def update_form_for_schema_changes(self, data_type, old_schema, new_schema):
        """Update a form class based on schema changes"""
        # Only update if we have a cached form
        if data_type not in self.form_cache:
            return self.get_form(data_type, new_schema)
        
        # Update form class
        updated_form_class = DynamicForm.update_from_schema_changes(old_schema, new_schema)
        
        # Update cache
        self.form_cache[data_type] = updated_form_class
        self.schema_cache[data_type] = self._hash_schema(new_schema)
        
        return updated_form_class()
    
    def populate_linked_fields(self, form, dropdown_options):
        """Populate linked_object fields with options from the database"""
        for field_name, options in dropdown_options.items():
            if hasattr(form, field_name):
                field = getattr(form, field_name)
                if isinstance(field, SelectField):
                    # Get the none_result from the field
                    none_result = getattr(field, 'none_result', 'None')
                    field.choices = [('', none_result)] + [
                        (str(option['_id']), option['name']) for option in options
                    ]
    
    def _get_custom_form_class(self, data_type):
        """Get a custom form class for a specific data type if it exists"""
        custom_forms = {
            'nations': NationEditForm,
            # Add other custom forms here
        }
        return custom_forms.get(data_type)
    
    def _hash_schema(self, schema):
        """Create a hash of the schema for caching purposes"""
        # Use a simple string representation for now
        # In production, you might want a more efficient hashing method
        return json.dumps(schema, sort_keys=True)

class NationEditForm(SchemaForm):
    """Custom form for editing nations"""
    name = StringField("Name", validators=[DataRequired()])
    jobs = FieldList(FormField(KeyValueForm), min_entries=0)
    districts = FieldList(SelectField("District", choices=[]), min_entries=0)
    
    @classmethod
    def create_from_nation(cls, nation, schema, json_data):
        """Create a form pre-populated with nation data"""
        form = cls(obj=nation)
        
        # Handle jobs
        form.jobs.entries = []
        initial_jobs = []
        for job_key, job_val in nation.get("jobs", {}).items():
            initial_jobs.append({"key": job_key, "value": job_val})
        
        for job_data in initial_jobs:
            form.jobs.append_entry(job_data)
        
        # Handle districts
        district_slots = nation.get("district_slots", 3)
        initial_districts = nation.get("districts", [])
        if len(initial_districts) < district_slots:
            initial_districts.extend([""] * (district_slots - len(initial_districts)))
        
        form.districts.entries = []
        district_choices = [("", "Empty Slot")]
        for key, district in json_data["districts"].items():
            district_choices.append((key, district["display_name"]))
        
        for i in range(district_slots):
            entry = form.districts.append_entry(initial_districts[i])
            entry.choices = district_choices
        
        return form

def validate_form_with_jsonschema(form, schema):
    """Validate form data against JSON schema"""
    from jsonschema import validate, ValidationError
    
    # Remove Flask-WTF specific fields
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    form_data.pop('reason', None)
    
    try:
        validate(instance=form_data, schema=schema)
        return True, None
    except ValidationError as e:
        return False, e.message

class EnhancedNationEditForm(FlaskForm):
    """Enhanced form for editing nations with all fields"""
    # CSRF token and submit button
    csrf_token = HiddenField()
    submit = SubmitField("Save")
    reason = StringField("Reason")
    
    # General fields
    name = StringField("Name", validators=[DataRequired()])
    region = SelectField("Region", choices=[])
    stability = SelectField("Stability", choices=[])
    infamy = IntegerField("Infamy", validators=[NumberRange(min=0)], filters=[to_int])
    temporary_karma = IntegerField("Temporary Karma", validators=[NumberRange(min=0)], filters=[to_int])
    rolling_karma = IntegerField("Rolling Karma", validators=[NumberRange(min=0)], filters=[to_int])
    
    # Income fields
    money = IntegerField("Money", validators=[NumberRange(min=0)], filters=[to_int])
    
    # Demographics fields
    primary_race = SelectField("Primary Race", choices=[])
    primary_culture = SelectField("Primary Culture", choices=[])
    primary_religion = SelectField("Primary Religion", choices=[])
    
    # Administration & Holdings fields
    current_territory = IntegerField("Current Territory", validators=[NumberRange(min=0)], filters=[to_int])
    road_usage = IntegerField("Road Usage", validators=[NumberRange(min=0)], filters=[to_int])
    
    # Vassalship fields
    overlord = SelectField("Overlord", choices=[])
    vassal_type = SelectField("Vassal Type", choices=[])
    compliance = SelectField("Compliance", choices=[])
    
    # Miscellaneous fields
    origin = SelectField("Origin", choices=[])
    modifiers = TextAreaField("Modifiers")
    
    # Complex fields
    resource_storage = FieldList(FormField(ResourceStorageForm), min_entries=0)
    jobs = FieldList(FormField(JobAssignmentForm), min_entries=0)
    districts = FieldList(SelectField("District", choices=[]), min_entries=0)
    
    # Dynamic government laws fields will be added programmatically
    
    @classmethod
    def create_from_nation(cls, nation, schema, json_data, dropdown_options):
        """Create a form pre-populated with nation data"""
        form = cls()
        
        # Set basic fields from nation data
        form.name.data = nation.get("name", "")
        form.infamy.data = nation.get("infamy", 0)
        form.temporary_karma.data = nation.get("temporary_karma", 0)
        form.rolling_karma.data = nation.get("rolling_karma", 0)
        form.money.data = nation.get("money", 0)
        form.current_territory.data = nation.get("current_territory", 0)
        form.road_usage.data = nation.get("road_usage", 0)
        form.modifiers.data = json.dumps(nation.get("modifiers", {})) if "modifiers" in nation else ""
        
        # Set up select fields
        # Region
        populate_select_field(form, "region", schema, dropdown_options)
        populate_select_field(form, "primary_race", schema, dropdown_options)
        populate_select_field(form, "primary_culture", schema, dropdown_options)
        populate_select_field(form, "primary_religion", schema, dropdown_options)
        populate_select_field(form, "overlord", schema, dropdown_options)
        
        form.stability.choices = [(option, option) for option in schema["properties"].get("stability", {}).get("enum", [])] or \
                                 [("Balanced", "Balanced"), ("Unstable", "Unstable"), ("Stable", "Stable")]
        form.stability.data = nation.get("stability", "Balanced")
        
        form.vassal_type.choices = [(option, option) for option in schema["properties"].get("vassal_type", {}).get("enum", [])] or \
                                    [("None", "None"), ("Tributary", "Tributary"), ("Mercantile", "Mercantile")]
        form.vassal_type.data = nation.get("vassal_type", "None")
        
        form.compliance.choices = [(option, option) for option in schema["properties"].get("compliance", {}).get("enum", [])] or \
                                  [("None", "None"), ("Neutral", "Neutral"), ("Loyal", "Loyal"), ("Disloyal", "Disloyal")]
        form.compliance.data = nation.get("compliance", "None")
        
        form.origin.choices = [(option, option) for option in schema["properties"].get("origin", {}).get("enum", [])] or \
                              [("Unknown", "Unknown"), ("Settled", "Settled"), ("Conquered", "Conquered")]
        form.origin.data = nation.get("origin", "")
        
        # Add government law fields dynamically
        for law in schema.get("laws", []):
            if law != "stability" and law in schema["properties"]:
                law_field = SelectField(schema["properties"][law].get("label", law))
                law_choices = [(option, option) for option in schema["properties"][law].get("enum", [])]
                if not law_choices:  # Ensure we have default choices if schema doesn't provide them
                    law_choices = [("None", "None")]
                law_field.choices = law_choices
                setattr(form, law, law_field)
                getattr(form, law).data = nation.get(law, "")
        
        print(form.origin)
        print(form.government_type)
        
        # Handle resource storage
        form.resource_storage.entries = []
        # General resources
        general_resources = json_data.get("general_resources", [])
        for resource in general_resources:
            resource_data = {
                "key": resource["key"],
                "name": resource["name"],
                "value": nation.get("resource_storage", {}).get(resource["key"], 0)
            }
            form.resource_storage.append_entry(resource_data)
        
        # Unique resources
        unique_resources = json_data.get("unique_resources", [])
        for resource in unique_resources:
            resource_data = {
                "key": resource["key"],
                "name": resource["name"],
                "value": nation.get("resource_storage", {}).get(resource["key"], 0)
            }
            form.resource_storage.append_entry(resource_data)
        
        # Handle jobs
        form.jobs.entries = []
        for job_key, job in nation.get("job_details", {}).items():
            job_data = {
                "key": job_key,
                "display_name": job.get("display_name", job_key),
                "value": nation.get("jobs", {}).get(job_key, 0)
            }
            form.jobs.append_entry(job_data)
        
        # Handle districts
        district_slots = nation.get("district_slots", 3)
        initial_districts = nation.get("districts", [])
        if len(initial_districts) < district_slots:
            initial_districts.extend([""] * (district_slots - len(initial_districts)))
        
        form.districts.entries = []
        district_choices = [("", "Empty Slot")]
        for key, district in json_data.get("districts", {}).items():
            district_choices.append((key, district["display_name"]))
        
        for i in range(district_slots):
            entry = form.districts.append_entry(initial_districts[i] if i < len(initial_districts) else "")
            entry.choices = district_choices
        
        return form
    
    def to_dict(self):
        """Convert form data to a dictionary for database storage"""
        data = {}
        
        # Basic fields
        data["name"] = self.name.data
        data["infamy"] = self.infamy.data
        data["temporary_karma"] = self.temporary_karma.data
        data["rolling_karma"] = self.rolling_karma.data
        data["money"] = self.money.data
        data["current_territory"] = self.current_territory.data
        data["road_usage"] = self.road_usage.data
        
        # Select fields
        data["region"] = self.region.data if self.region.data else None
        data["primary_race"] = self.primary_race.data if self.primary_race.data else None
        data["primary_culture"] = self.primary_culture.data if self.primary_culture.data else None
        data["primary_religion"] = self.primary_religion.data if self.primary_religion.data else None
        data["overlord"] = self.overlord.data if self.overlord.data else None
        data["stability"] = self.stability.data
        data["vassal_type"] = self.vassal_type.data
        data["compliance"] = self.compliance.data
        data["origin"] = self.origin.data
        
        # Handle modifiers
        if self.modifiers.data:
            try:
                data["modifiers"] = json.loads(self.modifiers.data)
            except:
                data["modifiers"] = {}
        
        # Handle government laws
        for field_name, field in self._fields.items():
            if isinstance(field, SelectField) and field_name not in [
                "region", "primary_race", "primary_culture", "primary_religion", 
                "overlord", "stability", "vassal_type", "compliance", "origin"
            ]:
                data[field_name] = field.data
        
        # Handle resource storage
        resource_storage = {}
        for entry in self.resource_storage.data:
            resource_storage[entry["key"]] = entry["value"]
        data["resource_storage"] = resource_storage
        
        # Handle jobs
        jobs = {}
        for entry in self.jobs.data:
            jobs[entry["key"]] = entry["value"]
        data["jobs"] = jobs
        
        # Handle districts
        data["districts"] = [district for district in self.districts.data if district]
        
        return data

# Create a global instance of the form generator
form_generator = SchemaFormGenerator()
