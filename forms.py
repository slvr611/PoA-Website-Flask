from flask_wtf import FlaskForm
from wtforms.validators import DataRequired, NumberRange, InputRequired
from wtforms import HiddenField, IntegerField, StringField, SelectField, FieldList, FormField, SubmitField

class KeyValueForm(FlaskForm):
    key = HiddenField("Key")
    value = IntegerField("Value", validators=[InputRequired()])

class StringFieldForm(FlaskForm):
    value = StringField("Value")

class NationEditForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    # (Add any other top-level fields you need.)
    
    # For jobs, we use a FieldList of KeyValueForm.
    # You'll need to decide how many entries you require.
    jobs = FieldList(FormField(KeyValueForm), min_entries=0)
    
    # For resources, you could do the same if you need to allow editing.
    # If the resources are the same for every nation, you might have fixed entries.
    resources = FieldList(FormField(KeyValueForm), min_entries=0)
    
    # For districts, since it's an array of strings (district names or empty),
    # we use a FieldList of StringField. (Alternatively, you could use a custom form
    # if you need additional logic.)
    districts = FieldList(SelectField("District", choices=[]), min_entries=0)
    
    reason = StringField("Reason")
    submit = SubmitField("Save")
