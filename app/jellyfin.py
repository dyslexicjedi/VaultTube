"""Small, deliberately scoped Jellyfin client used by the companion spike.

The browser only receives VaultTube URLs.  Authentication is applied here so
the Jellyfin token never appears in page source, query strings, or manifests.
"""

import base64
import json
import os
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests


_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_SECRET_QUERY_KEYS = {"api_key", "token", "x-emby-token"}
_LIBRARY_ITEM_TYPES = {
    "series": "Series",
    "seasons": "Season",
    "episodes": "Episode",
}


class JellyfinConfigError(RuntimeError):
    pass


class JellyfinProxyError(RuntimeError):
    pass


def validate_profile_id(profile_id):
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise JellyfinConfigError("Invalid Jellyfin server profile ID")
    return profile_id


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _legacy_profile():
    base_url = os.environ.get("VAULTTUBE_JELLYFIN_URL", "").strip()
    token = os.environ.get("VAULTTUBE_JELLYFIN_TOKEN", "").strip()
    if not base_url and not token:
        return None
    return {
        "name": os.environ.get("VAULTTUBE_JELLYFIN_NAME", "Default").strip(),
        "url": base_url,
        "token": token,
        "user_id": os.environ.get("VAULTTUBE_JELLYFIN_USER_ID", "").strip(),
        "verify_tls": os.environ.get("VAULTTUBE_JELLYFIN_VERIFY_TLS", "true"),
        "report_playback": os.environ.get(
            "VAULTTUBE_JELLYFIN_REPORT_PLAYBACK", "false"
        ),
    }


def _profile_definitions():
    raw = os.environ.get("VAULTTUBE_JELLYFIN_PROFILES", "").strip()
    profiles = {}
    if raw:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise JellyfinConfigError(
                "VAULTTUBE_JELLYFIN_PROFILES must be valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise JellyfinConfigError(
                "VAULTTUBE_JELLYFIN_PROFILES must be a JSON object"
            )
        profiles.update(payload)
    legacy = _legacy_profile()
    if legacy is not None and "default" not in profiles:
        profiles["default"] = legacy
    return profiles


def get_config(profile_id="default"):
    profile_id = validate_profile_id(profile_id or "default")
    definition = _profile_definitions().get(profile_id)
    if not isinstance(definition, dict):
        raise JellyfinConfigError("Unknown Jellyfin server profile")
    base_url = str(definition.get("url") or definition.get("base_url") or "").strip().rstrip("/")
    token = str(definition.get("token") or "").strip()
    user_id = str(definition.get("user_id") or "").strip()
    if not base_url or not token:
        raise JellyfinConfigError(
            "Jellyfin server profile is missing its URL or token"
        )
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise JellyfinConfigError("Jellyfin server profile URL must be an HTTP(S) URL")
    return {
        "id": profile_id,
        "name": str(definition.get("name") or profile_id)[:100],
        "base_url": base_url,
        "token": token,
        "user_id": user_id,
        "verify_tls": _as_bool(definition.get("verify_tls"), True),
        "report_playback": _as_bool(definition.get("report_playback"), False),
    }


def profile_summaries():
    summaries = []
    for profile_id in _profile_definitions():
        config = get_config(profile_id)
        summaries.append({"id": config["id"], "name": config["name"]})
    return summaries


def is_configured(profile_id=None):
    try:
        if profile_id:
            get_config(profile_id)
            return True
        return bool(profile_summaries())
    except JellyfinConfigError:
        return False


def validate_item_id(item_id):
    if not _ITEM_ID_RE.fullmatch(item_id or ""):
        raise JellyfinProxyError("Invalid Jellyfin item ID")
    return item_id


def auth_headers(config):
    return {"X-Emby-Token": config["token"]}


def manifest_url(config, item_id):
    item_id = validate_item_id(item_id)
    return "%s/Videos/%s/master.m3u8" % (config["base_url"], item_id)


def manifest_params(config, item_id):
    params = {
        "MediaSourceId": item_id,
        "VideoCodec": "h264",
        "AudioCodec": "aac",
        "VideoBitrate": "8000000",
        "AudioBitrate": "192000",
        "MaxAudioChannels": "2",
        "TranscodingMaxAudioChannels": "2",
        "SegmentContainer": "ts",
        "MinSegments": "1",
        "BreakOnNonKeyFrames": "true",
    }
    if config.get("user_id"):
        params["UserId"] = config["user_id"]
    return params


def item_info(config, item_id):
    """Return the small metadata subset needed for a composite session."""
    item_id = validate_item_id(item_id)
    params = {}
    if config.get("user_id"):
        # Server API keys are not tied to a Jellyfin user.  Scope the item
        # lookup explicitly, just as the library and manifest requests do,
        # or Jellyfin rejects the metadata request with HTTP 400.
        params["UserId"] = config["user_id"]
    response = requests.get(
        "%s/Items/%s" % (config["base_url"], item_id),
        params=params,
        headers=auth_headers(config),
        timeout=(5, 30),
        verify=config["verify_tls"],
        allow_redirects=False,
    )
    try:
        response.raise_for_status()
        data = response.json()
    finally:
        response.close()
    ticks = data.get("RunTimeTicks")
    return {
        "id": data.get("Id") or item_id,
        "name": data.get("Name") or item_id,
        "duration": (float(ticks) / 10_000_000) if ticks else None,
    }


