# Vision Model Evaluation Dashboard

Interactive dashboard and image gallery for reviewing AI niche classification results across 6 vision models.

## Prerequisites

- Python 3.10+
- pip

## Install Dependencies

```bash
pip install flask Pillow
```

## Start the Server

```bash
python3 -m flask --app dashboard.app run --port 5050
```

Then open http://localhost:5050 in your browser.

### Share via Public URL

```bash
bash dashboard/start.sh
```

This starts the server and opens a [localtunnel](https://theboroer.github.io/localtunnel-www/) public URL (requires Node.js/npx). The tunnel password (your public IP) is printed in the terminal.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Analytics dashboard — KPIs, charts, heatmaps, model comparison |
| `/gallery` | Image gallery — browse all 304 evaluated images with filters and per-model verdicts |
| `/api/data` | JSON API for dashboard metrics |
| `/api/gallery` | JSON API for gallery data |

## Project Structure

```
dashboard/
  app.py              # Flask backend — data loading, metrics, routes
  templates/
    index.html        # Analytics dashboard (Chart.js)
    gallery.html      # Image gallery with filters and detail modal
  static/
    thumbs/           # Auto-generated thumbnail cache (created on first request)
```

## Data Sources

The dashboard reads from these files in the project root:

- `report.json` — Post metadata (owners, titles, likes, dates)
- `evaluation/niche_analysis_results_*.csv` — Model classification results
- `downloads/<niche>/*.jpg` — Source images served as fallback
