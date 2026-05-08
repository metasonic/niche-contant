#!/usr/bin/env python3
"""AI Model Evaluation Dashboard — Flask backend.

Reads evaluation CSVs dynamically, computes aggregate metrics, resolves
CDN image URLs from report.json, and serves a management‑ready dashboard.
"""

import csv
import json
import os
import tempfile
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image
from werkzeug.middleware.proxy_fix import ProxyFix

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
REPORT_JSON = PROJECT_ROOT / "report.json"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
THUMBS_DIR = Path(__file__).resolve().parent / "static" / "thumbs"

app = Flask(__name__)
# Honor X-Forwarded-Proto/Host if the reverse proxy sends them. Sub-path
# mounting is handled client-side: templates derive the mount prefix from
# window.location.pathname and prepend it to internal URLs, so neither
# X-Forwarded-Prefix nor a SCRIPT_NAME env var is required.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ---------------------------------------------------------------------------
# Human Labels (Ground Truth)
# ---------------------------------------------------------------------------
LABELS_PATH = Path(__file__).resolve().parent / "human_labels.json"


class HumanLabels:
    """Thread-safe singleton for human ground-truth labels.

    File format: {"version": 1, "labels": {"folder/image.jpg": {"verdict": "YES", "timestamp": "..."}}}
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.Lock()
            cls._instance._cache = None
        return cls._instance

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if LABELS_PATH.exists():
            with open(LABELS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._cache = data.get("labels", {})
        else:
            self._cache = {}
        return self._cache

    def _save(self):
        payload = json.dumps(
            {"version": 1, "labels": self._cache},
            indent=2,
            ensure_ascii=False,
        )
        fd, tmp = tempfile.mkstemp(
            dir=str(LABELS_PATH.parent), suffix=".tmp"
        )
        closed = False
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            closed = True
            os.replace(tmp, str(LABELS_PATH))
        except Exception:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get_all(self) -> dict:
        with self._lock:
            return dict(self._load())

    def get(self, key: str):
        with self._lock:
            entry = self._load().get(key)
            return entry["verdict"] if entry else None

    def set_label(self, key: str, verdict: str):
        verdict = verdict.strip().upper()
        if verdict not in ("YES", "NO"):
            raise ValueError(f"verdict must be YES or NO, got {verdict!r}")
        with self._lock:
            self._load()
            self._cache[key] = {
                "verdict": verdict,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._save()

    def clear_label(self, key: str):
        with self._lock:
            self._load()
            self._cache.pop(key, None)
            self._save()

    def stats(self) -> dict:
        with self._lock:
            labels = self._load()
            total = len(labels)
            yes = sum(1 for v in labels.values() if v["verdict"] == "YES")
            return {"total": total, "yes": yes, "no": total - yes}


human_labels = HumanLabels()

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _discover_csvs() -> list[Path]:
    """Return all evaluation CSV files sorted by name."""
    if not EVALUATION_DIR.exists():
        return []
    return sorted(EVALUATION_DIR.glob("niche_analysis_results_*.csv"))


def _parse_csv(path: Path) -> list[dict]:
    """Parse a single evaluation CSV into a list of row dicts."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["probability"] = float(row.get("probability", 0))
            except (ValueError, TypeError):
                row["probability"] = 0.0
            row["belongs_to_niche"] = row.get("belongs_to_niche", "").strip().upper()
            # Skip error rows (empty verdict from failed API calls)
            if row["belongs_to_niche"] not in ("YES", "NO"):
                continue
            rows.append(row)
    return rows


def _build_url_map() -> dict[str, dict]:
    """Build filename → {source_url, preview_url} mapping from report.json.

    Works with both the legacy format (media_files as plain strings) and the
    enhanced format (media_urls dict alongside media_files).
    """
    url_map: dict[str, dict] = {}
    if not REPORT_JSON.exists():
        return url_map
    with open(REPORT_JSON, encoding="utf-8") as f:
        report = json.load(f)
    for niche in report.get("niches", []):
        for post in niche.get("posts", []):
            media_urls = post.get("media_urls", {})
            for filename in post.get("media_files", []):
                if filename in media_urls:
                    url_map[filename] = media_urls[filename]
    return url_map


