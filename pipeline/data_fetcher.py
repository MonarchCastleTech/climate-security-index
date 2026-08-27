# -*- coding: utf-8 -*-
"""Shared data fetchers for MCT Intelligence projects."""
import os
import json
import csv
import io
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

CACHE_MAX_AGE = timedelta(hours=72)


def _cache_path(name):
    root = Path(os.path.expanduser("~")) / ".cache" / "climate-security-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{name}.json"


def _read_recent_cache(name):
    try:
        payload = json.loads(_cache_path(name).read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(payload.get("fetched_at", "")).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if timedelta(0) <= datetime.now(timezone.utc) - fetched <= CACHE_MAX_AGE:
            return payload.get("data")
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _write_cache(name, data):
    _cache_path(name).write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}), encoding="utf-8")


def _cached_request(name, fetcher):
    try:
        data = fetcher()
        if not data:
            raise ValueError(f"{name} returned no data")
        data["cached"] = False
        _write_cache(name, data)
        return data
    except Exception as exc:
        print(f"[{name.upper()}] Error: {exc}")
        cached = _read_recent_cache(name)
        if cached:
            cached["cached"] = True
            return cached
        return {}


def fetch_nasa_eonet():
    """Fetch 120 days of curated NASA EONET natural-event metadata."""
    def fetch():
        response = requests.get("https://eonet.gsfc.nasa.gov/api/v3/events", params={"status": "all", "days": 120, "limit": 1000}, timeout=40, headers={"User-Agent": "climate-security-index/2.0"})
        response.raise_for_status()
        events = response.json().get("events", [])
        return {"events": events, "count": len(events)}
    return _cached_request("nasa-eonet", fetch)


def fetch_gdacs_events():
    """Fetch current and archive GDACS GeoJSON alerts."""
    def fetch():
        base = "https://www.gdacs.org/contentdata/xml/"
        with ThreadPoolExecutor(max_workers=2) as executor:
            payloads = list(executor.map(lambda name: requests.get(base + name, timeout=45, headers={"User-Agent": "climate-security-index/2.0"}), ["gdacs_app_feed.json", "gdacs_archive.geojson"]))
        for response in payloads:
            response.raise_for_status()
        rows = [feature for response in payloads for feature in response.json().get("features", [])]
        deduplicated = {}
        for feature in rows:
            props = feature.get("properties", {})
            key = f"{props.get('eventtype')}|{props.get('eventid')}|{props.get('episodeid')}"
            deduplicated[key] = feature
        return {"features": list(deduplicated.values()), "count": len(deduplicated)}
    return _cached_request("gdacs", fetch)


def fetch_fao_food_prices():
    """Fetch official monthly nominal FAO food-price indices."""
    def fetch():
        url = "https://www.fao.org/media/docs/worldfoodsituationlibraries/default-document-library/food_price_indices_data.csv?download=true"
        response = requests.get(url, timeout=45, headers={"User-Agent": "climate-security-index/2.0"})
        response.raise_for_status()
        lines = response.content.decode("latin-1").splitlines()
        reader = csv.DictReader(io.StringIO("\n".join(lines[2:])))
        rows = []
        for row in reader:
            if not row.get("Date") or not row.get("Food Price Index"):
                continue
            try:
                rows.append({key: (row[key] if key == "Date" else float(row[key])) for key in ("Date", "Food Price Index", "Meat", "Dairy", "Cereals", "Oils", "Sugar")})
            except (ValueError, TypeError):
                continue
        return {"rows": rows, "source_url": url}
    return _cached_request("fao-food-prices", fetch)


CLIMATE_SENTINELS = {
    "Somalia pastoral belt": (5.0, 45.3), "Ethiopia lowlands": (8.0, 40.0),
    "Sahel west": (14.5, -1.5), "Lake Chad basin": (12.5, 14.5),
    "Sudan grain belt": (13.5, 33.5), "Afghanistan drylands": (34.0, 65.0),
    "Pakistan Indus": (29.0, 70.5), "Central America dry corridor": (14.0, -88.5),
}


def _fetch_power_point(item):
    name, (lat, lon) = item
    now = datetime.now(timezone.utc)
    response = requests.get("https://power.larc.nasa.gov/api/temporal/daily/point", params={
        "parameters": "PRECTOTCORR,T2M_MAX", "community": "AG", "longitude": lon, "latitude": lat,
        "start": f"{now.year - 5}0101", "end": now.strftime("%Y%m%d"), "format": "JSON", "time-standard": "UTC",
    }, timeout=70, headers={"User-Agent": "climate-security-index/2.0"})
    response.raise_for_status()
    parameters = response.json().get("properties", {}).get("parameter", {})
    return {"name": name, "lat": lat, "lon": lon, "precipitation": parameters.get("PRECTOTCORR", {}), "temperature_max": parameters.get("T2M_MAX", {})}


def fetch_nasa_power_sentinels():
    """Fetch five-year daily agroclimate history for fixed exposed regions."""
    def fetch():
        with ThreadPoolExecutor(max_workers=4) as executor:
            points = list(executor.map(_fetch_power_point, CLIMATE_SENTINELS.items()))
        if len(points) < 6:
            raise ValueError("insufficient NASA POWER sentinel coverage")
        return {"points": points, "count": len(points)}
    return _cached_request("nasa-power", fetch)

def fetch_nasa_firms(api_key=None, region="world", days=1):
    """Fetch NASA FIRMS fire/thermal anomaly data."""
    key = api_key or os.environ.get("NASA_FIRMS_API_KEY", "")
    if not key:
        return []
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NPP/{region}/{days}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            lines = r.text.strip().split("\n")
            if len(lines) < 2:
                return []
            headers = lines[0].split(",")
            return [
                dict(zip(headers, line.split(",")))
                for line in lines[1:]
                if line.strip()
            ][:500]
        return []
    except Exception as e:
        print(f"[NASA-FIRMS] Error: {e}")
        return []

