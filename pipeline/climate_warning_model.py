"""Explainable climate-security pressure model using public official data."""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone


WEIGHTS = {
    "agroclimate_stress": 0.35,
    "gdacs_impact_pressure": 0.25,
    "eonet_hazard_persistence": 0.20,
    "food_price_pressure": 0.20,
}
EONET_WEIGHTS = {"drought": 4.0, "floods": 2.5, "severeStorms": 2.0, "wildfires": 1.5, "landslides": 1.5, "dustHaze": 1.2}
GDACS_WEIGHTS = {"Green": 1.0, "Orange": 8.0, "Red": 20.0}


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        return None


def _robust_z(current, baseline):
    values = [float(value) for value in baseline if value is not None]
    if len(values) < 3:
        return 0.0
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    if mad < 1e-9:
        return (float(current) - median) / max(1.0, math.sqrt(abs(median) + 1))
    return (float(current) - median) / (1.4826 * mad)


def _eonet_component(payload, now):
    events = list(payload.get("events") or [])
    buckets = [0.0] * 4
    evidence = []
    for event in events:
        geometry = list(event.get("geometry") or [])
        observed = _parse(geometry[-1].get("date")) if geometry else None
        if not observed:
            continue
        index = (now - observed).days // 30
        if not 0 <= index < 4:
            continue
        categories = [row.get("id") for row in event.get("categories", [])]
        weight = sum(EONET_WEIGHTS.get(category, 0.8) for category in categories)
        if event.get("closed") is None:
            weight *= 1.25
        magnitude = geometry[-1].get("magnitudeValue") if geometry else None
        if isinstance(magnitude, (int, float)) and magnitude > 0:
            weight += min(4.0, math.log10(magnitude + 1) * 0.6)
        buckets[index] += weight
        if index == 0:
            evidence.append({"id": event.get("id"), "title": event.get("title"), "date": observed.isoformat(), "categories": categories, "weight": round(weight, 1), "url": (event.get("sources") or [{}])[0].get("url") or event.get("link")})
    anomaly = _robust_z(buckets[0], buckets[1:])
    score = 0.65 * _clamp(buckets[0] * 0.7, high=70) + 0.35 * _clamp(max(0, anomaly) * 15, high=30)
    return {"id": "eonet_hazard_persistence", "label": "NASA EONET hazard persistence", "available": bool(events) and not payload.get("compact"), "score": round(_clamp(score), 1), "current_30d_weight": round(buckets[0], 1), "baseline_median": round(statistics.median(buckets[1:]), 1), "anomaly_z": round(anomaly, 2), "events_considered": len(events), "evidence": sorted(evidence, key=lambda row: row["weight"], reverse=True)[:15], "retained": bool(payload.get("cached") or payload.get("retained"))}


def _gdacs_component(payload, now):
    features = list(payload.get("features") or [])
    buckets = [0.0] * 13
    evidence = []
    seen = set()
    for feature in features:
        props = feature.get("properties", {})
        event_key = f"{props.get('eventtype')}|{props.get('eventid')}"
        if event_key in seen:
            continue
        seen.add(event_key)
        observed = _parse(props.get("fromdate"))
        if not observed:
            continue
        index = (now - observed).days // 30
        if not 0 <= index < len(buckets):
            continue
        level = str(props.get("alertlevel") or "Green").title()
        weight = GDACS_WEIGHTS.get(level, 1.0)
        affected = props.get("affectedcountries") or []
        weight *= 1 + min(0.5, max(0, len(affected) - 1) * 0.12)
        buckets[index] += weight
        if index == 0:
            report = props.get("url", {}).get("report") if isinstance(props.get("url"), dict) else props.get("url")
            evidence.append({"id": event_key, "title": props.get("name") or props.get("description"), "date": observed.isoformat(), "alert_level": level, "event_type": props.get("eventtype"), "country": props.get("country"), "weight": round(weight, 1), "url": report})
    anomaly = _robust_z(buckets[0], buckets[1:])
    score = 0.7 * _clamp(buckets[0] * 1.8, high=75) + 0.3 * _clamp(max(0, anomaly) * 15, high=25)
    return {"id": "gdacs_impact_pressure", "label": "GDACS multi-hazard impact pressure", "available": bool(features) and not payload.get("compact"), "score": round(_clamp(score), 1), "current_30d_weight": round(buckets[0], 1), "baseline_months": 12, "baseline_median": round(statistics.median(buckets[1:]), 1), "anomaly_z": round(anomaly, 2), "events_considered": len(seen), "evidence": sorted(evidence, key=lambda row: row["weight"], reverse=True)[:15], "retained": bool(payload.get("cached") or payload.get("retained"))}


