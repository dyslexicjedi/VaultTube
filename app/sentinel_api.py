import logging

from flask import Blueprint, current_app, jsonify, request

from sentinel import get_events, get_source_detail, get_sources, get_summary
from sentinel_inventory import manual_census
from sentinel_import import (
    MAX_IMPORT_BYTES, compare_historical_ids, parse_historical_export,
)
from sentinel_rescue import (
    create_rescue_preview, create_rescue_session, get_rescue_preview,
    get_rescue_session, set_rescue_session_status,
)


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


@sentinel_bp.route(
    '/source/<string:source_type>/<string:source_id>/compare-import',
    methods=['POST'],
)
def compare_import(source_type, source_id):
    """Compare a manual Filmot export without changing Sentinel state."""
    if source_type != 'channel':
        return _error('Unsupported Sentinel source type', 404)
    try:
        if get_source_detail(source_id) is None:
            return _error('Sentinel source not found', 404)
        upload = request.files.get('file')
        if upload is None or not upload.filename:
            return _error('A Filmot text export is required', 400)
        if not upload.filename.lower().endswith('.txt'):
            return _error('Filmot import must be a .txt file', 400)
        raw = upload.stream.read(MAX_IMPORT_BYTES + 1)
        parsed = parse_historical_export(raw)
        return _success(compare_historical_ids(source_id, parsed))
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:
        logger.error('Sentinel import comparison failed for %s: %s', source_id, e)
        return _error(str(e), 500)


@sentinel_bp.route(
    '/source/<string:source_type>/<string:source_id>/scan', methods=['POST'],
)
def scan_source(source_type, source_id):
    try:
        return _success(manual_census(source_type, source_id))
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:
        logger.error('Manual Sentinel census failed for %s: %s', source_id, e)
        return _error(str(e), 502)


@sentinel_bp.route(
    '/source/<string:source_type>/<string:source_id>/rescue-preview',
    methods=['POST'],
)
def rescue_preview(source_type, source_id):
    try:
        data = create_rescue_preview(
            source_type, source_id, request.get_json(silent=True) or {},
        )
        return _success(data)
    except ValueError as e:
        status = 409 if 'complete inventory' in str(e).lower() else 400
        return _error(str(e), status)
    except Exception as e:
        logger.error('Sentinel rescue preview failed for %s: %s', source_id, e)
        return _error(str(e), 500)


@sentinel_bp.route('/rescue-previews/<string:preview_id>')
def saved_rescue_preview(preview_id):
    try:
        data = get_rescue_preview(preview_id)
        if data is None:
            return _error('Sentinel rescue preview not found', 404)
        return _success(data)
    except Exception as e:
        logger.error('Sentinel rescue preview read failed for %s: %s', preview_id, e)
        return _error(str(e), 500)


@sentinel_bp.route('/rescues', methods=['POST'])
def start_rescue():
    body = request.get_json(silent=True) or {}
    preview_id = str(body.get('preview_id') or '').strip()
    if not preview_id:
        return _error('preview_id is required', 400)
    try:
        data = create_rescue_session(
            preview_id, body, current_app.config['queue'],
        )
        return _success(data)
    except ValueError as e:
        message = str(e)
        status = 404 if message == 'Rescue preview not found' else 409 \
            if any(term in message.lower() for term in (
                'stale', 'already approved', 'blocked', 'availability',
                'no selected',
            )) else 400
        return _error(message, status)
    except Exception as e:
        logger.error('Sentinel rescue start failed for %s: %s', preview_id, e)
        return _error(str(e), 500)


@sentinel_bp.route('/rescues/<string:session_id>')
def rescue_session(session_id):
    try:
        data = get_rescue_session(session_id)
        if data is None:
            return _error('Rescue session not found', 404)
        return _success(data)
    except Exception as e:
        logger.error('Sentinel rescue read failed for %s: %s', session_id, e)
        return _error(str(e), 500)


def _rescue_action(session_id, action):
    try:
        return _success(set_rescue_session_status(session_id, action))
    except ValueError as e:
        status = 404 if str(e) == 'Rescue session not found' else 409
        return _error(str(e), status)
    except Exception as e:
        logger.error(
            'Sentinel rescue %s failed for %s: %s', action, session_id, e,
        )
        return _error(str(e), 500)


@sentinel_bp.route('/rescues/<string:session_id>/pause', methods=['POST'])
def pause_rescue(session_id):
    return _rescue_action(session_id, 'pause')


@sentinel_bp.route('/rescues/<string:session_id>/resume', methods=['POST'])
def resume_rescue(session_id):
    return _rescue_action(session_id, 'resume')


@sentinel_bp.route('/rescues/<string:session_id>/cancel', methods=['POST'])
def cancel_rescue(session_id):
    return _rescue_action(session_id, 'cancel')