def _load_report_meta() -> dict:
    """Load top‑level metadata from report.json."""
    if not REPORT_JSON.exists():
        return {}
    with open(REPORT_JSON, encoding="utf-8") as f:
        report = json.load(f)
    return {
        "generated_at": report.get("generated_at", ""),
        "total_niches": report.get("total_niches", 0),
        "grand_total_images": report.get("grand_total_images", 0),
        "grand_total_posts": report.get("grand_total_posts", 0),
        "grand_total_videos_downloaded": report.get("grand_total_videos_downloaded", 0),
    }

def _build_post_lookup() -> dict[str, dict]:
    """Map image filenames to post metadata from report.json.

    Returns {filename: {niche, owner, title, likes, comments, date, post_id}}.
    Skips video files (.mp4, .mov, .avi, .webm).
    """
    lookup: dict[str, dict] = {}
    if not REPORT_JSON.exists():
        return lookup
    with open(REPORT_JSON, encoding="utf-8") as f:
        report = json.load(f)
    video_exts = {".mp4", ".mov", ".avi", ".webm"}
    for niche in report.get("niches", []):
        slug = niche.get("slug", "")
        for post in niche.get("posts", []):
            for filename in post.get("media_files", []):
                ext = Path(filename).suffix.lower()
                if ext in video_exts:
                    continue
                lookup[filename] = {
                    "niche": slug,
                    "owner": post.get("owner", ""),
                    "title": post.get("title", ""),
                    "likes": post.get("likes", 0),
                    "comments": post.get("comments", 0),
                    "date": post.get("date", ""),
                    "post_id": post.get("id", ""),
                }
    return lookup