def fetch_cisa_kev():
    """Fetch CISA Known Exploited Vulnerabilities catalog."""
    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "MCT-Intel/1.0"})
        if r.status_code == 200:
            data = r.json()
            vulns = data.get("vulnerabilities", [])
            return [
                {
                    "cveID": v.get("cveID", ""),
                    "vendorProject": v.get("vendorProject", ""),
                    "product": v.get("product", ""),
                    "vulnerabilityName": v.get("vulnerabilityName", ""),
                    "dateAdded": v.get("dateAdded", ""),
                    "shortDescription": v.get("shortDescription", ""),
                    "dueDate": v.get("requiredAction", ""),
                    "source": "CISA-KEV"
                }
                for v in vulns
            ]
        return []
    except Exception as e:
        print(f"[CISA-KEV] Error: {e}")
        return []

def fetch_acled(*_args, **_kwargs):
    """Disabled until a licensed ACLED key is explicitly configured."""
    return []

def fetch_opensanctions(*_args, **_kwargs):
    """Disabled: current OpenSanctions API requires authenticated access."""
    return {}

def fetch_census_country():
    """Fetch World Bank country indicators (GDP, population)."""
    url = "https://api.worldbank.org/v2/country?format=json&per_page=300"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if len(data) > 1:
                return [
                    {
                        "id": c.get("id", ""),
                        "name": c.get("name", ""),
                        "region": c.get("region", {}).get("value", ""),
                        "capitalCity": c.get("capitalCity", ""),
                        "longitude": c.get("longitude", ""),
                        "latitude": c.get("latitude", ""),
                    }
                    for c in data[1]
                ]
        return []
    except Exception as e:
        print(f"[WorldBank] Error: {e}")
        return []

def fetch_coingecko(coin="bitcoin"):
    """Fetch crypto market data from CoinGecko (free, no key)."""
    url = f"https://api.coingecko.com/api/v3/coins/{coin}"
    try:
        r = requests.get(url, params={"localization": "false", "tickers": "false"}, timeout=30)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        print(f"[CoinGecko] Error: {e}")
        return {}

def fetch_exchange_rates(base="USD"):
    """Fetch free exchange rates (no key needed)."""
    url = f"https://api.exchangerate-api.com/v4/latest/{base}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            rates = data.get("rates", {})
            # Return top 20 rates as list of dicts
            return [{"currency": k, "rate": v} for k, v in list(rates.items())[:20]]
        return []
    except Exception as e:
        print(f"[ExchangeRate] Error: {e}")
        return []

def fetch_weather(lat, lon):
    """Fetch free weather from Open-Meteo (no key)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": lat, "longitude": lon, "current": "temperature_2m,wind_speed_10m"}
    try:
        r = requests.get(url, params=params, timeout=30)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        print(f"[OpenMeteo] Error: {e}")
        return {}

def fetch_covid_global():
    """Fetch COVID-19 summary data."""
    url = "https://disease.sh/v3/covid-19/countries"
    try:
        r = requests.get(url, timeout=30)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[COVID] Error: {e}")
        return []

def fetch_earthquakes(hours=24):
    """Fetch recent earthquake data from USGS."""
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            features = data.get("features", [])
            return [
                {
                    "place": f.get("properties", {}).get("place", ""),
                    "mag": f.get("properties", {}).get("mag", 0),
                    "time": f.get("properties", {}).get("time", ""),
                    "lon": f.get("geometry", {}).get("coordinates", [0, 0, 0])[0],
                    "lat": f.get("geometry", {}).get("coordinates", [0, 0, 0])[1],
                    "depth": f.get("geometry", {}).get("coordinates", [0, 0, 0])[2],
                    "source": "USGS"
                }
                for f in features[:200]
            ]
        return []
    except Exception as e:
        print(f"[USGS-Quake] Error: {e}")
        return []

def safe_fetch(fetcher, *args, **kwargs):
    """Wrapper that catches all exceptions and returns empty data."""
    try:
        return fetcher(*args, **kwargs)
    except Exception as e:
        print(f"[SafeFetch] {fetcher.__name__} failed: {e}")
        return {} if not isinstance(args, list) else []

def fetch_google_news_rss(query, max_results=50):
    """Fetch news headlines from Google News RSS."""
    import re
    import urllib.parse
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone

    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "MCT-Intel/1.0"})
        if r.status_code != 200:
            print(f"[GoogleNews] HTTP {r.status_code}")
            return []
        root = ET.fromstring(r.content)
        items = root.findall(".//item")[:max_results]
        articles = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            source_el = item.find("source")
            source_name = source_el.text.strip() if source_el is not None and source_el.text else ""
            if source_name and title.endswith(" - " + source_name):
                title = title[: -(len(source_name) + 3)].strip()
            seendate = ""
            for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
                try:
                    dt = datetime.strptime(pub.replace("GMT", "UTC"), fmt)
                    seendate = dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    break
                except ValueError:
                    continue
            if not seendate:
                seendate = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            domain = re.sub(r"[^a-z0-9.-]", "", (source_name or "news.google.com").lower()) or "news.google.com"
            articles.append({
                "title": title,
                "url": link,
                "domain": domain,
                "language": "",
                "tone": 0,
                "seendate": seendate,
                "source": "GoogleNews",
            })
        return articles
    except Exception as e:
        print(f"[GoogleNews] Error: {e}")
        return []
