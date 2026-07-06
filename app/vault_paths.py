import os


PUBLIC_VIDEO_PREFIX = "/videos/"


def vault_relative_path(filepath, vault_dir=None):
    """Normalize filesystem and historical DB paths to a vault-relative path."""
    if filepath is None:
        raise ValueError("Video filepath is missing")

    raw = os.fspath(filepath).strip()
    if not raw:
        raise ValueError("Video filepath is empty")

    vault = os.path.realpath(vault_dir or os.environ["VAULTTUBE_VAULTDIR"])
    candidate = os.path.realpath(raw)
    try:
        if os.path.isabs(raw) and os.path.commonpath((vault, candidate)) == vault:
            raw = os.path.relpath(candidate, vault)
    except ValueError:
        pass

    raw = raw.replace("\\", "/").lstrip("/")
    if raw.startswith(PUBLIC_VIDEO_PREFIX.lstrip("/")):
        raw = raw[len(PUBLIC_VIDEO_PREFIX.lstrip("/")):]

    normalized = os.path.normpath(raw)
    if normalized in ("", ".") or normalized == ".." or normalized.startswith("../"):
        raise ValueError("Video filepath escapes the vault")
    return normalized


def resolve_vault_path(filepath, vault_dir=None):
    """Resolve a video path beneath the vault, rejecting traversal."""
    vault = os.path.realpath(vault_dir or os.environ["VAULTTUBE_VAULTDIR"])
    relative = vault_relative_path(filepath, vault_dir=vault)
    resolved = os.path.realpath(os.path.join(vault, relative))
    if os.path.commonpath((vault, resolved)) != vault:
        raise ValueError("Video filepath escapes the vault")
    return resolved


def public_video_path(filepath):
    """Build the canonical URL path for a stored video."""
    return PUBLIC_VIDEO_PREFIX + vault_relative_path(filepath).replace(os.sep, "/")
