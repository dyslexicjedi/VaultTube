import logging

from flask import Blueprint, jsonify, request

from sentinel import get_events, get_source_detail, get_sources, get_summary


logger = logging.getLogger('sentinel_api')
sentinel_bp = Blueprint('sentinel_api', __name__)


def _success(data):
    return jsonify({'success': True, 'data': data})


def _error(message, status=400):
    return jsonify({'success': False, 'error': message}), status


def _int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        raise ValueError('%s must be an integer' % name)


@sentinel_bp.route('/summary')
def summary():
    try:
        return _success(get_summary())
    except Exception as e:
        logger.error('Sentinel summary failed: %s', e)
        return _error(str(e), 500)


@sentinel_bp.route('/events')
def events():
    try:
        data = get_events(
            limit=_int_arg('limit', 50),
            offset=_int_arg('offset', 0),
            event_type=(request.args.get('event_type') or '').strip() or None,
            channel_id=(request.args.get('channel_id') or '').strip() or None,
        )
        return _success(data)
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:
        logger.error('Sentinel events failed: %s', e)
        return _error(str(e), 500)


@sentinel_bp.route('/sources')
def sources():
    try:
        return _success(get_sources(
            limit=_int_arg('limit', 50), offset=_int_arg('offset', 0),
        ))
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:
        logger.error('Sentinel sources failed: %s', e)
        return _error(str(e), 500)


@sentinel_bp.route('/source/<string:source_type>/<string:source_id>')
def source(source_type, source_id):
    if source_type != 'channel':
        return _error('Unsupported Sentinel source type', 404)
    try:
        data = get_source_detail(source_id)
        if data is None:
            return _error('Sentinel source not found', 404)
        return _success(data)
    except Exception as e:
        logger.error('Sentinel source detail failed for %s: %s', source_id, e)
        return _error(str(e), 500)
