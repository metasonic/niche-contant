#!/usr/bin/env python3
"""Optimize the downloads/ tree for inclusion in the Docker image.

- Resizes images to a max edge (default 1280px) preserving aspect ratio.
- Re-encodes as JPEG (q82) with metadata stripped.
- Skips videos (the dashboard filters them out anyway).
- Writes to a sibling output directory, preserving folder/filename so URL
  paths in the Flask app keep working unchanged.

Usage:
    python3 scripts/optimize_images.py
    python3 scripts/optimize_images.py --max-dim 1024 --quality 80
    python3 scripts/optimize_images.py --src downloads --dst downloads_optimized
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageOps

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def optimize_one(src: Path, dst: Path, max_dim: int, quality: int) -> tuple[int, int]:
    """Optimize a single image. Returns (src_bytes, dst_bytes)."""
    src_bytes = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        # Honor EXIF orientation, then strip all metadata.
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(im.size) > max_dim:
            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        # Always write JPEG (smaller + universally supported by browsers).
        im.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)

    return src_bytes, dst.stat().st_size


def discover(src_root: Path) -> list[Path]:
    files: list[Path] = []
    for p in src_root.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXTS:
            continue
        if ext not in IMAGE_EXTS:
            continue
        files.append(p)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="downloads", help="Source directory (default: downloads)")
    parser.add_argument("--dst", default="downloads_optimized", help="Output directory (default: downloads_optimized)")
    parser.add_argument("--max-dim", type=int, default=1280, help="Max edge in pixels (default: 1280)")
    parser.add_argument("--quality", type=int, default=82, help="JPEG quality 1-95 (default: 82)")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers (default: 8)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    src_root = (repo_root / args.src).resolve()
    dst_root = (repo_root / args.dst).resolve()

    if not src_root.exists():
        print(f"error: source {src_root} not found", file=sys.stderr)
        return 1

    files = discover(src_root)
    print(f"Optimizing {len(files)} images")
    print(f"  src: {src_root}")
    print(f"  dst: {dst_root}")
    print(f"  max-dim={args.max_dim}px  quality={args.quality}  workers={args.workers}")

    total_src = total_dst = ok = fail = 0
    futures = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for src in files:
            rel = src.relative_to(src_root)
            # Always use .jpg (we're re-encoding to JPEG).
            dst = dst_root / rel.with_suffix(".jpg")
            futures[pool.submit(optimize_one, src, dst, args.max_dim, args.quality)] = src

        for i, fut in enumerate(as_completed(futures), 1):
            src = futures[fut]
            try:
                a, b = fut.result()
                total_src += a
                total_dst += b
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  FAIL {src}: {e}", file=sys.stderr)
            if i % 25 == 0 or i == len(futures):
                print(f"  [{i}/{len(futures)}]")

    mb = lambda n: n / 1024 / 1024
    ratio = total_dst / total_src if total_src else 0
    print()
    print(f"Done: {ok} ok, {fail} failed")
    print(f"  before: {mb(total_src):,.1f} MB")
    print(f"  after:  {mb(total_dst):,.1f} MB  ({ratio*100:.1f}%)")
    print(f"  saved:  {mb(total_src - total_dst):,.1f} MB")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
