#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
発火: issues(opened, edited) / issue_comment(created)

要件まとめ（8パターン対応）:
- 新規本文に添付なし → 何もしない（並べ替えせず、検証もスキップ）
- 新規本文に添付あり → 並べ替えして検証
- 既存本文を編集時 添付なし → 何もしない（検証スキップ）
- 既存本文を編集時 添付あり → 並べ替えして検証
- 本文に検証済み添付あり & コメントに添付なし → 何もしない（検証スキップ）
- 本文に検証済み添付あり & コメントに添付あり → 本文の添付を削除し、コメントの添付を本文に移動（並べ替え）して検証
- 本文に添付なし & コメントに添付なし → 何もしない（検証スキップ）
- 本文に添付なし & コメントに添付あり → コメントの添付を本文に移動（先頭、画像→ZIP）して検証

重要: 画像は <img ... src="https://github.com/user-attachments/assets/..."> の元テキスト、
      ZIPは [表示名](https://github.com/user-attachments/files/.../default.zip) の元テキストを
      そのまま移動し、形式は一切変えない（ファイル検証互換性維持のため）。
"""

import json
import os
import re
import sys
import subprocess
import requests
from typing import List, Tuple

# === 設定 ======================================================

GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")

SESSION = requests.Session()
if GITHUB_TOKEN:
    SESSION.headers.update({"Authorization": f"token {GITHUB_TOKEN}"})
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "User-Agent": "unified-attachments-pipeline"
})
TIMEOUT = (10, 30)

# === パターン抽出（形式は一切変更しない） ========================

IMG_HTML_RE = re.compile(
    r'<img\s+[^>]*?src="https://github\.com/user-attachments/assets/[^"]+"[^>]*?>',
    re.IGNORECASE | re.DOTALL
)
IMG_MD_RE = re.compile(  # 念のためサポート（変換しない）
    r'!\[[^\]]*?\]\(\s*(https://github\.com/user-attachments/(?:assets|files)/[^\s)]+)\s*\)',
    re.IGNORECASE
)
ZIP_MD_RE = re.compile(
    r'\[[^\]]+?\]\(\s*(https://github\.com/user-attachments/(?:files|assets)/[^\s)]+?\.zip)\s*\)',
    re.IGNORECASE
)

VERIFIED_LABEL = "Verified ✅"  # verify.py が付与する想定のラベル名
PLACEHOLDER_TEXT = "--検証に失敗したため本ファイルは削除しました--"


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for s in items:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def _extract_images_and_zips(text: str) -> Tuple[List[str], List[str]]:
    images, zips = [], []
    for m in IMG_HTML_RE.finditer(text):
        images.append(m.group(0).strip())
    for m in IMG_MD_RE.finditer(text):
        images.append(m.group(0).strip())
    for m in ZIP_MD_RE.finditer(text):
        zips.append(m.group(0).strip())
    return images, zips


def _remove_occurrences(text: str, occurrences: List[str]) -> str:
    out = text
    for s in occurrences:
        out = out.replace(s, "")
    # 失敗時サニタイズで挿入されたプレースホルダも、このタイミングで一緒に除去する
    if PLACEHOLDER_TEXT in out:
        out = out.replace(PLACEHOLDER_TEXT, "")
    return out.lstrip("\n")


def _build_top_block(images: List[str], zips: List[str]) -> str:
    lines: List[str] = []
    if images:
        lines.extend(images)
    if zips:
        if images:
            lines.append("")  # 画像とZIPの間に1行
        lines.extend(zips)
    return "\n".join(lines)


def _patch_issue_body(issue_number: int, new_body: str) -> None:
    if not GITHUB_TOKEN or not REPO:
        raise RuntimeError("必要な環境変数が不足しています。GITHUB_TOKEN, GITHUB_REPOSITORY を確認してください。")
    url = f"{GITHUB_API}/repos/{REPO}/issues/{issue_number}"
    resp = SESSION.patch(url, json={"body": new_body}, timeout=TIMEOUT)
    resp.raise_for_status()


def _patch_comment_body(comment_id: int, new_body: str) -> None:
    if not GITHUB_TOKEN or not REPO:
        raise RuntimeError("必要な環境変数が不足しています。GITHUB_TOKEN, GITHUB_REPOSITORY を確認してください。")
    url = f"{GITHUB_API}/repos/{REPO}/issues/comments/{comment_id}"
    resp = SESSION.patch(url, json={"body": new_body}, timeout=TIMEOUT)
    resp.raise_for_status()


def _run_verify_py(issue_number: int) -> int:
    """
    verify.py をサブプロセスで実行。
    ここで不足しがちな環境変数（GITHUB_TOKEN / GITHUB_REPOSITORY / ISSUE_NUMBER）を
    子プロセスの環境に注入してから実行する（保険）。
    """
    candidates = [
        os.path.join(os.getcwd(), "verify.py"),
        os.path.join(os.path.dirname(__file__), "verify.py"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
        print("verify.py が見つからないため検証をスキップします。", file=sys.stderr)
        return 2

    # 既存環境をコピーして必要キーを補完
    child_env = os.environ.copy()
    if "GITHUB_TOKEN" not in child_env and GITHUB_TOKEN:
        child_env["GITHUB_TOKEN"] = GITHUB_TOKEN
    if "GITHUB_REPOSITORY" not in child_env and REPO:
        child_env["GITHUB_REPOSITORY"] = REPO
    # ISSUE_NUMBER は verify.py が必須とするため、ここで確実に渡す
    child_env["ISSUE_NUMBER"] = str(issue_number)

    # そのほか SIGNATURE_SALT / ZIP_URL などは親環境にあればそのまま伝搬

    return subprocess.run([sys.executable, path], check=False, env=child_env).returncode


# === メイン =====================================================

def main() -> int:
    # イベントペイロード必須
    if not EVENT_PATH or not os.path.exists(EVENT_PATH):
        print("イベントペイロードが無いので検証スキップ。", file=sys.stderr)
        return 2

    with open(EVENT_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)

    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    original_body = issue.get("body") or ""
    action = payload.get("action", "")
    is_issues_event = (EVENT_NAME == "issues") and (action in ("opened", "edited"))
    is_comment_event = (EVENT_NAME == "issue_comment") and (action == "created")

    # 既に Verified ラベルが付いているか
    labels = [lbl.get("name") for lbl in issue.get("labels", []) if isinstance(lbl, dict)]
    is_verified = VERIFIED_LABEL in labels

    # 本文の添付抽出
    body_images, body_zips = _extract_images_and_zips(original_body)

    # コメントの添付抽出（コメントイベントのみ）
    comment_body = ""
    comment_images: List[str] = []
    comment_zips: List[str] = []
    if is_comment_event:
        comment_body = (payload.get("comment") or {}).get("body") or ""
        comment_images, comment_zips = _extract_images_and_zips(comment_body)

    # ここから分岐
    do_rewrite = False
    new_body = original_body

    # --- issues(opened, edited) ---
    if is_issues_event:
        if not body_images and not body_zips:
            # 新規/編集ともに本文に添付なし → スルー（検証スキップ）
            print("Issues event: no attachments in body -> skip reorder & verification.")
            return 2
        else:
            # 本文に添付あり → 並べ替えして検証
            cleaned = _remove_occurrences(original_body, body_images + body_zips)
            images_block = _dedupe_preserve_order(body_images)
            zips_block = _dedupe_preserve_order(body_zips)
            top_block = _build_top_block(images_block, zips_block)
            candidate = (top_block + "\n\n" + cleaned) if top_block else cleaned
            if candidate.strip() != original_body.strip():
                new_body = candidate
                do_rewrite = True

            if do_rewrite:
                print("Updating issue body (issues event) with reordered attachments...")
                _patch_issue_body(issue_number, new_body)

            rc = _run_verify_py(issue_number)
            print(f"verify.py exited with code {rc}")
            return rc

    # --- issue_comment(created) ---
    if is_comment_event:
        if not comment_images and not comment_zips:
            # コメントに添付なし
            if is_verified and (body_images or body_zips):
                # 本文は検証済み添付あり → 完全スルー（検証スキップ）
                print("Comment event: verified body attachments exist, comment has none -> skip.")
                return 2
            else:
                # 本文未検証 or 添付なしだが、今回コメントにも添付なし → スルー
                print("Comment event: no attachments in comment and nothing requires re-verify -> skip.")
                return 2

        # コメントに添付あり → コメントの添付を優先して本文に移動
        cleaned = _remove_occurrences(original_body, body_images + body_zips)

        # 置き換え（本文に検証済み添付あり → 本文添付は捨ててコメント添付のみ）
        images_block = _dedupe_preserve_order(comment_images)
        zips_block = _dedupe_preserve_order(comment_zips)

        top_block = _build_top_block(images_block, zips_block)
        candidate = (top_block + "\n\n" + cleaned) if top_block else cleaned
        if candidate.strip() != original_body.strip():
            new_body = candidate
            do_rewrite = True

        if do_rewrite:
            print("Updating issue body (comment event) with reordered/moved attachments...")
            _patch_issue_body(issue_number, new_body)
            # コメント側の元リンクを無効化（本文へ移動した旨のプレースホルダに差し替え）
            try:
                comment = payload.get("comment") or {}
                comment_id = comment.get("id")
                if comment_id and comment_body:
                    replaced = comment_body
                    for s in (comment_images + comment_zips):
                        replaced = replaced.replace(s, "--issue本文に移動しました--")
                    if replaced != comment_body:
                        print("Patching original comment to invalidate moved links...")
                        _patch_comment_body(int(comment_id), replaced)
            except Exception as e:
                print(f"Failed to patch original comment body: {e}", file=sys.stderr)


        rc = _run_verify_py(issue_number)
        print(f"verify.py exited with code {rc}")
        return rc

    # 想定外イベント（念のため）
    print("Unsupported event/action; skip.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
