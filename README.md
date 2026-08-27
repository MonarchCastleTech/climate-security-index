# Climate Security Index

[![Pages](https://github.com/MonarchCastleTech/climate-security-index/actions/workflows/pipeline.yml/badge.svg)](https://github.com/MonarchCastleTech/climate-security-index/actions/workflows/pipeline.yml)

Autonomous public-data early warning for climate-security pressure over a 0–90 day horizon. This is not a conflict, famine, displacement, or casualty forecast.

The reproducible score combines NASA POWER agroclimate stress (35%), GDACS multi-hazard impact pressure (25%), NASA EONET event persistence (20%), and FAO food-price pressure (20%). Missing components are excluded and weights renormalized. A five-point concurrence bonus requires climate evidence plus independent food-price elevation.

GitHub Actions runs every six hours, tests before refresh, caches sources for at most 72 hours, preserves last-good output on total physical-source failure, commits compact accepted snapshots, and deploys GitHub Pages. Formulae and evidence are published in `data/output.json`.

**Live dashboard:** https://monarchcastletech.github.io/climate-security-index/

## Run locally

```bash
python -m pip install -r requirements.txt
python pipeline/climate_security_index_pipeline.py
python -m http.server 8000
```

Open `http://localhost:8000`. Direct `file://` access cannot fetch `data/output.json` in modern browsers.

## Automation

GitHub Actions refreshes public data every six hours and deploys the static dashboard to GitHub Pages. AI briefs are optional: configure `OPENROUTER_API_KEY` as a repository Actions secret. Without it, core collection and dashboard deployment remain available.

## Data notice

Source availability varies. The dashboard identifies its generation time and operating mode in `data/output.json`. Treat indicators as decision-support signals, not verified ground truth.

## Brand

Part of Monarch Castle Technologies. See [BRAND.md](BRAND.md) for approved asset use.
