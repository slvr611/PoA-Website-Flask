from flask import Blueprint, request, jsonify

api_routes = Blueprint('api_routes', __name__)


@api_routes.route('/api/hello', methods=['POST'])
def hello():
    """Minimal connectivity check for external integrations (e.g. the Discord bot)."""
    data = request.get_json(silent=True) or {}
    if data.get('message') != 'Hello':
        return jsonify({'error': 'Expected {"message": "Hello"}'}), 400
    return jsonify({'message': 'World'})
