from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_public_product_contract():
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    joined = page + script
    for term in ("Early Warning", "Methodology", "NASA POWER", "NASA EONET", "GDACS", "FAO Food Price Index"):
        assert term in joined
    for fake in ("Copernicus Sentinel", "CHIRPS", "FEWS NET", "SPEI", "ACLED"):
        assert fake not in joined
    assert "python -m pytest -q" in workflow
    assert "actions/cache" in workflow
    assert "set -euo pipefail" in workflow
    assert "attempt in 1 2 3 4 5" in workflow
