"""창 재생이 **운영 산출물을 건드리지 않는가**.

이 검사가 있는 이유는 두 번 당했기 때문이다.

    1회차  issue_insights.json(463줄) · keei_llm_matches.json(72줄) 이 더럽혀졌다
    2회차  web/public/brief · web/public/issue · web/_audit 이 재생본으로 바뀌어
           페이지와 데이터가 서로 다른 빌드의 것이 되고 검사 세 건이 깨졌다

둘 다 코드 회귀가 아니라 **격리 누락**이었고, 둘 다 `git status` 나 테스트가
아니었으면 못 봤다. 그래서 "출력 경로에 구멍이 생기면 재생기가 반드시 막는다"를
검사로 못 박는다.
"""

import re
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLAY = (ROOT / "tools" / "window_replay.py").read_text(encoding="utf-8")

# 재생이 덮으면 안 되는 산출물을 가진 모듈들.
WRITER_MODULES = ("web/build_data.py", "issue_ledger.py", "issue_review.py",
                  "issue_insight.py", "keei_match.py")
# 출력 경로가 아닌 환경변수(입력·진단 스위치)는 격리 대상이 아니다.
NOT_OUTPUTS = {"BOT_DIR", "EMBEDDINGS_FILE", "GENERATION_ID"}

_ENV_RE = re.compile(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"')


class IsolationContractTests(unittest.TestCase):
    def test_every_output_override_is_set_by_the_replay_harness(self):
        missing = []
        for name in WRITER_MODULES:
            source = (ROOT / name).read_text(encoding="utf-8")
            for variable in _ENV_RE.findall(source):
                if not (variable.endswith("_DIR") or variable.endswith("_FILE")):
                    continue
                if variable in NOT_OUTPUTS:
                    continue
                if f'"{variable}"' not in REPLAY:
                    missing.append(f"{name}:{variable}")
        self.assertEqual(missing, [], f"재생기가 덮어쓸 수 있는 경로: {missing}")

    def test_every_override_points_inside_the_arm(self):
        """arm 밖을 가리키는 값이 하나라도 있으면 그 파일이 운영본이 된다."""
        arm_scoped = ("target", "ledger", "reviews")
        for line in REPLAY.splitlines():
            stripped = line.strip()
            if not (stripped.startswith('"') and ('_FILE":' in stripped
                                                  or '_DIR":' in stripped)):
                continue
            self.assertTrue(any(name in stripped for name in arm_scoped), stripped)


class MeasurementTests(unittest.TestCase):
    def test_evidence_days_are_deduplicated_by_hash(self):
        """대표 기사는 related_articles 에도 실려 있다.

        그대로 세면 단독 이슈조차 날짜 두 개를 가진 것처럼 보인다 — 첫 재생에서
        554/554 가 'span 표본' 으로 잡혀 D 문서의 n=273 과 비교가 안 됐다.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "window_replay", ROOT / "tools" / "window_replay.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        issue = {
            "representative_article": {"hash": "h1", "article_date": "2026-08-01"},
            "related_articles": [
                {"hash": "h1", "article_date": "2026-08-01"},
            ],
        }
        self.assertEqual(module._evidence_days(issue), [date(2026, 8, 1)])

        issue["related_articles"].append({"hash": "h2", "article_date": "2026-08-20"})
        self.assertEqual(module._evidence_days(issue),
                         [date(2026, 8, 1), date(2026, 8, 20)])


if __name__ == "__main__":
    unittest.main()
