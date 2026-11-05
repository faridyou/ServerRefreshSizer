from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from refreshsizer.ui.app import create_app


def test_ui_flow(fixtures_dir: Path) -> None:
    c = TestClient(create_app())
    assert c.get("/").status_code == 200
    with open(fixtures_dir / "messy_semicolon.csv", "rb") as fh:
        r = c.post("/upload", files=[("files", ("messy_semicolon.csv", fh.read(), "text/csv"))])
    assert r.status_code == 200 and "Column mappings" in r.text
    sid = re.search(r"/size/([0-9a-f]{12})", r.text).group(1)  # type: ignore[union-attr]
    form = {"policy": "horizon_years: 4\n", "map::messy_semicolon.csv|messy_semicolon|Cluster": ""}
    r2 = c.post(f"/size/{sid}", data=form)
    assert r2.status_code == 200 and "Downloads" in r2.text and "1 mapping edit" in r2.text
    link = re.search(r"/download/[0-9a-f]{12}/refreshsizer_report.html", r2.text).group(0)  # type: ignore[union-attr]
    assert c.get(link).status_code == 200
    assert c.get(f"/download/{sid}/..%2Fproject.json").status_code == 404
    assert c.get("/download/zzz/x").status_code == 404