def build_gallery_data() -> dict:
    """Build the full gallery dataset joining CSVs + report.json.

    Returns {items: [...], filters: {niches, models, owners}}.
    """
    csv_paths = _discover_csvs()
    url_map = _build_url_map()
    post_lookup = _build_post_lookup()
    all_labels = human_labels.get_all()

    all_rows: list[dict] = []
    for p in csv_paths:
        all_rows.extend(_parse_csv(p))

    if not all_rows:
        return {"items": [], "filters": {"niches": [], "models": [], "owners": []}, "label_stats": human_labels.stats()}

    model_short = {m: m.split("/")[-1] for m in sorted({r["model_name_name"] for r in all_rows})}

    # Group rows by image key (niche/filename)
    image_evals: dict[str, list[dict]] = defaultdict(list)
    for r in all_rows:
        key = f"{r['folder_name']}/{r['image_name']}"
        image_evals[key].append(r)

    items = []
    all_owners = set()

    for img_key, rows in image_evals.items():
        niche, filename = img_key.split("/", 1)

        # Skip videos
        ext = Path(filename).suffix.lower()
        if ext in {".mp4", ".mov", ".avi", ".webm"}:
            continue

        # Image URL
        if filename in url_map:
            image_url = url_map[filename].get("source_url", "")
        else:
            image_url = f"/image/{niche}/{filename}"
        thumb_url = f"/thumb/{niche}/{filename}"

        # Model evaluations
        evaluations = []
        verdicts = {}
        confidences = []
        for r in rows:
            short = model_short[r["model_name_name"]]
            verdict = r["belongs_to_niche"]
            conf = r["probability"]
            verdicts[short] = verdict
            confidences.append(conf)
            evaluations.append({
                "model": short,
                "verdict": verdict,
                "confidence": round(conf, 3),
                "reason": r.get("reason", ""),
            })

        # Agreement status
        unique_verdicts = set(verdicts.values())
        if len(unique_verdicts) == 1:
            agreement = "all_yes" if "YES" in unique_verdicts else "all_no"
        else:
            agreement = "disagreement"

        avg_conf = round(mean(confidences), 3) if confidences else 0

        # Post metadata
        meta = post_lookup.get(filename, {})
        owner = meta.get("owner", "")
        if owner:
            all_owners.add(owner)

        # Human label (ground truth)
        label_entry = all_labels.get(img_key)
        hl = label_entry["verdict"] if label_entry else None

        items.append({
            "key": img_key,
            "filename": filename,
            "niche": niche,
            "image_url": image_url,
            "thumb_url": thumb_url,
            "agreement": agreement,
            "avg_confidence": avg_conf,
            "evaluations": evaluations,
            "verdicts": verdicts,
            "human_label": hl,
            "owner": owner,
            "title": meta.get("title", ""),
            "likes": meta.get("likes", 0),
            "comments": meta.get("comments", 0),
            "date": meta.get("date", ""),
            "post_id": meta.get("post_id", ""),
        })

    niches = sorted({item["niche"] for item in items})
    models = sorted(model_short.values())
    owners = sorted(all_owners)

    return {
        "items": items,
        "filters": {
            "niches": niches,
            "models": models,
            "owners": owners,
        },
        "label_stats": human_labels.stats(),
    }


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_metrics():
    """Aggregate all evaluation CSVs into dashboard metrics."""
    csv_paths = _discover_csvs()
    url_map = _build_url_map()
    report_meta = _load_report_meta()

    all_rows: list[dict] = []
    for p in csv_paths:
        all_rows.extend(_parse_csv(p))

    if not all_rows:
        return {"error": "No evaluation data found"}

    # Unique dimensions
    models = sorted({r["model_name_name"] for r in all_rows})
    niches = sorted({r["folder_name"] for r in all_rows})
    model_short = {m: m.split("/")[-1] for m in models}

    # --- Per‑model metrics ---
    per_model: dict[str, dict] = {}
    for m in models:
        m_rows = [r for r in all_rows if r["model_name_name"] == m]
        total = len(m_rows)
        yes_count = sum(1 for r in m_rows if r["belongs_to_niche"] == "YES")
        probs = [r["probability"] for r in m_rows]
        per_model[model_short[m]] = {
            "full_name": m,
            "total": total,
            "yes_count": yes_count,
            "no_count": total - yes_count,
            "accuracy": round(yes_count / total * 100, 1) if total else 0,
            "avg_confidence": round(mean(probs), 3) if probs else 0,
            "median_confidence": round(median(probs), 3) if probs else 0,
            "std_confidence": round(stdev(probs), 3) if len(probs) > 1 else 0,
        }

    # --- Per‑niche metrics ---
    per_niche: dict[str, dict] = {}
    for n in niches:
        n_rows = [r for r in all_rows if r["folder_name"] == n]
        total = len(n_rows)
        yes_count = sum(1 for r in n_rows if r["belongs_to_niche"] == "YES")
        probs = [r["probability"] for r in n_rows]
        per_niche[n] = {
            "total": total,
            "yes_count": yes_count,
            "no_count": total - yes_count,
            "accuracy": round(yes_count / total * 100, 1) if total else 0,
            "avg_confidence": round(mean(probs), 3) if probs else 0,
        }

    # --- Model × Niche heatmap (accuracy %) ---
    heatmap: dict[str, dict[str, float]] = {}
    for m in models:
        short = model_short[m]
        heatmap[short] = {}
        for n in niches:
            mn_rows = [r for r in all_rows if r["model_name_name"] == m and r["folder_name"] == n]
            total = len(mn_rows)
            yes_c = sum(1 for r in mn_rows if r["belongs_to_niche"] == "YES")
            heatmap[short][n] = round(yes_c / total * 100, 1) if total else 0

    # --- Model × Niche confidence heatmap ---
    confidence_heatmap: dict[str, dict[str, float]] = {}
    for m in models:
        short = model_short[m]
        confidence_heatmap[short] = {}
        for n in niches:
            mn_rows = [r for r in all_rows if r["model_name_name"] == m and r["folder_name"] == n]
            probs = [r["probability"] for r in mn_rows]
            confidence_heatmap[short][n] = round(mean(probs), 3) if probs else 0

    # --- Model agreement (per image) ---
    image_verdicts: dict[str, dict[str, str]] = defaultdict(dict)
    for r in all_rows:
        key = f"{r['folder_name']}/{r['image_name']}"
        image_verdicts[key][model_short[r["model_name_name"]]] = r["belongs_to_niche"]

    total_images_eval = len(image_verdicts)
    unanimous_yes = 0
    unanimous_no = 0
    disagreements = 0
    disagreement_samples = []

    for img_key, verdicts in image_verdicts.items():
        vals = set(verdicts.values())
        if len(vals) == 1:
            if "YES" in vals:
                unanimous_yes += 1
            else:
                unanimous_no += 1
        else:
            disagreements += 1
            if len(disagreement_samples) < 20:
                disagreement_samples.append({
                    "image": img_key,
                    "verdicts": verdicts,
                })

    agreement_rate = round((unanimous_yes + unanimous_no) / total_images_eval * 100, 1) if total_images_eval else 0

    # --- Confidence distribution buckets per model ---
    conf_distribution: dict[str, list[int]] = {}
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for m in models:
        short = model_short[m]
        m_rows = [r for r in all_rows if r["model_name_name"] == m]
        probs = [r["probability"] for r in m_rows]
        counts = []
        for lo, hi in buckets:
            counts.append(sum(1 for p in probs if lo <= p < hi))
        conf_distribution[short] = counts

    # --- Lowest confidence classifications (interesting edge cases) ---
    sorted_by_conf = sorted(all_rows, key=lambda r: r["probability"])
    low_confidence = []
    for r in sorted_by_conf[:30]:
        entry = {
            "model": model_short[r["model_name_name"]],
            "niche": r["folder_name"],
            "image": r["image_name"],
            "verdict": r["belongs_to_niche"],
            "probability": r["probability"],
            "reason": r.get("reason", ""),
        }
        # Attach CDN URL if available
        if r["image_name"] in url_map:
            entry["image_url"] = url_map[r["image_name"]].get("source_url", "")
            entry["preview_url"] = url_map[r["image_name"]].get("preview_url", "")
        else:
            entry["image_url"] = f"/image/{r['folder_name']}/{r['image_name']}"
        low_confidence.append(entry)

    # --- Niche file counts from disk ---
    niche_file_counts = {}
    if DOWNLOADS_DIR.exists():
        for d in DOWNLOADS_DIR.iterdir():
            if d.is_dir():
                files = [f for f in d.iterdir() if f.is_file()]
                niche_file_counts[d.name] = len(files)

    # --- Overall KPIs ---
    total_rows = len(all_rows)
    total_yes = sum(1 for r in all_rows if r["belongs_to_niche"] == "YES")
    all_probs = [r["probability"] for r in all_rows]

    # --- Ground truth (human labels) ---
    all_labels = human_labels.get_all()
    label_stats = human_labels.stats()
    ground_truth = None

    if label_stats["total"] > 0:
        # Per-model: TP/FP/TN/FN vs human labels
        gt_per_model: dict[str, dict] = {}
        for m in models:
            short = model_short[m]
            tp = fp = tn = fn = 0
            m_rows = [r for r in all_rows if r["model_name_name"] == m]
            for r in m_rows:
                key = f"{r['folder_name']}/{r['image_name']}"
                lbl_entry = all_labels.get(key)
                if not lbl_entry:
                    continue
                gt = lbl_entry["verdict"]
                pred = r["belongs_to_niche"]
                if pred == "YES" and gt == "YES":
                    tp += 1
                elif pred == "YES" and gt == "NO":
                    fp += 1
                elif pred == "NO" and gt == "NO":
                    tn += 1
                elif pred == "NO" and gt == "YES":
                    fn += 1
            total_labeled = tp + fp + tn + fn
            accuracy = round((tp + tn) / total_labeled * 100, 1) if total_labeled else None
            precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) else None
            recall = round(tp / (tp + fn) * 100, 1) if (tp + fn) else None
            f1 = round(2 * tp / (2 * tp + fp + fn) * 100, 1) if (2 * tp + fp + fn) else None
            # Cohen's Kappa
            if total_labeled > 0:
                p_o = (tp + tn) / total_labeled
                p_yes = ((tp + fp) / total_labeled) * ((tp + fn) / total_labeled)
                p_no = ((tn + fn) / total_labeled) * ((tn + fp) / total_labeled)
                p_e = p_yes + p_no
                kappa = round((p_o - p_e) / (1 - p_e), 3) if p_e != 1 else None
            else:
                kappa = None

            # Specificity (True Negative Rate)
            specificity = round(tn / (tn + fp) * 100, 1) if (tn + fp) > 0 else None

            # ECE (Expected Calibration Error) — computed after calibration buckets
            # Calibration buckets for this model
            cal_buckets = []
            cal_bounds = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
            for lo, hi in cal_bounds:
                midpoint = round((lo + min(hi, 1.0)) / 2, 2)
                bucket_rows = []
                for r in m_rows:
                    key = f"{r['folder_name']}/{r['image_name']}"
                    lbl_entry = all_labels.get(key)
                    if not lbl_entry:
                        continue
                    prob = r["probability"]
                    if lo <= prob < hi:
                        correct = 1 if r["belongs_to_niche"] == lbl_entry["verdict"] else 0
                        bucket_rows.append((prob, correct))
                if bucket_rows:
                    avg_conf = round(mean([x[0] for x in bucket_rows]), 3)
                    actual_acc = round(sum(x[1] for x in bucket_rows) / len(bucket_rows) * 100, 1)
                    cal_buckets.append([midpoint, avg_conf, actual_acc, len(bucket_rows)])
                else:
                    cal_buckets.append([midpoint, None, None, 0])

            # ECE = weighted average |confidence - accuracy| across buckets
            ece_num = 0.0
            ece_den = 0
            for bucket in cal_buckets:
                _, avg_c, act_a, cnt = bucket
                if cnt > 0 and avg_c is not None and act_a is not None:
                    ece_num += cnt * abs(avg_c * 100 - act_a)
                    ece_den += cnt
            ece = round(ece_num / ece_den, 1) if ece_den > 0 else None

            gt_per_model[short] = {
                "tp": tp, "fp": fp, "tn": tn, "fn": fn,
                "total_labeled": total_labeled,
                "accuracy": accuracy, "precision": precision,
                "recall": recall, "f1": f1,
                "kappa": kappa, "specificity": specificity, "ece": ece,
                "calibration": cal_buckets,
            }

        # Per-niche accuracy vs ground truth
        gt_per_niche: dict[str, dict] = {}
        for n in niches:
            correct = total_l = 0
            n_rows = [r for r in all_rows if r["folder_name"] == n]
            for r in n_rows:
                key = f"{r['folder_name']}/{r['image_name']}"
                lbl_entry = all_labels.get(key)
                if not lbl_entry:
                    continue
                total_l += 1
                if r["belongs_to_niche"] == lbl_entry["verdict"]:
                    correct += 1
            gt_per_niche[n] = {
                "labeled": total_l,
                "accuracy": round(correct / total_l * 100, 1) if total_l else None,
            }

        # Model x Niche heatmap: accuracy vs ground truth
        gt_heatmap: dict[str, dict[str, float | None]] = {}
        for m in models:
            short = model_short[m]
            gt_heatmap[short] = {}
            for n in niches:
                correct = total_l = 0
                mn_rows = [r for r in all_rows if r["model_name_name"] == m and r["folder_name"] == n]
                for r in mn_rows:
                    key = f"{r['folder_name']}/{r['image_name']}"
                    lbl_entry = all_labels.get(key)
                    if not lbl_entry:
                        continue
                    total_l += 1
                    if r["belongs_to_niche"] == lbl_entry["verdict"]:
                        correct += 1
                gt_heatmap[short][n] = round(correct / total_l * 100, 1) if total_l else None

        # Overall accuracy across all models (treating each model-image pair independently)
        total_correct = sum(m["tp"] + m["tn"] for m in gt_per_model.values())
        total_labeled_all = sum(m["total_labeled"] for m in gt_per_model.values())
        overall_accuracy = round(total_correct / total_labeled_all * 100, 1) if total_labeled_all else None

        # Majority Vote Accuracy
        mv_correct = 0
        mv_total = 0
        for img_key, verdicts in image_verdicts.items():
            lbl_entry = all_labels.get(img_key)
            if not lbl_entry:
                continue
            yes_votes = sum(1 for v in verdicts.values() if v == "YES")
            no_votes = len(verdicts) - yes_votes
            majority = "YES" if yes_votes >= no_votes else "NO"  # ties → YES
            mv_total += 1
            if majority == lbl_entry["verdict"]:
                mv_correct += 1
        majority_vote = {
            "accuracy": round(mv_correct / mv_total * 100, 1) if mv_total else None,
            "correct": mv_correct,
            "total": mv_total,
        }

        # Calibration data restructured per-model for frontend
        calibration = {}
        for short, gm in gt_per_model.items():
            calibration[short] = gm.get("calibration", [])

        # Simple verdict-only map for frontend use (disagreement cards, etc.)
        label_map = {k: v["verdict"] for k, v in all_labels.items()}

        ground_truth = {
            "label_stats": label_stats,
            "overall_accuracy": overall_accuracy,
            "per_model": gt_per_model,
            "per_niche": gt_per_niche,
            "heatmap": gt_heatmap,
            "label_map": label_map,
            "majority_vote": majority_vote,
            "calibration": calibration,
        }

    return {
        "kpis": {
            "total_models": len(models),
            "total_niches": len(niches),
            "total_evaluations": total_rows,
            "total_images_evaluated": total_images_eval,
            "overall_yes_rate": round(total_yes / total_rows * 100, 1) if total_rows else 0,
            "avg_confidence": round(mean(all_probs), 3) if all_probs else 0,
            "agreement_rate": agreement_rate,
        },
        "models": list(model_short.values()),
        "models_full": {v: k for k, v in model_short.items()},
        "niches": niches,
        "per_model": per_model,
        "per_niche": per_niche,
        "heatmap": heatmap,
        "confidence_heatmap": confidence_heatmap,
        "agreement": {
            "total": total_images_eval,
            "unanimous_yes": unanimous_yes,
            "unanimous_no": unanimous_no,
            "disagreements": disagreements,
            "rate": agreement_rate,
            "samples": disagreement_samples,
        },
        "conf_distribution": conf_distribution,
        "low_confidence": low_confidence,
        "niche_file_counts": niche_file_counts,
        "report_meta": report_meta,
        "ground_truth": ground_truth,
    }

