"""Flask を使ってフロントエンドとバックエンドをつなぐシンプルなAPIサーバーです。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

from backend import config_loader
from backend.email_processor import run_email_processing
from backend.unzip_processor import run_unzip_processing

app = Flask(__name__, static_folder="frontend", static_url_path="")


@app.route("/")
def index() -> Any:
    """メニュー画面(HTML)を返します。"""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/config", methods=["GET"])
def get_config() -> Any:
    """現在の設定(JSON)をそのまま返します。"""
    config = config_loader.load_config()
    return jsonify(config)


@app.route("/api/process/email", methods=["POST"])
def process_email() -> Any:
    """メール処理を実行します。"""
    config = config_loader.load_config()
    payload: Dict[str, Any] = request.get_json(force=True) or {}

    paths = config.get("paths", {})
    mail_folder = payload.get("mail_folder") or paths.get("default_mail_folder", "")
    download_folder = payload.get("download_folder") or paths.get("default_download_folder", "")
    csv_output = payload.get("csv_output") or paths.get("default_csv_output", "")

    result = run_email_processing(mail_folder, download_folder, csv_output, config)

    return jsonify({"status": "ok", "result": result})


@app.route("/api/process/unzip", methods=["POST"])
def process_unzip() -> Any:
    """圧縮ファイルの展開処理を実行します。"""
    config = config_loader.load_config()
    payload: Dict[str, Any] = request.get_json(force=True) or {}

    paths = config.get("paths", {})
    archive_folder = payload.get("archive_folder") or paths.get("default_archive_folder", "")
    password_csv = payload.get("password_csv") or paths.get("default_password_csv", "")
    extract_root = payload.get("extract_root") or paths.get("default_extract_root", "")

    result = run_unzip_processing(archive_folder, password_csv, extract_root, config)

    return jsonify({"status": "ok", "result": result})


@app.route("/api/open-settings", methods=["POST"])
def open_settings() -> Any:
    """設定ファイルのパスを返すだけのダミーAPIです。"""
    config_path = Path(config_loader.CONFIG_PATH)
    return jsonify({"path": str(config_path)})


if __name__ == "__main__":
    # Flask の開発サーバーを起動します。EXE化するときは別途設定します。
    app.run(debug=True)
