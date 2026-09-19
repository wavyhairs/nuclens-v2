"""후보 생성은 **프로세스가 달라도 같아야 한다.**

왜 별도 파일인가
----------------
이 성질은 한 프로세스 안에서는 증명할 수 없다. `set` 의 순회 순서는 프로세스마다
정해지고 그 프로세스 안에서는 내내 같기 때문에, 보통의 단위 테스트는 흔들림을
절대 보지 못한다. 그래서 **자식 프로세스를 seed 를 바꿔 가며** 띄운다.

무엇을 잡았나 (2026-09-19)
--------------------------
`event_retrieval.candidates()` 가 어휘 역색인을 펼칠 때 판별력 높은 낱말 12개를
고르는데, 그 정렬이 idf 하나만 보고 있었다. idf 가 같은 낱말은 흔하고 —
df 가 같으면 idf 도 같다 — 동점은 집합 순회 순서로 깨졌다. 실측 —

    PYTHONHASHSEED=0  후보 3,615쌍
    PYTHONHASHSEED=1  후보 3,595쌍
    PYTHONHASHSEED=2  후보 3,602쌍

후보가 흔들리면 그 위의 판정·묶음·`thread_id` 가 전부 흔들린다. 같은 원장으로
빌드를 두 번 돌렸을 때 스토리 주소가 바뀌면 밖에 나간 링크가 죽는다.
"""

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 동점을 **실제로** 만든다. 앞선 시도는 모든 사건에 같은 엔티티를 달아서,
# 어휘 12칸이 무엇으로 채워지든 후보 풀이 엔티티 역색인으로 똑같이 채워졌다 —
# 그래서 고장난 코드에서도 테스트가 통과했다. 조건은 둘이다:
#
#   · 공유 엔티티·호기가 없어야 한다. 그래야 풀이 **어휘로만** 정해진다.
#   · 기준 사건의 낱말이 전부 같은 df 를 가져야 한다. df 가 같으면 idf 가 같고,
#     그때 상위 12칸을 무엇으로 고르는지가 드러난다.
#
# 기준 사건은 zz00~zz29 서른 낱말을 들고, 각 낱말은 정확히 한 이웃에만 더 있다.
# 따라서 서른 낱말의 df 가 전부 2 — 완전한 동점이고, 상위 12칸이 이웃 서른 중
# 열둘을 고른다.
NEIGHBOURS = 30
STORE = {"issues": {
    "issue-target": {
        "title": " ".join(f"zz{index:02d}" for index in range(NEIGHBOURS)),
        "summary": "", "first_seen": "2026-09-01", "last_seen": "2026-09-02",
        "briefing_count": 1,
    },
    **{
        f"issue-n{index:02d}": {
            "title": f"zz{index:02d} filler{index:02d}a filler{index:02d}b",
            "summary": "", "first_seen": "2026-09-01", "last_seen": "2026-09-02",
            "briefing_count": 1,
        } for index in range(NEIGHBOURS)
    },
}}

PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import event_retrieval
    store = json.loads(sys.argv[2])
    import issue_ledger
    issue_ledger.load_store = lambda path=None: store
    index = event_retrieval.build_index()
    target = index.by_id["issue-target"]
    rows = event_retrieval.candidates(index, target, limit=64, min_score=0.0)
    print(json.dumps([row["issue_id"] for row in rows]))
""")


class CandidateDeterminismTests(unittest.TestCase):
    def _probe(self, seed: str) -> list[str]:
        result = subprocess.run(
            [sys.executable, "-c", PROBE, str(ROOT), json.dumps(STORE, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8",
            env={"PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8",
                 "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                 "PATH": __import__("os").environ.get("PATH", "")},
            timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_candidates_do_not_depend_on_the_hash_seed(self):
        runs = {seed: self._probe(seed) for seed in ("0", "1", "12345")}
        first = runs["0"]
        for seed, rows in runs.items():
            self.assertEqual(rows, first,
                             f"PYTHONHASHSEED={seed} 에서 후보가 달라졌다: {runs}")
        self.assertTrue(first, "후보가 하나도 나오지 않아 아무것도 증명하지 못했다")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