def heatmap_detail(model_short: str, niche: str) -> dict:
    """Per-image breakdown for a single model×niche cell in the confidence heatmap."""
    csv_paths = _discover_csvs()
    url_map = _build_url_map()

    all_rows: list[dict] = []
    for p in csv_paths:
        all_rows.extend(_parse_csv(p))

    # Build model short-name mapping
    model_names = sorted({r["model_name_name"] for r in all_rows})
    short_to_full = {}
    for m in model_names:
        short = m.split("/")[-1]
        short_to_full[short.lower()] = m

    full_name = short_to_full.get(model_short.lower())
    if not full_name:
        return {"error": f"Unknown model: {model_short}"}

    # Filter rows for this model + niche
    matched = [
        r for r in all_rows
        if r["model_name_name"] == full_name and r["folder_name"].lower() == niche.lower()
    ]

    if not matched:
        return {"error": f"No data for {model_short} × {niche}"}

    # Summary stats
    probs = [r["probability"] for r in matched]
    yes_count = sum(1 for r in matched if r["belongs_to_niche"] == "YES")
    no_count = len(matched) - yes_count
    summary = {
        "count": len(matched),
        "yes": yes_count,
        "no": no_count,
        "avg": round(mean(probs), 4),
        "median": round(median(probs), 4),
        "min": round(min(probs), 4),
        "max": round(max(probs), 4),
    }

    # Per-image items sorted by probability ascending
    all_labels = human_labels.get_all()
    items = []
    for r in sorted(matched, key=lambda r: r["probability"]):
        filename = r["image_name"]
        folder = r["folder_name"]
        if filename in url_map:
            image_url = url_map[filename].get("source_url", "")
        else:
            image_url = f"/image/{folder}/{filename}"
        thumb_url = f"/thumb/{folder}/{filename}"
        img_key = f"{folder}/{filename}"
        lbl_entry = all_labels.get(img_key)
        items.append({
            "filename": filename,
            "thumb_url": thumb_url,
            "image_url": image_url,
            "verdict": r["belongs_to_niche"],
            "probability": r["probability"],
            "reason": r.get("reason", ""),
            "human_label": lbl_entry["verdict"] if lbl_entry else None,
        })

    return {"summary": summary, "items": items}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    data = compute_metrics()
    return render_template("index.html", data=json.dumps(data))


