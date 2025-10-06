"""設定ファイルを読み込むための簡単な関数を集めたモジュールです。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

# 設定ファイルの位置を1か所で管理しておくと分かりやすいです。
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "app_config.json"


def load_config() -> Dict[str, Any]:
    """設定ファイル(JSON)を辞書として読み込みます。"""
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_config(config: Dict[str, Any]) -> None:
    """アプリから設定を書き換えたい場合に備えて保存関数も用意します。"""
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
