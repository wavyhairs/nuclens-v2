"""아침 브리핑 후보의 글 종류 — 사설·칼럼은 빼고, 기획·분석은 '해설'로 하루 한 건(brief_kind.py).

2026-09-26 발송분 점검에서 틀린 제목 55건 중 12건이 칼럼·사설을 기관 조치처럼,
35건이 기획·해설의 배경 사실을 오늘 사건처럼 올린 것이었다(결정 D2).
"""
import unittest

import brief_kind as bk
import freshness as fr
import ranking


def _item(h, title, article_type="news", **kw):
    return {"hash": h, "title": title, "title_kr": title, "article_type": article_type, **kw}


class KindTests(unittest.TestCase):
    def test_opinion_prefixes_are_opinion(self):
        for title in ("[사설] 원전 정책 서둘러야", "[이한우 칼럼] 울산 SMR 특구", "[E·D칼럼] 전기국가",
                      "[아시아포럼] 원자력 회귀", "[창간사] 다시 기본을 묻는다", "[특별기고] SMR",
                      "박정희 때로 돌아간 산업정책 [왜냐면]", "[글로벌이코노믹 사설] 전력망"):
            with self.subTest(title=title):
                self.assertEqual(bk.kind(_item("x", title, "policy")), "opinion")

    def test_the_label_catches_a_column_without_a_prefix(self):
        self.assertEqual(bk.kind(_item("x", "메가 프로젝트의 효능감[오늘을 생각한다]", "opinion")),
                         "opinion")

    def test_an_interview_is_kept_as_a_report(self):
        self.assertEqual(bk.kind(_item("x", "[인터뷰] 울진군수 \"에너지연금 추진\"", "opinion")), "")

    def test_features_and_analysis_are_explainers(self):
        self.assertEqual(bk.kind(_item("x", "[창간기획] 냉각이냐 전력이냐", "news")), "explainer")
        self.assertEqual(bk.kind(_item("x", "NRC, 원전 규제 대수술", "analysis")), "explainer")

    def test_reports_stay_reports(self):
        for title in ("[단독] 웨스팅하우스 지분 투자 제안", "(설명자료) 확정된 바 없습니다",
                      "한수원, 체코 원전 주기기 계약"):
            with self.subTest(title=title):
                self.assertEqual(bk.kind(_item("x", title, "policy")), "")


class SplitTests(unittest.TestCase):
    def test_opinion_is_dropped_and_explainers_are_marked(self):
        items = [_item("op", "[사설] 원전"), _item("ex", "시장 해설", "analysis"),
                 _item("nw", "한수원 계약", brief_kind="explainer")]
        kept, dropped = bk.split(items, bk.resolve_config({}))
        self.assertEqual([a["hash"] for a in kept], ["ex", "nw"])
        self.assertEqual([(d["hash"], d["reason"]) for d in dropped], [("op", "opinion")])
        self.assertTrue(bk.is_explainer(kept[0]))
        self.assertFalse(bk.is_explainer(kept[1]), "어제 붙은 해설 표시가 남았다")

    def test_disabled_config_keeps_everything(self):
        items = [_item("op", "[사설] 원전"), _item("ex", "시장 해설", "analysis")]
        kept, dropped = bk.split(items, bk.resolve_config({"article_kind": {"enabled": False}}))
        self.assertEqual((len(kept), dropped), (2, []))
        self.assertFalse(any(bk.is_explainer(a) for a in kept))


class SelectionTests(unittest.TestCase):
    def test_only_one_explainer_is_selected_and_the_slot_is_backfilled(self):
        rows = [
            {"hash": "e1", "title_kr": "원전 산업의 미래 구조 분석", "importance": "must_read",
             "section": "khnp", "brief_kind": "explainer"},
            {"hash": "e2", "title_kr": "데이터센터 전력 시장 심층 진단", "importance": "must_read",
             "section": "policy", "brief_kind": "explainer"},
            {"hash": "n1", "title_kr": "한수원 체코 원전 주기기 계약 체결", "importance": "nice_to_know",
             "section": "business"},
        ]
        cfg = ranking.load_config()
        cfg["_explainer_cap"] = 1
        selected, _ = ranking.rank_and_select(rows, 2, cfg)
        self.assertEqual(sorted(a["hash"] for a in selected), ["e1", "n1"])

    def test_no_cap_leaves_selection_alone(self):
        rows = [{"hash": f"e{i}", "title_kr": t, "importance": "must_read", "section": s,
                 "brief_kind": "explainer"}
                for i, (t, s) in enumerate((("원전 산업의 미래 구조 분석", "khnp"),
                                            ("데이터센터 전력 시장 심층 진단", "policy")))]
        selected, _ = ranking.rank_and_select(rows, 2, ranking.load_config())
        self.assertEqual(len(selected), 2)

    def test_an_explainer_never_leads(self):
        rows = [{"hash": "ex", "brief_kind": "explainer"}, {"hash": "old", "stale_since": "2026-09-22"},
                {"hash": "new"}]
        self.assertEqual([r["hash"] for r in fr.fresh_first(rows)], ["new", "ex", "old"])


if __name__ == "__main__":
    unittest.main()