@app.route("/api/data")
def api_data():
    return jsonify(compute_metrics())


@app.route("/gallery")
def gallery():
    data = build_gallery_data()
    return render_template("gallery.html", data=json.dumps(data))


@app.route("/api/gallery")
def api_gallery():
    return jsonify(build_gallery_data())


@app.route("/api/heatmap-detail")
def api_heatmap_detail():
    model = request.args.get("model", "")
    niche = request.args.get("niche", "")
    if not model or not niche:
        return jsonify({"error": "model and niche query params required"}), 400
    return jsonify(heatmap_detail(model, niche))


@app.route("/api/labels", methods=["GET"])
def api_labels_get():
    labels = human_labels.get_all()
    stats = human_labels.stats()
    return jsonify({"labels": labels, "stats": stats})


@app.route("/api/labels", methods=["POST"])
def api_labels_set():
    body = request.get_json(silent=True) or {}
    key = body.get("key", "").strip()
    verdict = body.get("verdict", "").strip().upper()
    if not key or verdict not in ("YES", "NO"):
        return jsonify({"error": "key and verdict (YES/NO) required"}), 400
    human_labels.set_label(key, verdict)
    return jsonify({"ok": True, "key": key, "verdict": verdict})


@app.route("/api/labels", methods=["DELETE"])
def api_labels_clear():
    body = request.get_json(silent=True) or {}
    key = body.get("key", "").strip()
    if not key:
        return jsonify({"error": "key required"}), 400
    human_labels.clear_label(key)
    return jsonify({"ok": True, "key": key, "verdict": None})


