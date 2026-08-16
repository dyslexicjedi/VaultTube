"""Small, deliberately scoped Jellyfin client used by the companion spike.

The browser only receives VaultTube URLs.  Authentication is applied here so
the Jellyfin token never appears in page source, query strings, or manifests.
"""

import base64
import os
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests


_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
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


def get_config():
    base_url = os.environ.get("VAULTTUBE_JELLYFIN_URL", "").strip().rstrip("/")
    token = os.environ.get("VAULTTUBE_JELLYFIN_TOKEN", "").strip()
    user_id = os.environ.get("VAULTTUBE_JELLYFIN_USER_ID", "").strip()
    if not base_url or not token:
        raise JellyfinConfigError(
            "Jellyfin is not configured; set VAULTTUBE_JELLYFIN_URL and "
            "VAULTTUBE_JELLYFIN_TOKEN"
        )
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise JellyfinConfigError("VAULTTUBE_JELLYFIN_URL must be an HTTP(S) URL")
    return {
        "base_url": base_url,
        "token": token,
        "user_id": user_id,
        "verify_tls": os.environ.get("VAULTTUBE_JELLYFIN_VERIFY_TLS", "true").lower()
        not in ("0", "false", "no"),
    }


def is_configured():
    try:
        get_config()
        return True
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
    response = requests.get(
        "%s/Items/%s" % (config["base_url"], item_id),
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
            line = "/api/jellyfin/phase1/%s/asset/%s" % (item_id, encoded)
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