def library_items(config, kind, *, parent_id=None, search=None, limit=200):
    """Return sanitized Series/Season/Episode rows for the companion picker."""
    item_type = _LIBRARY_ITEM_TYPES.get(kind)
    if not item_type:
        raise JellyfinProxyError("Invalid Jellyfin library item type")
    if kind != "series" and not parent_id:
        raise JellyfinProxyError("A Jellyfin parent item ID is required")
    if parent_id:
        validate_item_id(parent_id)
    search = (search or "").strip()
    if len(search) > 200:
        raise JellyfinProxyError("Jellyfin search is too long")
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError) as exc:
        raise JellyfinProxyError("Invalid Jellyfin result limit") from exc

    params = {
        "IncludeItemTypes": item_type,
        "Recursive": "true" if kind == "series" else "false",
        "SortBy": (
            "SortName" if kind == "series"
            else "IndexNumber,SortName" if kind == "seasons"
            else "ParentIndexNumber,IndexNumber,SortName"
        ),
        "SortOrder": "Ascending",
        "StartIndex": 0,
        "Limit": limit,
        "Fields": "DateCreated",
    }
    if config.get("user_id"):
        params["UserId"] = config["user_id"]
    if parent_id:
        params["ParentId"] = parent_id
    if search:
        params["SearchTerm"] = search

    response = requests.get(
        "%s/Items" % config["base_url"],
        params=params,
        headers=auth_headers(config),
        timeout=(5, 30),
        verify=config["verify_tls"],
        allow_redirects=False,
    )
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()

    rows = []
    for item in payload.get("Items", []):
        item_id = item.get("Id")
        if not item_id:
            continue
        ticks = item.get("RunTimeTicks")
        rows.append({
            "id": item_id,
            "name": item.get("Name") or item_id,
            "type": item.get("Type") or item_type,
            "index_number": item.get("IndexNumber"),
            "parent_index_number": item.get("ParentIndexNumber"),
            "production_year": item.get("ProductionYear"),
            "duration": (float(ticks) / 10_000_000) if ticks else None,
        })
    return rows


def _strip_secrets(url):
    parsed = urlparse(url)
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SECRET_QUERY_KEYS
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def encode_asset_url(config, item_id, url):
    """Encode a same-server, same-item Jellyfin URL for a browser-safe route."""
    item_id = validate_item_id(item_id)
    absolute = _strip_secrets(urljoin(config["base_url"] + "/", url))
    parsed = urlparse(absolute)
    configured = urlparse(config["base_url"])
    if parsed.scheme != configured.scheme or parsed.netloc != configured.netloc:
        raise JellyfinProxyError("Jellyfin manifest referenced another host")
    if "/videos/%s/" % item_id.lower() not in parsed.path.lower():
        raise JellyfinProxyError("Jellyfin manifest referenced another item")
    raw = absolute.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_asset_url(config, item_id, encoded):
    try:
        padding = "=" * (-len(encoded) % 4)
        url = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise JellyfinProxyError("Invalid Jellyfin asset URL") from exc
    # Re-validating applies the host and item constraints after client input.
    encode_asset_url(config, item_id, url)
    return _strip_secrets(url)


def rewrite_manifest(config, item_id, manifest_text, source_url):
    lines = []
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            encoded = encode_asset_url(config, item_id, urljoin(source_url, stripped))
            line = "/api/jellyfin/phase1/%s/asset/%s?profile=%s" % (
                item_id, encoded, quote(config.get("id", "default"), safe="")
            )
        lines.append(line)
    return "\n".join(lines) + "\n"


def upstream_get(config, url, *, params=None, range_header=None):
    headers = auth_headers(config)
    if range_header:
        headers["Range"] = range_header
    return requests.get(
        url,
        params=params,
        headers=headers,
        stream=True,
        timeout=(5, 60),
        verify=config["verify_tls"],
        allow_redirects=False,
    )


def report_playback(config, item_id, position_seconds, *, watched=False):
    """Optionally update one Jellyfin user's resume position and watched state."""
    if not config.get("report_playback"):
        return False
    user_id = validate_item_id(config.get("user_id"))
    item_id = validate_item_id(item_id)
    try:
        position = max(0.0, float(position_seconds))
    except (TypeError, ValueError) as exc:
        raise JellyfinProxyError("Invalid Jellyfin playback position") from exc
    response = requests.post(
        "%s/Users/%s/Items/%s/UserData" % (
            config["base_url"], user_id, item_id
        ),
        json={"PlaybackPositionTicks": int(position * 10_000_000)},
        headers=auth_headers(config),
        timeout=(5, 30),
        verify=config["verify_tls"],
        allow_redirects=False,
    )
    try:
        response.raise_for_status()
    finally:
        response.close()
    if watched:
        response = requests.post(
            "%s/Users/%s/PlayedItems/%s" % (
                config["base_url"], user_id, item_id
            ),
            headers=auth_headers(config),
            timeout=(5, 30),
            verify=config["verify_tls"],
            allow_redirects=False,
        )
        try:
            response.raise_for_status()
        finally:
            response.close()
    return True
