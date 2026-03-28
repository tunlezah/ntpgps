"""Flask web application with WebSocket support.

Serves the single-page dashboard and provides REST API + WebSocket endpoints.
"""

import json
import logging
import os
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from ntpgps import __version__

logger = logging.getLogger(__name__)

# Module-level reference to the server instance (set by main.py)
_server = None


def set_server(server) -> None:
    global _server
    _server = server


def create_app() -> Flask:
    """Create and configure Flask application."""
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )
    app.config["SECRET_KEY"] = os.urandom(24).hex()

    sock = Sock(app)

    # --- Pages ---

    @app.route("/")
    def index():
        return render_template("index.html", version=__version__)

    # --- REST API ---

    @app.route("/api/status")
    def api_status():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        return jsonify(_server.get_full_status())

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        return jsonify(_server.config.data)

    @app.route("/api/config", methods=["POST"])
    def api_config_set():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        try:
            for key, value in data.items():
                _server.config.set(key, value)
            _server.config.save()
            return jsonify({"status": "ok"})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/source/mode", methods=["POST"])
    def api_set_source_mode():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        data = request.get_json()
        mode = data.get("mode", "") if data else ""
        try:
            _server.source_engine.set_mode(mode)
            return jsonify({"status": "ok", "mode": mode})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/chrony/restart", methods=["POST"])
    def api_restart_chrony():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        success, message = _server.chrony.restart_service()
        status_code = 200 if success else 500
        return jsonify({"success": success, "message": message}), status_code

    @app.route("/api/chrony/sources")
    def api_chrony_sources():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        _server.chrony.poll()
        return jsonify({
            "sources": _server.chrony.get_sources(),
            "tracking": _server.chrony.get_tracking(),
        })

    @app.route("/api/alerts")
    def api_alerts():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        since = request.args.get("since", 0, type=float)
        return jsonify({"alerts": _server.source_engine.get_alerts(since)})

    @app.route("/api/drift/history")
    def api_drift_history():
        if not _server:
            return jsonify({"error": "Server not initialized"}), 503
        count = request.args.get("count", 300, type=int)
        count = min(count, 86400)
        return jsonify({
            "samples": _server.source_engine.drift_tracker.get_recent_samples(count),
            "statistics": _server.source_engine.drift_tracker.get_statistics(),
        })

    @app.route("/api/health")
    def api_health():
        return jsonify({
            "status": "ok",
            "version": __version__,
            "uptime": time.time(),
        })

    # --- WebSocket ---

    @sock.route("/ws")
    def websocket(ws):
        if not _server:
            ws.close()
            return
        _server.register_ws_client(ws)
        try:
            # Send initial state
            ws.send(json.dumps(_server.get_full_status()))
            # Keep connection alive
            while True:
                try:
                    msg = ws.receive(timeout=30)
                    if msg is None:
                        break
                    # Handle client messages (ping/pong, config updates)
                    try:
                        data = json.loads(msg)
                        if data.get("type") == "ping":
                            ws.send(json.dumps({"type": "pong"}))
                    except json.JSONDecodeError:
                        pass
                except Exception:
                    break
        finally:
            _server.unregister_ws_client(ws)

    return app
