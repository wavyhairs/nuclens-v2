# -*- coding: utf-8 -*-
"""카드 산출물 계약 검사 — tools/verify_cards.py.

이 검사가 잡아야 하는 것은 '스크립트가 0 을 돌려줬다'가 아니라 '사이트에서
카드를 볼 수 있다'이다. 그래서 테스트도 종료 코드가 아니라 **결과물의 상태**를
하나씩 망가뜨려 본다.
"""
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import verify_cards  # noqa: E402


def png_bytes(width: int = 1080, height: int = 1080, padding: int = 30_000) -> bytes:
    """구조가 온전한 PNG. 내용은 안 본다 — 서명·IHDR·IEND 와 부피만 맞춘다."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (verify_cards.PNG_SIGNATURE
            + chunk(b"IHDR", ihdr)
            # 압축되지 않는 바이트여야 파일이 실제로 커진다 — 0 을 3만 개 넣으면
            # zlib 이 수십 바이트로 줄여서 '빈 렌더' 하한에 먼저 걸린다.
            + chunk(b"IDAT", zlib.compress(os.urandom(padding)))
            + b"\x00\x00\x00\x00IEND\xaeB`\x82")


class VerifyCardsTest(unittest.TestCase):
    DAY = "2026-09-18"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.site = self.root / "cards"
        (self.site / self.DAY).mkdir(parents=True)
        for name in ("01.png", "02.png"):
            (self.site / self.DAY / name).write_bytes(png_bytes())
        self.write_index({self.DAY: ["01.png", "02.png"]})
        # 기본은 '이번에 구웠다' — album.json 이 날짜를 들고 있는 경로.
        self.album(self.DAY)

    def write_index(self, dates, latest=None):
        (self.site / "index.json").write_text(json.dumps(
            {"latest": latest or (max(dates) if dates else ""), "dates": dates},
            ensure_ascii=False), encoding="utf-8")

    def album(self, day):
        path = self.root / "album.json"
        path.write_text(json.dumps({"date": day}), encoding="utf-8")
        self.patch(ALBUM_FILE=path)

    def patch(self, **attrs):
        for key, value in attrs.items():
            patcher = mock.patch.object(verify_cards, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_verify(self, date=None):
        return verify_cards.verify(self.site, date)

    # ── 통과 ────────────────────────────────────────────────────────────
    def test_a_complete_day_passes(self):
        self.assertEqual(self.run_verify(), [])

    # ── 1. index.json 자체 ──────────────────────────────────────────────
    def test_missing_index_is_a_failure(self):
        (self.site / "index.json").unlink()
        self.assertTrue(any("index.json" in p for p in self.run_verify()))

    def test_unreadable_index_is_a_failure(self):
        (self.site / "index.json").write_text("{ not json", encoding="utf-8")
        self.assertTrue(any("JSON" in p for p in self.run_verify()))

    # ── 2. 그날 카드가 최소 1장 ─────────────────────────────────────────
    def test_the_day_must_carry_at_least_one_card(self):
        """album.json 이 그날을 가리키는데 index 에 없으면 게시가 안 된 것이다.

        publish_cards.py 는 album.json 이 없을 때 '스킵'하고 0 을 돌려준다.
        그래서 '구웠는데 안 올라갔다'는 종료 코드만으로는 안 보인다.
        """
        self.write_index({})
        problems = self.run_verify()
        self.assertTrue(any("0 장" in p for p in problems), problems)

    # ── 3. index 가 가리키는 PNG 의 실존 ────────────────────────────────
    def test_index_pointing_at_a_missing_file_fails(self):
        (self.site / self.DAY / "02.png").unlink()
        self.assertTrue(any("02.png" in p for p in self.run_verify()))

    def test_a_pruned_old_folder_left_in_the_index_fails(self):
        """보관 정리가 폴더를 지웠는데 index 가 그 날짜를 들고 있으면 띠가 깨진다.

        오늘 카드를 아무리 잘 구워도 안 잡히는 어긋남이라 모든 날짜를 본다.
        """
        self.write_index({"2026-09-01": ["01.png"], self.DAY: ["01.png", "02.png"]})
        self.assertTrue(any("2026-09-01" in p for p in self.run_verify()))

    def test_an_empty_list_in_the_index_fails(self):
        self.write_index({self.DAY: ["01.png", "02.png"], "2026-09-17": []},
                         latest=self.DAY)
        self.assertTrue(any("2026-09-17" in p for p in self.run_verify()))

    # ── 4. PNG 무결성 ───────────────────────────────────────────────────
    def test_an_empty_png_fails(self):
        (self.site / self.DAY / "01.png").write_bytes(b"")
        self.assertTrue(any("너무 작다" in p for p in self.run_verify()))

    def test_a_png_below_the_empty_render_floor_fails(self):
        """빈 렌더는 파일로는 존재한다 — 부피로 가른다."""
        (self.site / self.DAY / "01.png").write_bytes(
            png_bytes(padding=10)[:verify_cards.MIN_PNG_BYTES - 1])
        self.assertTrue(any("너무 작다" in p for p in self.run_verify()))

    def test_a_truncated_png_fails(self):
        """렌더가 중간에 끊긴 파일 — 크기는 충분한데 IEND 가 없다."""
        (self.site / self.DAY / "01.png").write_bytes(png_bytes()[:-40])
        self.assertTrue(any("IEND" in p for p in self.run_verify()))

    def test_a_non_png_payload_fails(self):
        (self.site / self.DAY / "01.png").write_bytes(b"x" * 50_000)
        self.assertTrue(any("서명" in p for p in self.run_verify()))

    def test_a_zero_sized_image_fails(self):
        (self.site / self.DAY / "01.png").write_bytes(png_bytes(width=0, height=0))
        self.assertTrue(any("크기가 0" in p for p in self.run_verify()))

    # ── 5. latest ───────────────────────────────────────────────────────
    def test_latest_must_point_at_the_newest_day(self):
        """홈의 카드뉴스 띠가 latest 를 보고 그린다 — 어긋나면 옛날 카드가 선다."""
        self.write_index({self.DAY: ["01.png", "02.png"]}, latest="2026-01-01")
        self.assertTrue(any("latest" in p for p in self.run_verify()))

    # ── 날짜 결정 ───────────────────────────────────────────────────────
    def test_a_day_with_no_brief_is_not_a_failure(self):
        """텍스트 브리핑이 안 나간 날은 카드가 없는 게 정상이다.

        여기서 실패로 보면 주말·장애일마다 거짓 경보가 난다.
        """
        self.patch(ALBUM_FILE=self.root / "gone.json",
                   OUTBOX_FILE=self.write_outbox({"date": "2026-09-19", "status": "planned"}))
        self.assertEqual(self.run_verify(), [])

    def test_a_day_with_no_cards_to_make_is_not_a_failure(self):
        """카드로 낼 이슈가 없던 날 — make_cards 가 '억지로 채우지 않는다'고 빠진다."""
        self.patch(ALBUM_FILE=self.root / "gone.json",
                   OUTBOX_FILE=self.write_outbox({"date": "2026-09-19", "status": "sent"}))
        self.assertEqual(self.run_verify(), [])

    def test_an_already_published_day_is_still_verified(self):
        """멱등 스킵 경로 — album.json 이 없어도 그날 게시본은 계속 검사한다."""
        self.patch(ALBUM_FILE=self.root / "gone.json",
                   OUTBOX_FILE=self.write_outbox({"date": self.DAY, "status": "sent"}))
        (self.site / self.DAY / "01.png").write_bytes(b"")
        self.assertTrue(any("너무 작다" in p for p in self.run_verify()))

    def test_an_explicit_date_wins(self):
        self.write_index({self.DAY: ["01.png", "02.png"]})
        self.assertTrue(any("0 장" in p for p in self.run_verify("2026-09-17")))

    def write_outbox(self, payload):
        path = self.root / "outbox.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


class FloorStaysInSyncTest(unittest.TestCase):
    def test_the_empty_render_floor_matches_make_cards(self):
        """두 곳이 같은 하한을 쓴다 — verify 가 make_cards 보다 헐거우면 무의미하다.

        make_cards 를 import 하면 gemini_client 까지 딸려 와 키 없는 환경에서
        죽으므로 값을 옮겨 적었다. 그 사본이 갈라지지 않는지를 여기서 본다.
        """
        source = (Path(__file__).resolve().parents[1] / "make_cards.py").read_text(encoding="utf-8")
        line = next(l for l in source.splitlines() if l.startswith("MIN_PNG_BYTES"))
        self.assertEqual(int(line.split("=")[1].split("#")[0].strip().replace("_", "")),
                         verify_cards.MIN_PNG_BYTES)


if __name__ == "__main__":
    unittest.main()
