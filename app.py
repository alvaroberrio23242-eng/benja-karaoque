from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
PYTHON_DIR = BASE_DIR / ".venv" / "Scripts"
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1 GiB; limite para futuras cargas.

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

DEFAULT_ALLOWED_ORIGINS = {
    "https://alvaroberrio23242-eng.github.io",
}


def allowed_origins() -> set[str]:
    configured = os.getenv("CORS_ALLOWED_ORIGINS")
    if configured is None:
        return DEFAULT_ALLOWED_ORIGINS.copy()
    return {
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    }


@app.after_request
def apply_api_cors(response):
    # La API es de lectura desde el frontend; no habilitar credenciales ni '*'.
    if request.path.startswith("/api/"):
        response.vary.add("Origin")
        origin = request.headers.get("Origin")
        if origin and origin in allowed_origins():
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response



def package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def demucs_executable() -> str | None:
    # Desarrollo Windows: localizar el ejecutable del entorno virtual.
    local_executable = PYTHON_DIR / "demucs-infer.exe"
    if local_executable.is_file():
        return str(local_executable)

    # Despliegues Linux: buscarlo en PATH.
    return shutil.which("demucs-infer")


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/karaoke.html")
def karaoke_page():
    return send_from_directory(BASE_DIR, "karaoke.html")


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "application": "Benja Karaoke",
        "api_version": "1.0.0",
        "python_version": sys.version.split()[0],
        "flask_version": package_version("flask"),
    }), 200


@app.get("/api/status")
def status():
    demucs_path = demucs_executable()

    return jsonify({
        "application": "Benja Karaoke",
        "backend": "ready",
        "audio_processing": {
            "demucs_executable_found": demucs_path is not None,
            "demucs_path": demucs_path,
            "integrated": False,
            "message": (
                "Demucs esta disponible; la ejecucion desde la API "
                "todavia no esta implementada."
                if demucs_path
                else "Demucs no esta instalado o no se encuentra en PATH."
            ),
        },
        "transcription": {
            "integrated": False,
            "message": "Transcripcion y alineacion por palabra pendientes."
        },
        "storage": {
            "configured": False,
            "message": "No hay biblioteca persistente configurada."
        },
    }), 200


@app.get("/api/config")
def config():
    return jsonify({
        "application": "Benja Karaoke",
        "features": {
            "youtube_search_ui": True,
            "local_audio_ui": True,
            "manual_lyrics_timing": True,
            "demucs_api_processing": False,
            "automatic_word_alignment": False,
            "automatic_lyrics_lookup": True,
            "persistent_library": False,
        },
        "environment": (
            "render" if os.getenv("RENDER") else "local_or_other"
        ),
    }), 200



def lrclib_search(track: str, artist: str = "") -> dict:
    """Busca letras en LRCLIB sin descargar audio ni depender de APIs de pago."""
    params = {"q": f"{artist} {track}".strip()}
    url = "https://lrclib.net/api/search?" + urlencode(params)
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BenjaKaraoke/1.0 (lyrics lookup)",
        },
        method="GET",
    )
    with urlopen(req, timeout=8) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("La respuesta del proveedor es demasiado grande.")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("El proveedor devolvió un formato inesperado.")
    candidates = []
    for item in data[:30]:
        if not isinstance(item, dict):
            continue
        synced = item.get("syncedLyrics")
        plain = item.get("plainLyrics")
        if not isinstance(synced, str):
            synced = ""
        if not isinstance(plain, str):
            plain = ""
        lyrics = synced or plain
        if not lyrics.strip():
            continue
        candidates.append({
            "track": str(item.get("trackName") or "")[:200],
            "artist": str(item.get("artistName") or "")[:200],
            "album": str(item.get("albumName") or "")[:200],
            "duration": item.get("duration"),
            "synced": bool(synced.strip()),
            "lyrics": lyrics[:100_000],
            "source": "LRCLIB",
        })
    candidates.sort(key=lambda item: (item["synced"], bool(item["artist"])), reverse=True)
    return {"results": candidates[:5]}


@app.get("/api/lyrics/search")
def lyrics_search():
    track = request.args.get("track", "").strip()
    artist = request.args.get("artist", "").strip()
    if not track:
        return jsonify({
            "error": "missing_track",
            "message": "Escribe el nombre de la canción para buscar la letra."
        }), 400
    if len(track) > 200 or len(artist) > 200:
        return jsonify({
            "error": "invalid_query",
            "message": "El título y el artista deben tener como máximo 200 caracteres."
        }), 400
    try:
        result = lrclib_search(track, artist)
    except HTTPError as exc:
        if exc.code == 404:
            return jsonify({"results": [], "source": "LRCLIB"}), 200
        return jsonify({
            "error": "lyrics_provider_error",
            "message": "El proveedor de letras no está disponible ahora."
        }), 502
    except (URLError, TimeoutError, OSError):
        return jsonify({
            "error": "lyrics_provider_unavailable",
            "message": "No se pudo conectar con el proveedor de letras."
        }), 502
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({
            "error": "lyrics_provider_invalid_response",
            "message": "El proveedor devolvió una respuesta no válida."
        }), 502
    result["source"] = "LRCLIB"
    return jsonify(result), 200

@app.errorhandler(404)
def not_found(_error):
    return jsonify({
        "error": "not_found",
        "message": "La ruta solicitada no existe."
    }), 404


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({
        "error": "payload_too_large",
        "message": "La solicitud supera el limite configurado."
    }), 413


@app.errorhandler(500)
def internal_error(_error):
    return jsonify({
        "error": "internal_server_error",
        "message": "Ocurrio un error interno."
    }), 500


if __name__ == "__main__":
    # Solo para desarrollo local. En Render se utiliza Gunicorn.
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
