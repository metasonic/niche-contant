# Vision Model Evaluation Dashboard — Docker showcase

Self-contained Docker image of the dashboard at [`dashboard/`](./dashboard).
Showcases 10 vision models scored across 14 image niches with 304 ground-truth-labelled images.

## What's inside the image

| Component | Path in image | Source |
|---|---|---|
| Dashboard Flask app | `/app/dashboard/` | [`dashboard/`](./dashboard) |
| Evaluation results (10 CSVs) | `/app/evaluation/` | [`evaluation/`](./evaluation) |
| Post metadata + CDN URLs | `/app/report.json` | `report.json` |
| Source images, optimized | `/app/downloads/` | `downloads_optimized/` (generated) |

The image is fully self-contained — no external mounts required to run the showcase.

Mountable for persistence (optional):
- `/app/dashboard/human_labels.json` — ground-truth labels written from the gallery UI
- `/app/dashboard/static/thumbs/` — on-demand thumbnail cache

## Build

The image bakes in an **optimized** image set (1280px max, JPEG q82) generated
from the raw `downloads/` tree. Generate it once locally:

```bash
python3 scripts/optimize_images.py
# 423 MB → 36 MB (8.4 %)
```

Then build:

```bash
docker build -t niche-content-dashboard:latest .
```

Final image is ~120 MB (Python slim runtime + venv + 36 MB of images).

## Run

```bash
docker run --rm -p 5050:5050 niche-content-dashboard:latest
```

Open http://localhost:5050.

### Persist labels and thumbnail cache

```bash
docker run --rm -p 5050:5050 \
  -v "$(pwd)/dashboard/human_labels.json:/app/dashboard/human_labels.json" \
  -v "$(pwd)/dashboard/static/thumbs:/app/dashboard/static/thumbs" \
  niche-content-dashboard:latest
```

### Custom port

```bash
docker run --rm -p 8080:8080 -e DASHBOARD_PORT=8080 niche-content-dashboard:latest
```

## Pages

| Route | Description |
|---|---|
| `/` | Analytics dashboard — KPIs, charts, model × niche heatmaps |
| `/gallery` | Image gallery — browse 304 evaluated images, filter by niche/model/agreement, label as ground truth |
| `/api/data` | JSON metrics |
| `/api/gallery` | JSON gallery dataset |
| `/api/heatmap-detail?model=…&niche=…` | Per-cell drilldown |
| `/api/labels` | GET / POST / DELETE ground-truth labels |

## CI/CD (GitLab)

[`.gitlab-ci.yml`](./.gitlab-ci.yml) mirrors the kaniko-based flow:

1. `optimize-images` (prepare) — runs the optimizer if raw `downloads/` is present, exposes `downloads_optimized/` as artifact.
2. `build` (build) — kaniko builds and pushes to `${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA}` and `:latest`.

Triggers only on `main`.

## Stack

- **Server** — gunicorn (2 workers × 4 threads, 60 s timeout)
- **Deps** — UV-managed (`pyproject.toml` + `uv.lock`), installed into `/app/.venv`
- **Base** — `python:3.11-slim-bookworm` runtime, `ghcr.io/astral-sh/uv` builder
- **Port** — `5050` (override with `DASHBOARD_PORT`)
