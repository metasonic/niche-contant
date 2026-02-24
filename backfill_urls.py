#!/usr/bin/env python3
"""Backfill CDN URLs into report.json by re-fetching API data (no file downloads)."""

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# Unbuffered output
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

# === Reuse constants from download_niches.py ===
REPORT_PATH = Path(__file__).parent / "report.json"
API_URL = "https://beta.cam4.com/graph?operation=getNicheMedia&ssr=false"
PAGE_SIZE = 12
DELAY_BETWEEN_REQUESTS = 1.0

GRAPHQL_QUERY = """query getNicheMedia($slug: String!, $size: Int!, $cursor: String, $sort: String, $gender: String!, $mediaType: String) {
  nichePosts(
    slug: $slug
    size: $size
    cursor: $cursor
    sort: $sort
    gender: $gender
    mediaType: $mediaType
  ) {
    id
    items {
      id
      type
      media {
        id
        type
        sourceURL
        previewURL
        __typename
      }
      ownerUserName
      __typename
    }
    nextCursor
    __typename
  }
}"""

COOKIES = {
    "intercom-device-id-xku5pmiv": "fe9c0237-78f3-4820-9eac-a3cdb796ef95",
    "cam4_user_language": "en",
    "cam4_SESSION_ID": "60469313-0f80-47ac-9d1d-35277e2bb94f",
    "disclaimer18": "Accepted",
    "cam4-CONTENT": "uyvv9Z",
    "cam4-AH": "ACED000577C5010DAD6A0007636963656C323100B64F53315369584F33784B73524C6357736E4D76556F4855386D51696E617177376F3339664A3243727769686951584E3242504E6E5273626968534E525F68707668595A6749685F5937635F4B6F6F544D4A4B533455636F4C70376C7A6149397675424B5179655F554935507654353544686770512D304F70524D384E6545493942627754546C6E6A42736461733978624179663751355F56624469456155754936786E61665050537A2D336E494C6C59486B51677067",
    "INGRESSCOOKIE": "a9fe3b0d54d097636061db447432f719|d6a68dccd9919960ff135dee3820d14b",
    "JSESSIONID": "49C322490547A2F13300AD02FE697D59",
}

HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "DNT": "1",
    "Origin": "https://beta.cam4.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "accept": "*/*",
    "apollographql-client-name": "CAM4-client",
    "apollographql-client-version": "26.2.18-124143utc",
    "content-type": "application/json",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


def fetch_niche_page(session: requests.Session, slug: str, cursor: Optional[str] = None) -> dict:
    """Fetch one page of niche media (metadata only, no downloads)."""
    payload = {
        "operationName": "getNicheMedia",
        "variables": {
            "slug": slug,
            "size": PAGE_SIZE,
            "cursor": cursor,
            "sort": "TRENDING",
            "gender": "FEMALE",
            "mediaType": None,
        },
        "query": GRAPHQL_QUERY,
    }
    referer = f"https://beta.cam4.com/niches/{slug}?tab=media"
    headers = {**HEADERS, "Referer": referer}
    resp = session.post(API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_cdn_lookup(session: requests.Session, slug: str) -> dict:
    """Fetch all API pages for a niche and build {postId}_{mediaId} → URLs lookup."""
    lookup = {}
    cursor = None
    page = 0

    while True:
        page += 1
        try:
            data = fetch_niche_page(session, slug, cursor)
        except Exception as e:
            print(f"    [ERROR] API request failed on page {page}: {e}")
            break

        niche_posts = data.get("data", {}).get("nichePosts")
        if not niche_posts:
            break

        items = niche_posts.get("items", [])
        if not items:
            break

        for item in items:
            post_id = item.get("id", "")
            for media in item.get("media") or []:
                media_id = media.get("id", "")
                source_url = media.get("sourceURL", "")
                preview_url = media.get("previewURL", "")
                if post_id and media_id and (source_url or preview_url):
                    key = f"{post_id}_{media_id}"
                    lookup[key] = {
                        "source_url": source_url,
                        "preview_url": preview_url,
                    }

        next_cursor = niche_posts.get("nextCursor")
        if not next_cursor:
            break
        cursor = next_cursor
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return lookup


def parse_filename_key(filename: str) -> Optional[str]:
    """Extract '{postId}_{mediaId}' from a filename like 'owner_postId_mediaId.ext'.

    Filenames follow the pattern: {ownerUserName}_{postId}_{mediaId}.{ext}
    Owner names can contain underscores (e.g. Anna_XOX), so we parse from
    the right: the last two underscore-separated segments before the extension
    are mediaId and postId.
    """
    stem = Path(filename).stem  # strip extension
    parts = stem.rsplit("_", 2)
    if len(parts) >= 3:
        post_id = parts[-2]
        media_id = parts[-1]
        return f"{post_id}_{media_id}"
    return None


def main():
    if not REPORT_PATH.exists():
        print(f"ERROR: {REPORT_PATH} not found")
        sys.exit(1)

    # Load report
    with open(REPORT_PATH, encoding="utf-8") as f:
        report = json.load(f)

    # Backup original
    backup_path = REPORT_PATH.with_suffix(".json.bak")
    shutil.copy2(REPORT_PATH, backup_path)
    print(f"Backed up original to {backup_path}")

    session = requests.Session()
    session.cookies.update(COOKIES)

    total_backfilled = 0
    total_already_set = 0
    total_not_found = 0

    niches = report.get("niches", [])
    for i, niche in enumerate(niches, 1):
        slug = niche["slug"]
        posts = niche.get("posts", [])
        print(f"\n[{i}/{len(niches)}] {slug} ({len(posts)} posts)")

        # Fetch CDN URLs from API
        lookup = build_cdn_lookup(session, slug)
        print(f"  API returned {len(lookup)} media URL entries")

        # Patch each post
        niche_filled = 0
        for post in posts:
            # Ensure media_urls dict exists
            if "media_urls" not in post:
                post["media_urls"] = {}

            for filename in post.get("media_files", []):
                # Skip if already populated
                if filename in post["media_urls"] and post["media_urls"][filename].get("source_url"):
                    total_already_set += 1
                    continue

                key = parse_filename_key(filename)
                if key and key in lookup:
                    post["media_urls"][filename] = lookup[key]
                    niche_filled += 1
                    total_backfilled += 1
                else:
                    total_not_found += 1

        print(f"  Backfilled {niche_filled} URLs")

        if i < len(niches):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # Write updated report
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"BACKFILL COMPLETE")
    print(f"{'='*50}")
    print(f"  URLs backfilled:  {total_backfilled}")
    print(f"  Already present:  {total_already_set}")
    print(f"  Not found in API: {total_not_found}")
    print(f"\nUpdated {REPORT_PATH}")
    print(f"Backup at {backup_path}")


if __name__ == "__main__":
    main()
