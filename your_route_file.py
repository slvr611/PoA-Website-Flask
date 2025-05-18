@app.route('/nation/edit/<name>')
def edit_nation(name):
    # ... existing code ...
    
    # Get the form
    form = form_generator.get_form("nations", schema, nation, formdata=None)
    
    # For the template
    return render_template(
        'nation_owner_edit.html',
        nation=nation,
        form=form,
        form_json=wtform_to_json(form),
        # ... other template variables ...
    )