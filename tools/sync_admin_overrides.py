"""운영 콘솔이 KV 에 쌓은 판정을 `admin_overrides.json` 으로 끌어온다.

왜 이 단계가 있나
-----------------
콘솔은 정적 사이트(Cloudflare Pages)라 저장소에 쓸 수 없고, 파이프라인은 GitHub
Actions 에서 로컬 JSON 을 읽는다. 둘 사이를 잇는 것이 KV 다. 콘솔은 KV 에 적고,
워크플로가 시작할 때 이 스크립트가 그것을 파일로 내려놓는다. 그 파일은 다른 상태
파일과 같이 커밋되므로 **git 이 계속 DB**이고, 판정 하나하나가 커밋 이력에 남는다.

조용히 되돌리지 않는다
----------------------
KV 를 못 읽으면 마지막으로 커밋된 파일을 **그대로 둔다**. 실패했다고 빈 파일을 쓰면
관리자가 몇 주에 걸쳐 쌓은 판정이 한 번의 네트워크 오류로 사라지고, 그 사고는
조용하다 — 다음 수집이 그냥 예전처럼 도는 것으로 보인다. 그래서 실패는 시끄럽게
찍되 종료 코드는 0 이다(수집 자체를 세우지는 않는다).

필요한 것
---------
`CLOUDFLARE_API_TOKEN` · `CLOUDFLARE_ACCOUNT_ID` — 이미 배포에 쓰는 것과 같은 값.
토큰에 **Workers KV Storage: Read** 권한이 있어야 한다(배포 전용 토큰에는 없다).
네임스페이스 ID 는 Pages 프로젝트의 바인딩에서 찾아내므로 따로 설정하지 않는다.
못 찾으면 `ADMIN_KV_NAMESPACE_ID` 를 직접 줄 수도 있다.

    python tools/sync_admin_overrides.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Windows 콘솔 UTF-8 강제 (저장소 다른 모듈과 동일). 없으면 한국어 진단 문구가
# cp1252 에서 터져 '동기화 실패'가 아니라 '스크립트 크래시'로 보인다.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import admin_overrides  # noqa: E402

API_ROOT = "https://api.cloudflare.com/client/v4"
KV_KEY = "admin:overrides"
BINDING_NAME = "ADMIN_KV"
TIMEOUT = 20.0


def _log(message: str) -> None:
    print(f"[admin-sync] {message}", flush=True)


def _request(url: str, token: str) -> dict | None:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        _log(f"HTTP {exc.code} — {url.split('/client/v4')[-1]} :: {body}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        _log(f"요청 실패 — {type(exc).__name__}: {exc}")
    return None


def namespace_id(account: str, token: str, project: str) -> str:
    """Pages 프로젝트의 ADMIN_KV 바인딩에서 네임스페이스 ID 를 읽는다.

    사람이 설정할 값을 늘리지 않으려는 것이다. 콘솔 자체가 "설정할 환경변수는 없다"를
    지키고 있는데(미들웨어 주석), 동기화만 ID 를 손으로 붙여 두면 네임스페이스를
    다시 만든 날 조용히 옛 값을 읽는다.
    """
    direct = os.environ.get("ADMIN_KV_NAMESPACE_ID", "").strip()
    if direct:
        return direct
    payload = _request(f"{API_ROOT}/accounts/{account}/pages/projects/{project}", token)
    configs = ((payload or {}).get("result") or {}).get("deployment_configs") or {}
    for env_name in ("production", "preview"):
        binding = ((configs.get(env_name) or {}).get("kv_namespaces") or {}).get(BINDING_NAME)
        if isinstance(binding, dict) and binding.get("namespace_id"):
            return str(binding["namespace_id"])
    return ""


def fetch_entries(account: str, token: str, ns_id: str) -> list[dict] | None:
    url = f"{API_ROOT}/accounts/{account}/storage/kv/namespaces/{ns_id}/values/{KV_KEY}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # 키가 없다 = 관리자가 아직 아무 판정도 안 했다. 빈 목록이 정답이다.
            _log("KV 에 판정 기록이 아직 없습니다 (정상)")
            return []
        _log(f"KV 읽기 실패 HTTP {exc.code} — 커밋된 파일을 그대로 둡니다")
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        _log(f"KV 읽기 실패 {type(exc).__name__} — 커밋된 파일을 그대로 둡니다")
        return None
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        _log("KV 값의 모양이 예상과 다릅니다 — 커밋된 파일을 그대로 둡니다")
        return None
    return [e for e in entries if isinstance(e, dict) and e.get("id")]


def write_file(entries: list[dict], path: Path) -> bool:
    """판정 목록을 파일로 내려놓는다. 내용이 같으면 쓰지 않는다(빈 커밋 방지)."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}

    known = set(admin_overrides.KINDS)
    unknown = sorted({str(e.get("kind")) for e in entries} - known)
    if unknown:
        # 콘솔이 이 배포보다 새 종류를 쓰고 있다. 버리지 않고 파일에 남긴다 —
        # 파이프라인은 모르는 kind 를 무시하고, 배포가 따라오면 그때부터 듣는다.
        _log(f"이 배포가 모르는 판정 종류 {unknown} — 파일에는 남기고 무시합니다")

    payload = dict(current) if isinstance(current, dict) else {}
    payload.update({
        "version": admin_overrides.CONTRACT_VERSION,
        "source": "kv",
        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "updated_at": max((str(e.get("created_at") or "") for e in entries), default=""),
        "entries": entries,
    })

    if (isinstance(current, dict)
            and current.get("entries") == entries
            and current.get("source") == "kv"):
        _log(f"변경 없음 — 판정 {len(entries)}건 그대로")
        return False

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log(f"판정 {len(entries)}건을 {path.name} 에 기록했습니다")
    return True


def main() -> int:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    project = os.environ.get("CLOUDFLARE_PAGES_PROJECT", "nuclens-v2").strip()
    path = Path(os.environ.get("ADMIN_OVERRIDES_FILE") or (ROOT / "admin_overrides.json"))

    if not token or not account:
        _log("CLOUDFLARE_API_TOKEN/ACCOUNT_ID 미설정 — 콘솔 판정 동기화를 건너뜁니다")
        return 0

    ns_id = namespace_id(account, token, project)
    if not ns_id:
        _log(f"Pages 프로젝트 '{project}' 에서 {BINDING_NAME} 바인딩을 못 찾았습니다. "
             f"토큰에 Pages:Read 와 Workers KV Storage:Read 가 있는지 확인하세요 "
             f"(또는 ADMIN_KV_NAMESPACE_ID 를 직접 지정).")
        return 0

    entries = fetch_entries(account, token, ns_id)
    if entries is None:
        return 0
    write_file(entries, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
