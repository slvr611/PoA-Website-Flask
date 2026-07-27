from copy import deepcopy
from flask import Blueprint, request, jsonify, g
from app_core import category_data, mongo
from helpers.auth_helpers import api_key_required
from helpers.change_helpers import request_change

api_routes = Blueprint('api_routes', __name__)


@api_routes.route('/api/hello', methods=['POST'])
@api_key_required
def hello():
    """Minimal connectivity check for external integrations (e.g. the Discord bot)."""
    data = request.get_json(silent=True) or {}
    if data.get('message') != 'Hello':
        return jsonify({'error': 'Expected {"message": "Hello"}'}), 400
    return jsonify({'message': 'World'})


# data_types that don't behave like a normal named item and can't be safely
# targeted by this endpoint: hex_map uses a per-tile diff format instead of
# name lookup, and requesting a change against "changes" itself is nonsensical.
_DISALLOWED_DATA_TYPES = {'hex_map', 'changes'}


@api_routes.route('/api/change-request', methods=['POST'])
@api_key_required
def create_change_request():
    """Submit a change request against an existing named item, as if it had
    been edited through the item's own edit form. Always creates a Pending
    request — nothing is applied until a moderator approves it, exactly like
    a normal player-submitted edit.

    Expected JSON body:
      {
        "user": "<discord id, matches players.id>",
        "data_type": "nations",              // any key in app_core.category_data
        "name": "Some Nation",               // looked up by exact name
        "new_data": {"field": "new value"},  // partial patch — only these fields change
        "reason": "optional; defaults if omitted"
      }
    """
    payload = request.get_json(silent=True) or {}
    user_id = payload.get('user')
    data_type = payload.get('data_type')
    name = payload.get('name')
    new_data = payload.get('new_data')
    reason = payload.get('reason') or 'Submitted via Discord bot'

    if not user_id or not data_type or not name or not isinstance(new_data, dict):
        return jsonify({
            'error': 'Required fields: user (string), data_type (string), '
                     'name (string), new_data (object)'
        }), 400

    if data_type not in category_data or data_type in _DISALLOWED_DATA_TYPES:
        return jsonify({'error': f'Invalid data_type: {data_type!r}'}), 400

    player = mongo.db.players.find_one({'id': str(user_id)})
    if not player:
        return jsonify({'error': f'No player found for user {user_id!r}'}), 404

    db = category_data[data_type]['database']
    item = db.find_one({'name': name})
    if not item:
        return jsonify({'error': f'No {data_type} item named {name!r}'}), 404

    # Only fields the item's own edit form would actually submit are eligible.
    # The raw document also carries calculated/derived bookkeeping that isn't
    # in the schema at all (breakdowns, district_details, unit stat_breakdowns,
    # ...) or is marked "calculated": true — including that verbatim would
    # make request_change's diffing treat its unstable internal IDs as real
    # changes, burying the actual edit in thousands of lines of noise.
    schema = category_data[data_type].get('schema', {})
    editable_fields = {
        field for field, field_schema in schema.get('properties', {}).items()
        if isinstance(field_schema, dict) and not field_schema.get('calculated', False)
    }

    unknown_fields = set(new_data.keys()) - editable_fields
    if unknown_fields:
        return jsonify({
            'error': f'new_data includes fields that are not directly editable: {sorted(unknown_fields)}'
        }), 400

    before_data = {k: v for k, v in item.items() if k in editable_fields}
    after_data = deepcopy(before_data)
    after_data.update(new_data)

    # request_change reads the requester off g.user (normally populated by the
    # Discord OAuth session) — set it for just this request so the existing,
    # already-tested change-request logic (diffing, ID reconciliation, etc.)
    # can be reused as-is instead of duplicated here.
    previous_g_user = getattr(g, 'user', None)
    g.user = {'id': str(user_id)}
    try:
        change_id = request_change(data_type, item['_id'], 'Update', before_data, after_data, reason)
    finally:
        g.user = previous_g_user

    if not change_id:
        return jsonify({'error': 'Failed to create change request'}), 500

    return jsonify({'change_id': str(change_id), 'status': 'Pending'}), 201
