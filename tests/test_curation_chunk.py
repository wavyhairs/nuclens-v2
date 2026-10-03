"""요약 묶음 크기와 본문 길이의 계약 (2026-10-03).

묶음 15 → 8 은 한도가 아니라 **섞임** 가설이다(검사기 설계에서 10건 묶음이 옆
기사 본문을 근거로 끌어오는 것을 실측). 본문 1,500 → 3,000 은 잘린 뒷부분을
요약기가 추측하지 않게 하려는 것이다. 둘은 맞물려 호출당 입력을 예전과 같게
둔다. 둘 다 저장소 변수 하나로 되돌린다 — 코드 변경 없이.
"""

import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import article_body
import news_bot
from tools import curation_p4

ROOT = Path(__file__).resolve().parents[1]


class ChunkAndBodyContractTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {"CURATION_BATCH_CHUNK": "", "MAX_BODY_CHARS": ""}, clear=False):
            importlib.reload(article_body)
            importlib.reload(news_bot)
            self.assertEqual(news_bot.BATCH_CHUNK, 8)
            self.assertEqual(article_body.MAX_BODY_CHARS, 3000)

    def test_input_per_call_stays_where_it_was(self):
        """15 × 1,500 = 22,500 자였다. 묶음을 줄인 만큼 본문을 늘려 그 근처에 둔다."""
        importlib.reload(article_body)
        importlib.reload(news_bot)
        self.assertLessEqual(news_bot.BATCH_CHUNK * article_body.MAX_BODY_CHARS, 24_000)
        self.assertGreaterEqual(news_bot.BATCH_CHUNK * article_body.MAX_BODY_CHARS, 20_000)

    def test_repo_variables_roll_back_without_code(self):
        with patch.dict(os.environ, {"CURATION_BATCH_CHUNK": "15", "MAX_BODY_CHARS": "1500"}, clear=False):
            importlib.reload(article_body)
            importlib.reload(news_bot)
            self.assertEqual(news_bot.BATCH_CHUNK, 15)
            self.assertEqual(article_body.MAX_BODY_CHARS, 1500)
        importlib.reload(article_body)
        importlib.reload(news_bot)

    def test_workflows_pass_both_variables_together(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            yml = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("CURATION_BATCH_CHUNK: ${{ vars.CURATION_BATCH_CHUNK }}", yml)
            self.assertIn("MAX_BODY_CHARS: ${{ vars.MAX_BODY_CHARS }}", yml)

    def test_p4_calibration_keeps_the_capture_batch_size(self):
        """이미 기록된 P4 captures 는 15건 묶음이다. production 값이 바뀌어도 보정의
        후보 선별은 captures 크기로 한다 — 안 그러면 후보가 0건이 돼 보정이 조용히
        멈춘다."""
        self.assertEqual(curation_p4.CAPTURE_BATCH_CHUNK, 15)


if __name__ == "__main__":
    unittest.main()
