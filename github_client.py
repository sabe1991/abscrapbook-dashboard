"""GitHub Contents API の薄いラッパー(Python版)。

Android側の`GitHubContentsClient.kt`(ABScrapbookリポジトリ)と同じ考え方で、
このダッシュボードが使う分だけ(単一ファイルの取得・作成/更新)を実装する。
一覧取得・削除はAndroid側(inboxの消し込み)でしか使わないためこちらには無い。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import requests

API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class RepoConfig:
    repo: str
    token: str
    branch: str = "main"


def get_json(cfg: RepoConfig, path: str) -> tuple[dict | None, str | None]:
    """ファイルを取得してJSONとしてパースする。存在しなければ (None, None) を返す。"""
    url = f"{API_BASE}/repos/{cfg.repo}/contents/{path}"
    resp = requests.get(url, headers=_headers(cfg), params={"ref": cfg.branch}, timeout=20)
    if resp.status_code == 404:
        return None, None
    resp.raise_for_status()
    body = resp.json()
    content = base64.b64decode(body["content"]).decode("utf-8")
    return json.loads(content), body["sha"]


def put_json(cfg: RepoConfig, path: str, data: dict, message: str, sha: str | None = None) -> None:
    """ファイルを作成/更新する。[sha]は更新時のみ指定する(新規作成時はNone)。"""
    url = f"{API_BASE}/repos/{cfg.repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii"),
        "branch": cfg.branch,
    }
    if sha:
        payload["sha"] = sha
    resp = requests.put(url, headers=_headers(cfg), json=payload, timeout=20)
    resp.raise_for_status()


def _headers(cfg: RepoConfig) -> dict:
    return {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
