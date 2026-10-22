from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ucs_app.controlled import create_controlled_app


def test_only_configured_demo_images_are_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "before.jpg").write_bytes(b"demo-photo")
    (tmp_path / "private.txt").write_text("private", encoding="utf-8")
    with TestClient(create_controlled_app()) as client:
        response = client.get("/demo-images/before.jpg")
        assert response.status_code == 200
        assert response.content == b"demo-photo"
        assert response.headers["content-type"] == "image/jpeg"
        assert client.get("/demo-images/after.jpg").status_code == 404
        assert client.get("/demo-images/private.txt").status_code == 404