@app.route("/image/<niche>/<filename>")
def serve_image(niche, filename):
    """Serve a local image as fallback when CDN URL is unavailable."""
    niche_dir = DOWNLOADS_DIR / niche
    if niche_dir.exists():
        return send_from_directory(str(niche_dir), filename)
    return "Not found", 404


@app.route("/thumb/<niche>/<filename>")
def serve_thumb(niche, filename):
    """On-demand thumbnail generation (400px wide, JPEG q80). Cached."""
    thumb_dir = THUMBS_DIR / niche
    thumb_path = thumb_dir / filename

    # Serve cached thumbnail
    if thumb_path.exists():
        return send_from_directory(str(thumb_dir), filename)

    # Generate from source
    source = DOWNLOADS_DIR / niche / filename
    if not source.exists():
        return "Not found", 404

    thumb_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as img:
            img.thumbnail((400, 400))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            thumb_name = Path(filename).stem + ".jpg"
            thumb_path = thumb_dir / thumb_name
            img.save(thumb_path, "JPEG", quality=80)
        return send_from_directory(str(thumb_dir), thumb_name)
    except Exception:
        # Fall back to original image
        return send_from_directory(str(DOWNLOADS_DIR / niche), filename)


if __name__ == "__main__":
    print(f"  Evaluation dir: {EVALUATION_DIR}")
    print(f"  CSVs found:     {len(_discover_csvs())}")
    print(f"  Report.json:    {'found' if REPORT_JSON.exists() else 'missing'}")
    print(f"  Downloads dir:  {'found' if DOWNLOADS_DIR.exists() else 'missing'}")
    print()
    app.run(debug=True, port=5050)
