#!/usr/bin/env python3
"""Download niche media images and generate a summary report."""

import json
import os
import sys
import time
import hashlib
from typing import Optional, List, Dict
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

# Unbuffered output
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

# === Configuration ===
BASE_DIR = Path(__file__).parent / "downloads"
REPORT_PATH = Path(__file__).parent / "report.json"
REPORT_TEXT_PATH = Path(__file__).parent / "report.txt"
API_URL = "https://beta.cam4.com/graph?operation=getNicheMedia&ssr=false"
PAGE_SIZE = 12
DELAY_BETWEEN_REQUESTS = 1.0  # seconds between API calls
DELAY_BETWEEN_DOWNLOADS = 0.3  # seconds between image downloads

NICHES = [
    "feet", "tattoos", "milf", "cosplay", "creampie",
    "masturbation", "blowjob", "bdsm", "anal", "fem-dom",
    "bbw", "squirt", "18-plus-girls", "pov-joi", "big-cock",
]

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
      description
      title
      media {
        id
        type
        sourceURL
        previewURL
        status
        duration
        __typename
      }
      metadata {
        id
        text
        background
        __typename
      }
      ownerUserName
      ownerGender
      ownerProfilePicture
      date
      commentsCount
      likes
      isLikedByViewer
      cover {
        id
        url
        __typename
      }
      coverBlurred
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


def get_extension_from_url(url: str) -> str:
    """Extract file extension from URL, default to .jpg."""
    parsed = urlparse(url)
    path = parsed.path
    if "." in path.split("/")[-1]:
        ext = "." + path.split("/")[-1].rsplit(".", 1)[-1]
        if ext.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"):
            return ext.lower()
    return ".jpg"


def fetch_niche_page(session: requests.Session, slug: str, cursor: Optional[str] = None) -> dict:
    """Fetch one page of niche media."""
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


def download_file(session: requests.Session, url: str, dest: Path, seen_hashes: set) -> bool:
    """Download a file if it doesn't already exist and isn't a duplicate. Returns True on success."""
    if dest.exists():
        return True
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        content = resp.content
        content_hash = hashlib.md5(content).hexdigest()
        if content_hash in seen_hashes:
            print(f"    [SKIP] Duplicate content: {dest.name}")
            return False
        seen_hashes.add(content_hash)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"    [ERROR] Failed to download {url}: {e}")
        return False


def process_niche(session: requests.Session, slug: str) -> Dict:
    """Process all pages for a niche, download images, return stats."""
    niche_dir = BASE_DIR / slug
    niche_dir.mkdir(parents=True, exist_ok=True)

    # Track content hashes to skip duplicates within this niche
    seen_hashes = set()
    # Hash existing files so re-runs don't re-download
    for existing in niche_dir.iterdir():
        if existing.is_file():
            seen_hashes.add(hashlib.md5(existing.read_bytes()).hexdigest())

    stats = {
        "slug": slug,
        "total_posts": 0,
        "total_images_downloaded": 0,
        "total_videos_found": 0,
        "total_videos_downloaded": 0,
        "total_download_errors": 0,
        "posts": [],
    }

    cursor = None
    page = 0

    while True:
        page += 1
        print(f"  [Page {page}] Fetching (cursor={cursor})")

        try:
            data = fetch_niche_page(session, slug, cursor)
        except Exception as e:
            print(f"  [ERROR] API request failed: {e}")
            break

        niche_posts = data.get("data", {}).get("nichePosts")
        if not niche_posts:
            print("  [WARN] No nichePosts in response")
            break

        items = niche_posts.get("items", [])
        if not items:
            print("  [INFO] No more items")
            break

        for item in items:
            post_id = item.get("id", "unknown")
            post_type = item.get("type", "unknown")
            owner = item.get("ownerUserName", "unknown")
            title = item.get("title", "")
            description = item.get("description", "")
            likes = item.get("likes", 0)
            comments = item.get("commentsCount", 0)
            date = item.get("date", "")

            post_info = {
                "id": post_id,
                "type": post_type,
                "owner": owner,
                "title": title,
                "likes": likes,
                "comments": comments,
                "date": date,
                "media_files": [],
                "media_urls": {},
            }

            stats["total_posts"] += 1
            media_list = item.get("media") or []

            for media in media_list:
                media_type = media.get("type", "")
                source_url = media.get("sourceURL", "")
                preview_url = media.get("previewURL", "")
                media_id = media.get("id", "unknown")

                # Download images (sourceURL for images)
                if media_type == "IMAGE" and source_url:
                    ext = get_extension_from_url(source_url)
                    filename = f"{owner}_{post_id}_{media_id}{ext}"
                    dest = niche_dir / filename

                    success = download_file(session, source_url, dest, seen_hashes)
                    if success:
                        stats["total_images_downloaded"] += 1
                        post_info["media_files"].append(filename)
                        post_info["media_urls"][filename] = {
                            "source_url": source_url,
                            "preview_url": preview_url,
                        }
                    else:
                        stats["total_download_errors"] += 1
                    time.sleep(DELAY_BETWEEN_DOWNLOADS)

                elif media_type == "VIDEO":
                    stats["total_videos_found"] += 1
                    # Download the actual video file
                    if source_url:
                        ext = get_extension_from_url(source_url)
                        if ext == ".jpg":
                            ext = ".mp4"  # default video extension
                        filename = f"{owner}_{post_id}_{media_id}{ext}"
                        dest = niche_dir / filename

                        success = download_file(session, source_url, dest, seen_hashes)
                        if success:
                            stats["total_videos_downloaded"] += 1
                            post_info["media_files"].append(filename)
                            post_info["media_urls"][filename] = {
                                "source_url": source_url,
                                "preview_url": preview_url,
                            }
                        else:
                            stats["total_download_errors"] += 1
                        time.sleep(DELAY_BETWEEN_DOWNLOADS)

            stats["posts"].append(post_info)

        next_cursor = niche_posts.get("nextCursor")
        if not next_cursor:
            print("  [INFO] No more pages (nextCursor is null)")
            break

        cursor = next_cursor
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return stats


