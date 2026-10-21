import pytest

import app as app_module


@pytest.fixture()
def client():
    previous_testing = app_module.app.config["TESTING"]
    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.test_client() as test_client:
            yield test_client
    finally:
        app_module.app.config["TESTING"] = previous_testing


def test_health_returns_ok_and_runtime_versions(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["application"] == "Benja Karaoke"
    assert data["api_version"] == "1.0.0"
    assert data["python_version"]
    assert data["flask_version"]


def test_status_reports_processing_not_yet_integrated(client):
    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.get_json()
    assert data["backend"] == "ready"
    assert data["audio_processing"]["integrated"] is False
    assert data["transcription"]["integrated"] is False
    assert data["storage"]["configured"] is False


def test_config_distinguishes_available_and_pending_features(client):
    response = client.get("/api/config")

    assert response.status_code == 200
    features = response.get_json()["features"]
    assert features["youtube_search_ui"] is True
    assert features["local_audio_ui"] is True
    assert features["manual_lyrics_timing"] is True
    assert features["demucs_api_processing"] is False
    assert features["automatic_word_alignment"] is False
    assert features["persistent_library"] is False


@pytest.mark.parametrize("path", ["/", "/karaoke.html"])
def test_frontend_pages_are_served(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.content_type


def test_unknown_route_returns_json_404(client):
    response = client.get("/ruta-que-no-existe")

    assert response.status_code == 404
    data = response.get_json()
    assert data["error"] == "not_found"


def test_payload_too_large_handler_returns_json_413():
    with app_module.app.app_context():
        response, status_code = app_module.file_too_large(None)

    assert status_code == 413
    data = response.get_json()
    assert data["error"] == "payload_too_large"

@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (
            "https://alvaroberrio23242-eng.github.io",
            "https://alvaroberrio23242-eng.github.io",
        ),
        ("https://evil.example", None),
    ],
)
def test_api_cors_restricts_origins(client, monkeypatch, origin, expected):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    response = client.get("/api/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == expected
    assert "Origin" in response.headers.get("Vary", "")


def test_api_cors_accepts_configured_origin(client, monkeypatch):
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://preview.example, http://localhost:5500",
    )

    response = client.get(
        "/api/config",
        headers={"Origin": "http://localhost:5500"},
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5500"


def test_frontend_checks_api_without_replacing_player_status(client):
    response = client.get("/karaoke.html")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="playerStatus"' in html
    assert 'id="apiStatus"' in html
    assert "/api/health" in html
    assert "/api/config" in html
    assert "karaoke local sigue funcionando" in html
