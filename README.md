# Niche Moderation Demo

Vision-model evaluation dashboard for content niche classification.

10 vision models scored across 14 niches over 304 images, with per-image
ground-truth labelling, model agreement analysis, and calibration metrics.

## Stack

Flask + Pillow · server-rendered templates · gunicorn · UV-managed deps

## Run locally

```bash
python3 -m flask --app dashboard.app run --port 5050
# → http://localhost:5050
```

## Run as a container

```bash
python3 scripts/optimize_images.py     # one-time, builds downloads_optimized/
docker build -t niche-content-dashboard .
docker run --rm -p 5050:5050 niche-content-dashboard
```

See [DOCKER.md](./DOCKER.md) for details.

## Pages

| Route | Description |
|---|---|
| `/` | KPIs, charts, model × niche heatmaps |
| `/gallery` | Browse images, filter by niche / model / agreement, label as ground truth |
| `/api/data` · `/api/gallery` · `/api/labels` | JSON APIs |

## Repo layout

```
dashboard/            Flask app, templates, static
evaluation/           Per-model classification CSVs
scripts/              Image optimizer for the Docker build
report.json           Post metadata + CDN URLs
downloads_optimized/  300 source images @ 1280px (Git LFS)
Dockerfile  pyproject.toml  .gitlab-ci.yml
```
