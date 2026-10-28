"""Autonomous public-data pipeline for climate-security early warning."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from climate_warning_model import build_climate_warning
from data_fetcher import fetch_fao_food_prices, fetch_gdacs_events, fetch_nasa_eonet, fetch_nasa_power_sentinels


def load_config():
    with open(os.path.join(os.path.dirname(__file__), "config.yaml"), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_previous():
    try:
        with open("data/output.json", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_all(previous):
    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = {"nasa_power": executor.submit(fetch_nasa_power_sentinels), "gdacs": executor.submit(fetch_gdacs_events), "nasa_eonet": executor.submit(fetch_nasa_eonet), "fao_food_prices": executor.submit(fetch_fao_food_prices)}
    live, notes = {key: job.result() for key, job in jobs.items()}, []
    prior = previous.get("live_data") or {}
    for key in jobs:
        if not live.get(key) and prior.get(key):
            source = prior[key]
            timestamp = source.get("source_collected_at")
            if not timestamp and not source.get("retained") and not source.get("cached"):
                timestamp = (previous.get("meta") or {}).get("generated")
            try:
                collected = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if collected.tzinfo is None: raise ValueError("Missing timezone")
                age = datetime.now(timezone.utc) - collected
            except (TypeError, ValueError):
                age = timedelta(days=999)
            if timedelta(0) <= age <= timedelta(hours=72):
                live[key] = dict(source); live[key]["retained"] = True; live[key]["source_collected_at"] = collected.isoformat()
                notes.append(f"{key} unavailable; retained last accepted snapshot.")
    return {key: value for key, value in live.items() if value}, notes


def build_stats(warning):
    health = warning.get("data_health") or {}
    return [
        {"label": "Climate-Security Pressure", "value": f"{warning.get('score', 0):.1f}/100", "delta": warning.get("level", "UNAVAILABLE")},
        {"label": "Agroclimate Sentinels", "value": str(health.get("power_sentinels", 0)), "delta": "five-year daily baselines"},
        {"label": "Hazards Screened", "value": str(health.get("gdacs_events", 0) + health.get("eonet_events", 0)), "delta": "NASA + GDACS"},
        {"label": "Model Coverage", "value": f"{health.get('available_components', 0)}/4", "delta": warning.get("confidence", "LOW") + " confidence"},
    ]


def main():
    config, previous = load_config(), load_previous()
    live, notes = fetch_all(previous)
    if not live.get("nasa_power") and not live.get("nasa_eonet") and not live.get("gdacs"):
        print("No physical climate source available; preserving last-good output.")
        return False
    warning = build_climate_warning(live.get("nasa_power") or {}, live.get("gdacs") or {}, live.get("nasa_eonet") or {}, live.get("fao_food_prices") or {}, previous_warning=previous.get("early_warning"))
    retained = warning.get("data_health", {}).get("retained_components", [])
    evidence = []
    for component in warning.get("components", []):
        evidence.extend(component.get("evidence") or [])
    public_live = dict(live)
    if public_live.get("nasa_power"):
        source = public_live["nasa_power"]
        public_live["nasa_power"] = {"count": source.get("count", 0), "cached": bool(source.get("cached")), "retained": bool(source.get("retained")), "source_collected_at": source.get("source_collected_at"), "sentinels": warning["components"][0].get("sentinels", [])}
    if public_live.get("gdacs"):
        source = public_live["gdacs"]
        public_live["gdacs"] = {"count": source.get("count", 0), "cached": bool(source.get("cached")), "retained": bool(source.get("retained")), "source_collected_at": source.get("source_collected_at"), "compact": True, "events": warning["components"][1].get("evidence", [])}
    if public_live.get("nasa_eonet"):
        source = public_live["nasa_eonet"]
        public_live["nasa_eonet"] = {"count": source.get("count", 0), "cached": bool(source.get("cached")), "retained": bool(source.get("retained")), "source_collected_at": source.get("source_collected_at"), "compact": True, "events": warning["components"][2].get("evidence", [])}
    output = {"meta": {"project": config["project"]["id"], "generated": datetime.now(timezone.utc).isoformat(), "mode": "partial" if retained or warning["data_health"]["available_components"] < 4 else "live", "sources": [row["name"] for row in warning["sources"]], "source_notes": notes, "version": "2.0.0"}, "early_warning": warning, "stats": build_stats(warning), "live_data": public_live, "events": evidence[:30]}
    os.makedirs("data", exist_ok=True)
    with open("data/output.json", "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
    print(f"Done. mode={output['meta']['mode']} score={warning['score']} level={warning['level']} coverage={warning['data_health']['available_components']}/4")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if main() else 2)