def _shift_year(value, years):
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _power_component(payload):
    results = []
    for point in payload.get("points", []):
        precip = {datetime.strptime(key, "%Y%m%d").replace(tzinfo=timezone.utc): float(value) for key, value in point.get("precipitation", {}).items() if float(value) > -900}
        temp = {datetime.strptime(key, "%Y%m%d").replace(tzinfo=timezone.utc): float(value) for key, value in point.get("temperature_max", {}).items() if float(value) > -900}
        common = sorted(set(precip) & set(temp))
        if len(common) < 365:
            continue
        end, start = common[-1], common[-1] - timedelta(days=29)
        current_days = [day for day in common if start <= day <= end]
        current_rain = sum(precip[day] for day in current_days)
        current_temp = statistics.mean(temp[day] for day in current_days)
        rain_baseline, temp_baseline = [], []
        for years in range(1, 6):
            prior_start, prior_end = _shift_year(start, years), _shift_year(end, years)
            days = [day for day in common if prior_start <= day <= prior_end]
            if len(days) >= 25:
                rain_baseline.append(sum(precip[day] for day in days))
                temp_baseline.append(statistics.mean(temp[day] for day in days))
        if not rain_baseline:
            continue
        base_rain, base_temp = statistics.mean(rain_baseline), statistics.mean(temp_baseline)
        rain_ratio = current_rain / base_rain if base_rain > 1 else 1.0
        heat_anomaly = current_temp - base_temp
        score = _clamp(max(0, 1 - rain_ratio) * 70 + max(0, heat_anomaly) * 10)
        results.append({"name": point.get("name"), "lat": point.get("lat"), "lon": point.get("lon"), "score": round(score, 1), "rain_30d_mm": round(current_rain, 1), "rain_ratio": round(rain_ratio, 2), "max_temp_c": round(current_temp, 1), "heat_anomaly_c": round(heat_anomaly, 1), "observed_through": end.date().isoformat()})
    ranked = sorted(results, key=lambda row: row["score"], reverse=True)
    top = ranked[:3]
    score = statistics.mean(row["score"] for row in top) if top else 0.0
    return {"id": "agroclimate_stress", "label": "NASA POWER agroclimate stress", "available": len(results) >= 6, "score": round(score, 1), "sentinels_available": len(results), "sentinels": ranked, "evidence": top, "retained": bool(payload.get("cached") or payload.get("retained"))}


def _fao_component(payload):
    rows = sorted(payload.get("rows") or [], key=lambda row: row.get("Date", ""))
    if len(rows) < 24:
        return {"id": "food_price_pressure", "label": "FAO food-price pressure", "available": False, "score": 0.0, "retained": False}
    metrics = []
    for field in ("Food Price Index", "Cereals", "Oils"):
        values = [float(row[field]) for row in rows]
        changes = [((values[index] / values[index - 3]) - 1) * 100 for index in range(3, len(values)) if values[index - 3]]
        latest = changes[-1]
        anomaly = _robust_z(latest, changes[-61:-1])
        metrics.append({"field": field, "latest": values[-1], "three_month_change_pct": round(latest, 2), "year_change_pct": round(((values[-1] / values[-13]) - 1) * 100, 2) if len(values) >= 13 else None, "anomaly_z": round(anomaly, 2), "score": round(_clamp(max(0, latest) * 6 + max(0, anomaly) * 18), 1)})
    ranked = sorted(metrics, key=lambda row: row["score"], reverse=True)
    score = 0.6 * ranked[0]["score"] + 0.4 * ranked[1]["score"]
    return {"id": "food_price_pressure", "label": "FAO food-price pressure", "available": True, "score": round(score, 1), "latest_month": rows[-1]["Date"], "metrics": metrics, "evidence": ranked, "retained": bool(payload.get("cached") or payload.get("retained"))}


