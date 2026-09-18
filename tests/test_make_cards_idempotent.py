# -*- coding: utf-8 -*-
"""같은 날 카드를 두 번 굽지 않는다 — make_cards 의 멱등 가드.

카드를 cards.yml 로 떼면서 재실행이 쉬워졌다(손으로 한 번, Daily Brief 가 한 번,
push 충돌로 한 번). 그때마다 Gemini 를 새로 태우고 같은 PNG 를 다시 구우면
쿼터도 커밋도 낭비다.

예전 가드는 ``outbox["cards"]`` 만 봤는데 그건 **send_album.py 가 텔레그램
발송에 성공했을 때만** 남는 기록이다. 발송은 CARDS_SEND 가 켜진 날에만 도는
선택 기능이라, 꺼 둔 기본 상태에서는 가드가 영원히 비어 있었다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_cards  # noqa: E402


class IdempotentSkipTest(unittest.TestCase):
    DAY = "2026-09-18"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.site = self.root / "cards"
        self.outbox = self.root / "outbox.json"
        self.write_outbox({"date": self.DAY, "status": "sent"})
        self.patch(CARDS_SITE_DIR=self.site, OUTBOX_FILE=self.outbox)

    def patch(self, **attrs):
        for key, value in attrs.items():
            patcher = mock.patch.object(make_cards, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_outbox(self, payload):
        self.outbox.write_text(json.dumps(payload), encoding="utf-8")

    def publish(self, day, names=("01.png", "02.png"), on_disk=True):
        (self.site / day).mkdir(parents=True, exist_ok=True)
        if on_disk:
            for name in names:
                (self.site / day / name).write_bytes(b"png")
        (self.site / "index.json").write_text(
            json.dumps({"latest": day, "dates": {day: list(names)}}), encoding="utf-8")

    def run_main(self, *argv):
        """main() 을 돌리고 (종료 코드, 순위를 읽었는가) 를 돌려준다.

        load_site_ranking 이 불렸는지가 곧 'Gemini 를 태울 길로 들어갔는가'다 —
        그 앞에서 멈추면 LLM 호출도 렌더도 없다.
        """
        with mock.patch.object(make_cards, "load_site_ranking",
                               return_value=None) as ranking:
            with mock.patch.object(sys, "argv", ["make_cards.py", *argv]):
                code = make_cards.main()
        return code, ranking.called

    # ── 게시본을 보고 빠진다 ────────────────────────────────────────────
    def test_skips_when_the_day_is_already_on_the_site(self):
        self.publish(self.DAY)
        code, read_ranking = self.run_main()
        self.assertEqual(code, 0)
        self.assertFalse(read_ranking, "이미 게시된 날인데 순위를 읽으러 갔다")

    def test_still_skips_when_the_album_was_only_sent(self):
        """옛 가드(발송 기록)도 그대로 산다 — 둘 중 하나만 있어도 안 굽는다."""
        self.write_outbox({"date": self.DAY, "status": "sent",
                           "cards": {"date": self.DAY}})
        code, read_ranking = self.run_main()
        self.assertEqual(code, 0)
        self.assertFalse(read_ranking)

    # ── 반쯤 들어간 상태는 '했다'로 치지 않는다 ─────────────────────────
    def test_an_index_without_the_files_is_not_done(self):
        """index 는 갱신됐는데 PNG 가 없다 — 여기서 넘기면 그날은 영영 안 고쳐진다."""
        self.publish(self.DAY, on_disk=False)
        code, read_ranking = self.run_main()
        self.assertTrue(read_ranking, "PNG 가 없는데 '이미 했다'로 넘겼다")
        self.assertEqual(code, 1)   # 순위가 없어서 그 뒤에서 멈춘다

    def test_another_days_cards_do_not_count(self):
        self.publish("2026-09-17")
        _, read_ranking = self.run_main()
        self.assertTrue(read_ranking)

    def test_an_empty_list_in_the_index_is_not_done(self):
        self.publish(self.DAY, names=())
        _, read_ranking = self.run_main()
        self.assertTrue(read_ranking)

    # ── 손으로 부르면 늘 다시 굽는다 ────────────────────────────────────
    def test_force_goes_through_even_when_published(self):
        """cards.yml 의 수동 실행이 쓰는 길 — 복구는 다시 구워야 끝난다.

        발송이 실패한 날의 재시도도 이 길이다. 멱등 가드가 그것까지 막으면
        '카드는 사이트에 있는데 채널에는 안 갔다'가 영구히 고정된다.
        """
        self.publish(self.DAY)
        _, read_ranking = self.run_main("--force")
        self.assertTrue(read_ranking)

    # ── 브리핑이 안 나간 날은 애초에 안 굽는다 (종전 계약) ──────────────
    def test_an_unsent_brief_still_skips(self):
        self.write_outbox({"date": self.DAY, "status": "planned"})
        code, read_ranking = self.run_main()
        self.assertEqual(code, 0)
        self.assertFalse(read_ranking)


class AlreadyPublishedTest(unittest.TestCase):
    def test_returns_the_card_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "cards"
            (site / "2026-09-18").mkdir(parents=True)
            for name in ("01.png", "02.png", "03.png"):
                (site / "2026-09-18" / name).write_bytes(b"png")
            (site / "index.json").write_text(json.dumps(
                {"latest": "2026-09-18",
                 "dates": {"2026-09-18": ["01.png", "02.png", "03.png"]}}),
                encoding="utf-8")
            with mock.patch.object(make_cards, "CARDS_SITE_DIR", site):
                self.assertEqual(make_cards.already_published("2026-09-18"), 3)
                self.assertEqual(make_cards.already_published("2026-09-17"), 0)

    def test_a_broken_index_is_not_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "cards"
            site.mkdir(parents=True)
            (site / "index.json").write_text("{ not json", encoding="utf-8")
            with mock.patch.object(make_cards, "CARDS_SITE_DIR", site):
                self.assertEqual(make_cards.already_published("2026-09-18"), 0)

    def test_no_index_is_not_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(make_cards, "CARDS_SITE_DIR", Path(tmp) / "nope"):
                self.assertEqual(make_cards.already_published("2026-09-18"), 0)


if __name__ == "__main__":
    unittest.main()
