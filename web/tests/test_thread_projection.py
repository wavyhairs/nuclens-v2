"""스토리 주소를 이슈에 찍는 투영 — `build_data.stamp_thread_ids`.

이 투영이 없던 동안 무슨 일이 있었나
------------------------------------
2026-08-23 「기후부, 전력망 효율 위해 9월부터 '계절별 송전용량' 시범 적용」과
2026-09-18 「기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행」은
**이미 같은 스토리로 판정되어 있었다** — `thread-5aaa7dea33e702ac`,
`stage_progress`, 2026-09-18 판정, 라이브 `threads.json` 에 노출까지 된 상태.

그런데 `issues.json` 의 두 이슈는 `thread_id` 를 들고 있지 않았다. 그래서 이슈
상세는 그 스토리의 존재를 알 길이 없었고, 화면에서는 두 사건이 남남이었다.
판정이 아니라 **투영**이 끊긴 자리였다.

이 파일이 지키는 것
-------------------
① 스토리에 속한 이슈가 주소를 받는다.
② `related_articles` 는 **한 글자도 건드리지 않는다.** 사건 계층(thread)과
   기사 계층(evidence)을 가르는 것이 이 투영의 요점이라, 8월 사건의 기사가 9월
   이슈의 근거로 섞이면 그 이슈의 검증 수치가 거짓이 된다.
③ 페이로드가 숨김이면 아무도 주소를 받지 않는다 — `thread_web.gate()` 의 판정이
   이슈 상세까지 그대로 미쳐야 한다.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import build_data  # noqa: E402

# ── 라이브 실측 픽스처 (2026-09-20) ──────────────────────────────────────
SAR_AUG = "issue-099ce0d43f46036a"
SAR_SEP = "story-9e227f9daff25cd5"
SAR_THREAD = "thread-5aaa7dea33e702ac"

SAR_AUG_TITLE = "기후부, 전력망 효율 위해 9월부터 '계절별 송전용량' 시범 적용"
SAR_SEP_TITLE = "기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행"


SAR_SEP_HASHES = ["9e227f9daff25cd5", "9a94d3be8028be9e", "527b2e1db7613a88"]
SAR_AUG_HASHES = ["099ce0d43f46036a", "eead02e2334a6e47", "4602974590c8de51"]


def threads_payload(**overrides) -> dict:
    payload = {
        "version": "thread-web-v2",
        "date_kind": "first_seen",
        "visible": True,
        "threads": [{
            "thread_id": SAR_THREAD,
            "title": SAR_SEP_TITLE,
            "first_seen": "2026-08-24",
            "last_seen": "2026-09-19",
            "lifespan_days": 26,
            "event_count": 2,
            "events": [
                {"event_id": SAR_SEP, "source_event_id": SAR_SEP, "title": SAR_SEP_TITLE,
                 "date": "2026-09-19", "date_kind": "first_seen",
                 "evidence_hashes": SAR_SEP_HASHES},
                {"event_id": SAR_AUG, "source_event_id": SAR_AUG, "title": SAR_AUG_TITLE,
                 "date": "2026-08-24", "date_kind": "first_seen",
                 "evidence_hashes": SAR_AUG_HASHES},
            ],
            "flow": [
                {"event_id": SAR_AUG, "source_event_id": SAR_AUG,
                 "source_event_ids": [SAR_AUG], "evidence_hashes": SAR_AUG_HASHES,
                 "title": SAR_AUG_TITLE, "date": "2026-08-24", "date_kind": "first_seen",
                 "relation_to_next": "stage_progress", "relation_label": "다음 단계"},
                {"event_id": SAR_SEP, "source_event_id": SAR_SEP,
                 "source_event_ids": [SAR_SEP], "evidence_hashes": SAR_SEP_HASHES,
                 "title": SAR_SEP_TITLE, "date": "2026-09-19", "date_kind": "first_seen",
                 "relation_to_next": "", "relation_label": ""},
            ],
        }],
        "stats": {"threads": 1},
        "redirects": {},
    }
    payload.update(overrides)
    return payload


def catalog() -> list[dict]:
    """9/18 은 선정 1건 + 추가 근거 5건, 8/23 은 선정 1건 + 근거 2건."""
    return [
        {"issue_id": SAR_SEP, "title": SAR_SEP_TITLE, "first_seen": "2026-09-19",
         "related_articles": [
             {"hash": "9e227f9daff25cd5", "member_role": "card"},
             {"hash": "9a94d3be8028be9e", "member_role": "evidence"},
             {"hash": "527b2e1db7613a88", "member_role": "evidence"},
             {"hash": "110f52bd2668f703", "member_role": "evidence"},
             {"hash": "7c344963b55f3200", "member_role": "evidence"},
             {"hash": "346c7125d8123051", "member_role": "evidence"},
         ]},
        {"issue_id": SAR_AUG, "title": SAR_AUG_TITLE, "first_seen": "2026-08-24",
         "related_articles": [
             {"hash": "099ce0d43f46036a", "member_role": "card"},
             {"hash": "eead02e2334a6e47", "member_role": "evidence"},
             {"hash": "4602974590c8de51", "member_role": "evidence"},
         ]},
        {"issue_id": "issue-무관한사건", "title": "월성 2호기 계획예방정비 착수",
         "first_seen": "2026-09-10", "related_articles": []},
    ]


class StampThreadIdsTests(unittest.TestCase):
    def test_sar_events_share_one_thread_and_keep_separate_ids(self):
        issues = catalog()
        stamped = build_data.stamp_thread_ids(issues, threads_payload())
        by_id = {issue["issue_id"]: issue for issue in issues}
        self.assertEqual(stamped, 2)
        # 서로 다른 사건으로 남는다. 합치는 것이 아니라 잇는 것이다.
        self.assertNotEqual(SAR_AUG, SAR_SEP)
        self.assertEqual(by_id[SAR_AUG]["thread_id"], SAR_THREAD)
        self.assertEqual(by_id[SAR_SEP]["thread_id"], SAR_THREAD)

    def test_unrelated_issue_gets_an_empty_slot_not_a_neighbour_thread(self):
        issues = catalog()
        build_data.stamp_thread_ids(issues, threads_payload())
        lone = {issue["issue_id"]: issue for issue in issues}["issue-무관한사건"]
        # 칸은 늘 있고 값은 비어 있다 — 없는 칸과 빈 칸을 화면이 가를 필요가 없다.
        self.assertIn("thread_id", lone)
        self.assertEqual(lone["thread_id"], "")

    def test_related_articles_are_untouched(self):
        """근거는 한 글자도 움직이지 않는다. 이 투영이 지키는 첫째 계약이다."""
        issues = catalog()
        before = copy.deepcopy([issue["related_articles"] for issue in issues])
        build_data.stamp_thread_ids(issues, threads_payload())
        after = [issue["related_articles"] for issue in issues]
        self.assertEqual(before, after)

    def test_august_articles_never_reach_the_september_issue(self):
        issues = catalog()
        build_data.stamp_thread_ids(issues, threads_payload())
        by_id = {issue["issue_id"]: issue for issue in issues}
        sep_hashes = {row["hash"] for row in by_id[SAR_SEP]["related_articles"]}
        aug_hashes = {row["hash"] for row in by_id[SAR_AUG]["related_articles"]}
        self.assertEqual(sep_hashes & aug_hashes, set())
        # 9/18 의 선정 1건 + 추가 근거 5건이 그대로다.
        roles = [row.get("member_role") for row in by_id[SAR_SEP]["related_articles"]]
        self.assertEqual(roles.count("card"), 1)
        self.assertEqual(roles.count("evidence"), 5)

    def test_hidden_payload_stamps_nobody(self):
        """화면이 숨겨지는 회차에는 이슈 상세도 스토리를 말하지 않는다.

        `thread_web.build_payload` 는 숨김일 때 `threads` 를 빈 목록으로 내보낸다.
        그 계약에 기대어 여기서 따로 분기하지 않는 것이 설계다 — 분기를 두면 두
        화면이 서로 다른 가시성 판정을 갖게 된다.
        """
        issues = catalog()
        hidden = threads_payload(visible=False, threads=[], stats={"threads": 0})
        self.assertEqual(build_data.stamp_thread_ids(issues, hidden), 0)
        self.assertTrue(all(issue["thread_id"] == "" for issue in issues))

    def test_rebuild_is_idempotent(self):
        issues = catalog()
        build_data.stamp_thread_ids(issues, threads_payload())
        first = copy.deepcopy(issues)
        build_data.stamp_thread_ids(issues, threads_payload())
        self.assertEqual(first, issues)

    def test_stale_thread_id_is_replaced_not_kept(self):
        """이번 회차에 스토리에서 떨어진 이슈는 옛 주소를 들고 있지 않는다.

        원장은 detach 를 기록하고 `live_thread_ids` 에서 빠진다. 그때 이슈가 옛
        `thread_id` 를 그대로 들고 있으면 화면이 없는 스토리를 가리킨다.
        """
        issues = catalog()
        for issue in issues:
            issue["thread_id"] = "thread-옛주소"
        build_data.stamp_thread_ids(issues, threads_payload())
        by_id = {issue["issue_id"]: issue for issue in issues}
        self.assertEqual(by_id[SAR_SEP]["thread_id"], SAR_THREAD)
        self.assertEqual(by_id["issue-무관한사건"]["thread_id"], "")

    def test_malformed_payload_does_not_raise(self):
        """부가 데이터의 실패가 사이트를 죽이지 않는다(8/1 빈 화면 사고 계약)."""
        issues = catalog()
        for broken in ({}, {"threads": None}, {"threads": [{}]},
                       {"threads": [{"thread_id": SAR_THREAD, "events": None}]},
                       {"threads": [{"thread_id": "", "events": [{"event_id": SAR_SEP}]}]}):
            with self.subTest(payload=broken):
                self.assertEqual(build_data.stamp_thread_ids(issues, broken), 0)
                self.assertTrue(all(issue["thread_id"] == "" for issue in issues))


class ContestedEventTests(unittest.TestCase):
    """**한 사건을 둘 이상의 스토리가 주장하면 주소를 비운다.**

    왜 이 검사가 생겼나 (실측 2026-09-20 라이브)
    --------------------------------------------
    투영은 오래 `event_id` 로 풀었다. 그런데 그 값은 `thread_web.surviving_id()`
    를 거친 **라우트** 주소라 흡수된 사건이 전부 같은 값으로 접힌다 — 사건
    229건이 라우트 id 171개로 접혔고, 36개 id 를 둘 이상(최대 넷)의 스토리가
    동시에 주장했다. 예전 코드는 `thread_of[event_id] = thread_id` 로 덮어써서
    **마지막으로 순회한 스토리가 조용히 이겼다.** 즉 주소가 붙어 있어도 맞다는
    보장이 없었고, 목록 순서가 바뀌면 답도 바뀌었다.

    원장의 사건 id(`source_event_id`)로 풀면 정해진다 — 같은 날 살아 있는
    스토리 128개의 사건 378건이 전부 고유했다. 그래도 이 분기를 남기는 이유는,
    판정이 한 사건을 두 스토리에 넣는 날 **틀린 주소보다 빈칸이 낫기** 때문이다.
    빈칸은 타임라인이 안 서는 것으로 끝나지만, 틀린 주소는 다른 이야기의 근거를
    이 이슈의 것이라고 주장한다.
    """

    def _contested(self) -> dict:
        payload = threads_payload()
        other = copy.deepcopy(payload["threads"][0])
        other["thread_id"] = "thread-다른이야기"
        # 같은 사건을 두 스토리가 주장한다.
        payload["threads"].append(other)
        return payload

    def test_a_contested_event_gets_an_empty_slot_not_a_coin_flip(self):
        issues = catalog()
        build_data.stamp_thread_ids(issues, self._contested())
        by_id = {issue["issue_id"]: issue for issue in issues}
        self.assertEqual(by_id[SAR_SEP]["thread_id"], "")
        self.assertEqual(by_id[SAR_AUG]["thread_id"], "")

    def test_the_answer_does_not_depend_on_thread_order(self):
        """순서를 뒤집어도 같은 답. 예전 코드는 여기서 답이 갈렸다."""
        forward, backward = self._contested(), self._contested()
        backward["threads"].reverse()
        first, second = catalog(), catalog()
        build_data.stamp_thread_ids(first, forward)
        build_data.stamp_thread_ids(second, backward)
        self.assertEqual([issue["thread_id"] for issue in first],
                         [issue["thread_id"] for issue in second])

    def test_route_id_collisions_alone_do_not_contest_anything(self):
        """흡수로 **라우트** id 만 같아진 경우는 충돌이 아니다.

        v2 가 사건 신원을 따로 싣는 이유가 정확히 이것이다. 두 스토리의 서로
        다른 사건이 같은 주소로 열릴 수는 있어도, 그것이 "같은 사건"이라는
        뜻은 아니다.
        """
        payload = threads_payload()
        other = copy.deepcopy(payload["threads"][0])
        other["thread_id"] = "thread-다른이야기"
        for row in other["events"]:
            row["source_event_id"] = row["source_event_id"] + "-원본"
        for row in other["flow"]:
            row["source_event_id"] = row["source_event_id"] + "-원본"
            row["source_event_ids"] = [row["source_event_id"]]
        payload["threads"].append(other)

        issues = catalog()
        build_data.stamp_thread_ids(issues, payload)
        by_id = {issue["issue_id"]: issue for issue in issues}
        self.assertEqual(by_id[SAR_SEP]["thread_id"], SAR_THREAD)
        self.assertEqual(by_id[SAR_AUG]["thread_id"], SAR_THREAD)

    def test_v1_payload_still_resolves_through_event_id(self):
        """옛 스냅샷을 읽는 빌드가 여기서 통째로 비지 않는다."""
        payload = threads_payload(version="thread-web-v1")
        for thread in payload["threads"]:
            for row in (*thread["events"], *thread["flow"]):
                row.pop("source_event_id", None)
                row.pop("source_event_ids", None)
        issues = catalog()
        self.assertEqual(build_data.stamp_thread_ids(issues, payload), 2)


if __name__ == "__main__":
    unittest.main()