def _level(score):
    return "SEVERE" if score >= 75 else "ELEVATED" if score >= 55 else "WATCH" if score >= 35 else "BASELINE"


def build_climate_warning(power, gdacs, eonet, fao, previous_warning=None, now=None):
    now = now or datetime.now(timezone.utc)
    components = [_power_component(power), _gdacs_component(gdacs, now), _eonet_component(eonet, now), _fao_component(fao)]
    previous_issued = _parse((previous_warning or {}).get("issued_at"))
    age = now - previous_issued if previous_issued else timedelta(days=999)
    if timedelta(0) <= age <= timedelta(hours=72):
        prior_by_id = {row.get("id"): row for row in (previous_warning or {}).get("components", [])}
        for index, row in enumerate(components):
            prior = prior_by_id.get(row["id"])
            if not row.get("available") and prior and prior.get("available"):
                components[index] = dict(prior)
                components[index]["retained"] = True
    available = [row for row in components if row["available"]]
    denominator = sum(WEIGHTS[row["id"]] for row in available)
    base = sum(row["score"] * WEIGHTS[row["id"]] for row in available) / denominator if denominator else 0.0
    climate_elevated = [row["id"] for row in components[:3] if row["available"] and row["score"] >= 35]
    food_elevated = components[3]["available"] and components[3]["score"] >= 35
    bonus = 5.0 if climate_elevated and food_elevated else 0.0
    score = _clamp(base + bonus)
    coverage = sum(WEIGHTS[row["id"]] * (0.85 if row.get("retained") else 1.0) for row in available)
    confidence_score = 100 * coverage
    confidence = "HIGH" if confidence_score >= 80 else "MEDIUM" if confidence_score >= 55 else "LOW"
    alerts = [{"id": row["id"], "title": row["label"], "score": row["score"], "level": _level(row["score"])} for row in available if row["score"] >= 35]
    history = list((previous_warning or {}).get("history") or [])[-179:]
    if history:
        last = _parse(history[-1].get("timestamp"))
        if last and timedelta(0) <= now - last < timedelta(hours=1):
            history.pop()
    history.append({"timestamp": now.isoformat(), "score": round(score, 1), "level": _level(score), "components": {row["id"]: row["score"] for row in components}})
    return {"issued_at": now.isoformat(), "horizon": "0-90 days", "classification": "climate-security-pressure-not-conflict-or-famine-probability", "score": round(score, 1), "level": _level(score), "confidence": confidence, "confidence_score": round(confidence_score, 1), "components": components, "concurrence": {"active": bool(bonus), "climate_components": climate_elevated, "food_component_elevated": food_elevated, "score_bonus": bonus}, "alerts": sorted(alerts, key=lambda row: row["score"], reverse=True), "history": history, "data_health": {"available_components": len(available), "retained_components": [row["id"] for row in components if row.get("retained")], "power_sentinels": components[0].get("sentinels_available", 0), "gdacs_events": components[1].get("events_considered", 0), "eonet_events": components[2].get("events_considered", 0), "fao_latest_month": components[3].get("latest_month")}, "method": {"name": "Climate-security precursor concurrence model v1", "weights": WEIGHTS, "aggregation": "availability-renormalized weighted mean; five-point bonus requires climate and independent food-price elevation", "warning": "Public climate-security pressure only; not a conflict, famine, displacement, or casualty forecast."}, "sources": [{"name": "NASA POWER Daily API", "url": "https://power.larc.nasa.gov/docs/services/api/temporal/daily/"}, {"name": "NASA EONET v3", "url": "https://eonet.gsfc.nasa.gov/docs/v3"}, {"name": "GDACS", "url": "https://www.gdacs.org/"}, {"name": "FAO Food Price Index", "url": "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"}]}