def generate_report(all_stats: List[Dict]):
    """Generate JSON and text reports."""
    report = {
        "generated_at": datetime.now().isoformat(),
        "total_niches": len(all_stats),
        "grand_total_images": sum(s["total_images_downloaded"] for s in all_stats),
        "grand_total_posts": sum(s["total_posts"] for s in all_stats),
        "grand_total_videos_found": sum(s["total_videos_found"] for s in all_stats),
        "grand_total_videos_downloaded": sum(s.get("total_videos_downloaded", 0) for s in all_stats),
        "grand_total_errors": sum(s["total_download_errors"] for s in all_stats),
        "niches": all_stats,
    }

    # JSON report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    # Text report
    lines = [
        "=" * 60,
        "NICHE CONTENT DOWNLOAD REPORT",
        f"Generated: {report['generated_at']}",
        "=" * 60,
        "",
        f"Total Niches Processed: {report['total_niches']}",
        f"Total Posts Found:      {report['grand_total_posts']}",
        f"Total Images Downloaded: {report['grand_total_images']}",
        f"Total Videos Found:     {report['grand_total_videos_found']}",
        f"Total Videos Downloaded: {report['grand_total_videos_downloaded']}",
        f"Total Errors:           {report['grand_total_errors']}",
        "",
        "-" * 70,
        f"{'Niche':<20} {'Posts':>6} {'Images':>8} {'VidFound':>9} {'VidDL':>7} {'Errors':>8}",
        "-" * 70,
    ]

    for s in all_stats:
        lines.append(
            f"{s['slug']:<20} {s['total_posts']:>6} "
            f"{s['total_images_downloaded']:>8} {s['total_videos_found']:>9} "
            f"{s.get('total_videos_downloaded', 0):>7} "
            f"{s['total_download_errors']:>8}"
        )

    lines.append("-" * 60)
    lines.append("")

    # Top contributors per niche
    lines.append("TOP CONTRIBUTORS PER NICHE:")
    lines.append("")
    for s in all_stats:
        owner_counts = {}
        for post in s["posts"]:
            owner = post["owner"]
            owner_counts[owner] = owner_counts.get(owner, 0) + 1
        if owner_counts:
            sorted_owners = sorted(owner_counts.items(), key=lambda x: -x[1])
            top = sorted_owners[:5]
            lines.append(f"  {s['slug']}:")
            for owner, count in top:
                lines.append(f"    {owner}: {count} posts")
            lines.append("")

    # Most liked posts across all niches
    lines.append("MOST LIKED POSTS (top 20):")
    lines.append("")
    all_posts = []
    for s in all_stats:
        for post in s["posts"]:
            all_posts.append({**post, "niche": s["slug"]})
    all_posts.sort(key=lambda x: -(x.get("likes") or 0))
    for post in all_posts[:20]:
        lines.append(
            f"  [{post['niche']}] {post['owner']} - "
            f"{post.get('title', 'No title')!r} "
            f"({post.get('likes', 0)} likes, {post.get('comments', 0)} comments)"
        )

    lines.append("")
    lines.append("=" * 60)

    with open(REPORT_TEXT_PATH, "w") as f:
        f.write("\n".join(lines))

    print(f"\nReports saved to:")
    print(f"  {REPORT_PATH}")
    print(f"  {REPORT_TEXT_PATH}")


def main():
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.cookies.update(COOKIES)

    all_stats = []

    for i, slug in enumerate(NICHES, 1):
        print(f"\n{'='*50}")
        print(f"[{i}/{len(NICHES)}] Processing niche: {slug}")
        print(f"{'='*50}")

        stats = process_niche(session, slug)
        all_stats.append(stats)

        print(f"  => Posts: {stats['total_posts']}, "
              f"Images: {stats['total_images_downloaded']}, "
              f"Videos: {stats['total_videos_downloaded']}/{stats['total_videos_found']}, "
              f"Errors: {stats['total_download_errors']}")

        # Generate intermediate report after each niche
        generate_report(all_stats)

        if i < len(NICHES):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    print("\n\nDone!")
    generate_report(all_stats)


if __name__ == "__main__":
    main()
