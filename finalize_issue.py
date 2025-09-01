import os
import sys
import requests

# ---- GitHub API helpers ----
def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def post_comment(repo: str, issue: str | int, token: str, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{issue}/comments"
    r = requests.post(url, json={"body": body}, headers=_gh_headers(token), timeout=15)
    r.raise_for_status()

def add_labels(repo: str, issue: str | int, token: str, labels: list[str]) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{issue}/labels"
    r = requests.post(url, json={"labels": labels}, headers=_gh_headers(token), timeout=15)
    r.raise_for_status()

def set_issue_state(repo: str, issue: str | int, token: str, state: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{issue}"
    r = requests.patch(url, json={"state": state}, headers=_gh_headers(token), timeout=15)
    r.raise_for_status()

def main() -> int:
    # verify ステップからの結果
    result = os.environ.get("VERIFICATION_RESULT", "")      # "success" / "failure" / "skipped"
    exit_code = os.environ.get("VERIFICATION_EXIT_CODE", "")  # "0" / "1" / 空

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number = os.environ.get("ISSUE_NUMBER", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")

    print(f"VERIFICATION_EXIT_CODE: {exit_code}")
    print(f"VERIFICATION_RESULT: {result}")

    if not all([repo, issue_number, github_token]):
        print("必要な環境変数が不足しています。GITHUB_TOKEN, ISSUE_NUMBER, GITHUB_REPOSITORY を確認してください。")
        return 1

    # --- 成功時は reopen（冪等：既に open なら何も起きない） ---
    if result.lower() == "success":
        try:
            set_issue_state(repo, issue_number, github_token, "open")
            print("Verification succeeded. Finalize script re-opened the issue (if it was closed).")
        except Exception as e:
            print(f"[warn] Issue再オープンに失敗: {e}")
        return 0

    # --- 添付なしは何もしない ---
    if result.lower() == "skipped":
        print("Verification skipped. Finalize script does nothing.")
        return 0

    # --- 失敗時だけ“止め”を刺す（冪等） ---
    if result.lower() == "failure":
        try:
            post_comment(repo, issue_number, github_token, "検証失敗を確認しました。Issueをクローズします。")
        except Exception as e:
            print(f"[warn] コメント投稿に失敗: {e}")
        try:
            add_labels(repo, issue_number, github_token, ["Invalid ❌"])
        except Exception as e:
            print(f"[warn] ラベル付与に失敗: {e}")
        try:
            set_issue_state(repo, issue_number, github_token, "closed")
        except Exception as e:
            print(f"[warn] Issueクローズに失敗: {e}")
        return 0

    # --- 想定外（result 未設定など）は exit_code でフォールバック ---
    if exit_code == "0":
        try:
            set_issue_state(repo, issue_number, github_token, "open")
            print("Verification succeeded (by exit code fallback). Finalize script re-opened the issue (if it was closed).")
        except Exception as e:
            print(f"[warn] Issue再オープンに失敗: {e}")
        return 0

    # それ以外は失敗扱い
    try:
        post_comment(repo, issue_number, github_token, "検証失敗（不明な状態）。Issueをクローズします。")
    except Exception as e:
        print(f"[warn] コメント投稿に失敗: {e}")
    try:
        add_labels(repo, issue_number, github_token, ["Invalid ❌"])
    except Exception as e:
        print(f"[warn] ラベル付与に失敗: {e}")
    try:
        set_issue_state(repo, issue_number, github_token, "closed")
    except Exception as e:
        print(f"[warn] Issueクローズに失敗: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
