#!/usr/bin/env python3
"""Smoke-test companion playback against a running VaultTube/Jellyfin stack."""

import argparse
import sys
import time
from urllib.parse import urljoin

import requests


def api_json(session, method, url, **kwargs):
    response = session.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(body.get("error") or "API request failed")
    return body["data"]


def first_uri(manifest):
    return next(
        (line.strip() for line in manifest.splitlines()
         if line.strip() and not line.startswith("#")),
        None,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--reaction-id", required=True)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--reaction-start", type=float, default=30.125)
    parser.add_argument("--companion-start", type=float, default=10.375)
    args = parser.parse_args()

    base = args.base_url.rstrip("/") + "/"
    session = requests.Session()

    health = api_json(session, "GET", urljoin(base, "api/health"))
    status = api_json(session, "GET", urljoin(base, "api/jellyfin/phase1/status"))
    profiles = {row["id"] for row in status.get("profiles", [])}
    if not status.get("configured") or args.profile not in profiles:
        raise RuntimeError("Requested Jellyfin profile is not configured")

    query = {"profile": args.profile, "limit": 20}
    series = api_json(
        session, "GET", urljoin(base, "api/jellyfin/library/series"), params=query)
    if not series:
        raise RuntimeError("Jellyfin fixture has no series")
    seasons = api_json(
        session, "GET", urljoin(base, "api/jellyfin/library/seasons"),
        params={**query, "parent_id": series[0]["id"]})
    if not seasons:
        raise RuntimeError("Jellyfin fixture has no seasons")
    episodes = api_json(
        session, "GET", urljoin(base, "api/jellyfin/library/episodes"),
        params={**query, "parent_id": seasons[0]["id"]})
    if not episodes:
        raise RuntimeError("Jellyfin fixture has no episodes")
    item_id = episodes[0]["id"]

    manifest_url = urljoin(
        base, "api/jellyfin/phase1/%s/manifest.m3u8" % item_id)
    master = session.get(
        manifest_url, params={"profile": args.profile}, timeout=30)
    master.raise_for_status()
    if "#EXTM3U" not in master.text or "X-Emby-Token" in master.text:
        raise RuntimeError("Unsafe or invalid Jellyfin master manifest")
    child_uri = first_uri(master.text)
    if not child_uri:
        raise RuntimeError("Jellyfin master manifest has no child stream")
    child_url = urljoin(base, child_uri)
    child = session.get(child_url, timeout=30)
    child.raise_for_status()
    segment_uri = first_uri(child.text)
    if not segment_uri:
        raise RuntimeError("Jellyfin child manifest has no media segment")
    segment = session.get(
        urljoin(base, segment_uri), headers={"Range": "bytes=0-4095"}, timeout=30)
    segment.raise_for_status()
    if not segment.content or segment.content[0] != 0x47:
        raise RuntimeError("Jellyfin proxy did not return MPEG-TS media")

    composite = api_json(
        session, "POST", urljoin(base, "api/companion/composite"), json={
            "reaction_id": args.reaction_id,
            "server_profile_id": args.profile,
            "jellyfin_item_id": item_id,
            "reaction_start": args.reaction_start,
            "companion_start": args.companion_start,
        })
    composite_segment_url = urljoin(
        base,
        "api/companion/composite/%s/seg_0.ts" % composite["session_id"],
    )
    composite_segment = None
    for _ in range(4):
        composite_segment = session.get(composite_segment_url, timeout=35)
        if composite_segment.ok:
            break
        time.sleep(1)
    composite_segment.raise_for_status()
    if not composite_segment.content or composite_segment.content[0] != 0x47:
        raise RuntimeError("Composite endpoint did not return MPEG-TS media")

    print("Companion stack verification passed")
    print("  revision: %s" % health.get("revision", "unknown"))
    print("  profile: %s" % args.profile)
    print("  series/season/episode: %s / %s / %s" % (
        series[0]["name"], seasons[0]["name"], episodes[0]["name"]))
    print("  proxied range: HTTP %s, %s bytes" % (
        segment.status_code, len(segment.content)))
    print("  composite: %s, %s bytes" % (
        composite["session_id"], len(composite_segment.content)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Companion stack verification failed: %s" % error, file=sys.stderr)
        sys.exit(1)
