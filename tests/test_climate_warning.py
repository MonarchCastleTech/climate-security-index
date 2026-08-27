from datetime import datetime, timedelta, timezone

import pipeline.climate_warning_model as model


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def component(identifier, score, available=True):
    return {"id": identifier, "label": identifier, "score": score, "available": available, "retained": False}


def patch_components(monkeypatch, scores=(50, 30, 20, 40)):
    monkeypatch.setattr(model, "_power_component", lambda _: component("agroclimate_stress", scores[0]))
    monkeypatch.setattr(model, "_gdacs_component", lambda _a, _b: component("gdacs_impact_pressure", scores[1]))
    monkeypatch.setattr(model, "_eonet_component", lambda _a, _b: component("eonet_hazard_persistence", scores[2]))
    monkeypatch.setattr(model, "_fao_component", lambda _: component("food_price_pressure", scores[3]))


def test_contract_and_weights(monkeypatch):
    patch_components(monkeypatch)
    warning = model.build_climate_warning({}, {}, {}, {}, now=NOW)
    assert warning["classification"] == "climate-security-pressure-not-conflict-or-famine-probability"
    assert warning["horizon"] == "0-90 days"
    assert model.WEIGHTS == {"agroclimate_stress": .35, "gdacs_impact_pressure": .25, "eonet_hazard_persistence": .20, "food_price_pressure": .20}


def test_concurrence_requires_climate_and_food(monkeypatch):
    patch_components(monkeypatch, (60, 10, 10, 55))
    warning = model.build_climate_warning({}, {}, {}, {}, now=NOW)
    assert warning["concurrence"]["active"] is True
    assert warning["concurrence"]["score_bonus"] == 5


def test_no_concurrence_from_correlated_hazards_only(monkeypatch):
    patch_components(monkeypatch, (60, 70, 70, 10))
    warning = model.build_climate_warning({}, {}, {}, {}, now=NOW)
    assert warning["concurrence"]["active"] is False


def test_fao_component_uses_positive_three_month_pressure():
    rows = []
    for index in range(72):
        year, month = 2020 + index // 12, index % 12 + 1
        base = 90 + index * .2
        if index >= 69:
            base += (index - 68) * 8
        rows.append({"Date": f"{year}-{month:02d}", "Food Price Index": base, "Meat": base, "Dairy": base, "Cereals": base * 1.05, "Oils": base * 1.1, "Sugar": base})
    result = model._fao_component({"rows": rows})
    assert result["available"] is True
    assert result["score"] >= 35


def test_history_bounded_and_same_hour_replaced(monkeypatch):
    patch_components(monkeypatch)
    history = [{"timestamp": (NOW - timedelta(hours=200-index)).isoformat(), "score": index} for index in range(200)]
    history.append({"timestamp": (NOW - timedelta(minutes=10)).isoformat(), "score": 99})
    warning = model.build_climate_warning({}, {}, {}, {}, previous_warning={"history": history}, now=NOW)
    assert len(warning["history"]) <= 180
    assert warning["history"][-1]["timestamp"] == NOW.isoformat()


def test_recent_component_is_retained_for_72_hours(monkeypatch):
    patch_components(monkeypatch)
    monkeypatch.setattr(model, "_power_component", lambda _: component("agroclimate_stress", 0, False))
    previous = {"issued_at": (NOW - timedelta(hours=71)).isoformat(), "components": [component("agroclimate_stress", 62)]}
    warning = model.build_climate_warning({}, {}, {}, {}, previous_warning=previous, now=NOW)
    retained = next(row for row in warning["components"] if row["id"] == "agroclimate_stress")
    assert retained["available"] is True
    assert retained["retained"] is True
    assert retained["score"] == 62
