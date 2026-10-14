"""해외 AI 레인 0단계 — 프리필터 경계·판정 정규화·평가기 계산을 잠근다.

프리필터 사례는 아카이브 실측 제목에서 왔다. 짧은 약어(AI·IA·VR·PPA)가 영어 단어나
한글 조사에 붙을 때 어떻게 되는지가 이 파일의 대부분이다 — 파이썬 정규식에서 한글은
\\w 라 `\\b` 로는 "AI가" 를 못 잡고, 경계를 아예 안 두면 SAID·FAIL·IAEA 에 걸린다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ai_lane  # noqa: E402
import ai_lane_stage0 as stage0  # noqa: E402
import llm_policy  # noqa: E402


def art(title: str, summary: str = "", **extra) -> dict:
    return {"title": title, "summary": summary, **extra}


class PrefilterTermTests(unittest.TestCase):
    def test_real_positives_pass(self):
        for title in (
            "Tianwan puts AI to work",
            "Atomic Canyon launches virtual assistant for US nuclear fleet",
            "AI streamlines Blykalla's SEALER licensing",
            "Argonne designs molten salt-based heat exchanger with ML-based monitoring for reactor",
            "Nuclearn launches Equipment AI for nuclear plants",
        ):
            with self.subTest(title=title):
                self.assertTrue(ai_lane.prefilter(art(title, "nuclear reactor"))["passed"])

    def test_korean_particle_after_ai_is_caught(self):
        # "AI가" — \b 로는 못 잡는 자리.
        verdict = ai_lane.prefilter(art("日 도쿄전력, AI가 원전 서류 80% 줄였다"))
        self.assertTrue(verdict["passed"])
        self.assertIn("AI", verdict["ai_terms"])

    def test_english_words_containing_ai_do_not_count(self):
        for title in ("Operator SAID reactor restart is on track",
                      "Pump FAIL alarm at nuclear plant", "OpenAI and nuclear analogy",
                      "IAEA mission to reactor site", "AIDS research reactor isotopes"):
            with self.subTest(title=title):
                self.assertEqual(ai_lane.ai_terms(title), [], title)

    def test_lowercase_ai_is_not_a_signal(self):
        # 이름·단어 속 'ai'(Kansai, Ai Weiwei)는 약어가 아니다.
        self.assertEqual(ai_lane.ai_terms("Kansai Electric restarts Takahama"), [])

    def test_french_ia_and_japanese_terms(self):
        self.assertIn("IA", ai_lane.ai_terms("EDF déploie l'IA dans ses centrales"))
        self.assertTrue(ai_lane.prefilter(art("原発の点検に人工知能を活用", "原子力"))["passed"])

    def test_needs_both_ai_and_nuclear(self):
        self.assertFalse(ai_lane.prefilter(art("Google launches new AI model"))["passed"])
        self.assertFalse(ai_lane.prefilter(art("Reactor restart approved"))["passed"])

    def test_vendor_name_alone_is_an_ai_signal(self):
        self.assertTrue(ai_lane.prefilter(art("Nuclearn signs Duke Energy nuclear fleet deal"))["passed"])

    def test_hints_mark_but_do_not_drop(self):
        verdict = ai_lane.prefilter(art("Amazon signs 20-year PPA with nuclear plant for AI data centers"))
        self.assertTrue(verdict["passed"])
        self.assertIn("datacenter_power", verdict["hints"])

    def test_short_hint_tokens_use_boundaries(self):
        self.assertNotIn("robotics", ai_lane.hints("AI ARE nuclear VRAM"))
        self.assertIn("robotics", ai_lane.hints("AI and VR training for nuclear staff"))
        self.assertIn("diplomatic", ai_lane.hints("MOU on AI and nuclear"))

    def test_tags_and_topics_are_not_read(self):
        # 메인 LLM 이 붙인 태그를 다시 세면 '메인이 AI 라고 봤는가'를 재는 꼴이 된다.
        article = art("Reactor outage extended", tags=["#AI"], topics=["datacenter_ai"])
        self.assertFalse(ai_lane.prefilter(article)["passed"])


class JudgmentContractTests(unittest.TestCase):
    def test_request_carries_index_title_and_source(self):
        system, user = ai_lane.build_request([
            art("Tianwan puts AI to work", "톈완 원전 AI", publisher="NEI", title_kr="톈완 AI"),
            art("Second", publisher="WNN"),
        ])
        self.assertIs(system, ai_lane.SYSTEM_PROMPT)
        self.assertIn("[0] Tianwan puts AI to work", user)
        self.assertIn("한글 제목: 톈완 AI", user)
        self.assertIn("[1] Second", user)
        self.assertIn("출처: WNN", user)

    def test_prompt_lists_every_enum_value(self):
        for value in (ai_lane.EXCLUDE_REASONS + ai_lane.APPLICATION_AREAS + ai_lane.AI_TECH
                      + ai_lane.ACTOR_TYPES + ai_lane.MATURITY):
            with self.subTest(value=value):
                self.assertIn(value, ai_lane.SYSTEM_PROMPT)

    def test_every_enum_value_has_a_korean_label(self):
        for value in (ai_lane.EXCLUDE_REASONS + ai_lane.APPLICATION_AREAS + ai_lane.AI_TECH
                      + ai_lane.ACTOR_TYPES + ai_lane.MATURITY):
            with self.subTest(value=value):
                self.assertIn(value, ai_lane.LABELS_KR)

    def test_conflicting_scope_and_reason_resolves_to_exclude(self):
        out = ai_lane.normalize_judgment({"in_scope": True, "exclude_reason": "datacenter_power"})
        self.assertFalse(out["in_scope"])
        self.assertEqual(out["exclude_reason"], "datacenter_power")

    def test_unknown_values_are_dropped_or_defaulted(self):
        out = ai_lane.normalize_judgment({
            "in_scope": True, "exclude_reason": "none", "application_area": "space",
            "ai_tech": "quantum", "actor_type": "alien", "maturity": "rumor",
            "adoption_significance": 9, "countries": ["us", 3, "jp"]})
        self.assertEqual(out["application_area"], "")
        self.assertEqual(out["ai_tech"], "other")
        self.assertEqual(out["actor_type"], "other")
        self.assertEqual(out["maturity"], "announced")
        self.assertEqual(out["adoption_significance"], 3)
        self.assertEqual(out["countries"], ["US", "JP"])

    def test_exclude_without_reason_is_not_ai_use(self):
        out = ai_lane.normalize_judgment({"in_scope": False})
        self.assertEqual(out["exclude_reason"], "not_ai_use")

    def test_parse_response_keeps_order_and_marks_missing(self):
        payload = {"items": [{"idx": 1, "in_scope": False, "exclude_reason": "fusion"},
                             {"idx": 1, "in_scope": True},  # 중복 idx 는 첫 답을 쓴다
                             {"idx": 7, "in_scope": True}]}
        out = ai_lane.parse_response(payload, 3)
        self.assertIsNone(out[0])
        self.assertEqual(out[1]["exclude_reason"], "fusion")
        self.assertIsNone(out[2])

    def test_failed_batch_stays_none_not_excluded(self):
        # 실패를 '제외'로 바꾸면 다시 묻지 않게 된다 — 메인 레인의 sent 마킹 사고와 같은 꼴.
        def boom(system, user):
            raise RuntimeError("quota")
        out = ai_lane.judge([art("a"), art("b")], call=boom, batch_size=1)
        self.assertEqual(out, [None, None])

    def test_judge_batches_and_uses_injected_call(self):
        calls = []

        def fake(system, user):
            calls.append(user)
            n = user.count("\n\n") + 1
            return {"items": [{"idx": i, "in_scope": False, "exclude_reason": "fusion"}
                              for i in range(n)]}
        out = ai_lane.judge([art(str(i)) for i in range(10)], call=fake, batch_size=4)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(out), 10)
        self.assertTrue(all(o and o["exclude_reason"] == "fusion" for o in out))

    def test_policy_profile_is_registered(self):
        self.assertTrue(llm_policy.profile(ai_lane.POLICY_TASK).model())


class Stage0Tests(unittest.TestCase):
    def test_stratum_assignment(self):
        self.assertEqual(stage0.stratum_of("overseas", []), "overseas_core")
        self.assertEqual(stage0.stratum_of("overseas", ["robotics"]), "overseas_core")
        self.assertEqual(stage0.stratum_of("overseas", ["datacenter_power"]), "overseas_datacenter")
        self.assertEqual(stage0.stratum_of("kr", []), "domestic_core")
        self.assertEqual(stage0.stratum_of("kr", ["fusion"]), "domestic_hinted")
        self.assertEqual(stage0.stratum_of("", []), "unscoped")

    def test_extract_candidates_dedups_hash_and_title(self):
        rows = [
            {"hash": "a", "title": "Tianwan puts AI to work", "summary": "reactor", "scope": "overseas",
             "archived_at": "2026-09-01T00:00:00"},
            {"hash": "a", "title": "Tianwan puts AI to work", "summary": "reactor", "scope": "overseas"},
            {"hash": "b", "title": "Tianwan puts AI to work", "summary": "reactor", "scope": "overseas"},
            {"hash": "c", "title": "Reactor outage", "summary": "", "scope": "overseas",
             "archived_at": "2026-09-02T00:00:00"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-09.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            cands, meta = stage0.extract_candidates(Path(tmp))
        self.assertEqual([c["id"] for c in cands], ["a"])
        self.assertEqual(meta["archive_records"], 4)
        self.assertEqual(meta["archive_days"], 2)

    def test_sample_is_deterministic_and_full_for_core(self):
        cands = ([{"id": f"o{i}", "stratum": "overseas_core", "pub": "2026-09-01"} for i in range(5)]
                 + [{"id": f"d{i}", "stratum": "domestic_core", "pub": "2026-09-01"} for i in range(50)])
        plan = {"overseas_core": None, "domestic_core": 10}
        first, weights = stage0.draw_sample(cands, plan, seed=1)
        second, _ = stage0.draw_sample(cands, plan, seed=1)
        self.assertEqual([r["id"] for r in first], [r["id"] for r in second])
        self.assertEqual(weights["overseas_core"]["sampled"], 5)
        self.assertEqual(weights["domestic_core"]["weight"], 5.0)

    def test_baseline_rules(self):
        self.assertEqual(stage0.baseline_predict({"scope": "kr"})["exclude_reason"], "domestic")
        self.assertEqual(stage0.baseline_predict(
            {"scope": "overseas", "hints": ["datacenter_power"]})["exclude_reason"], "datacenter_power")
        self.assertFalse(stage0.baseline_predict(
            {"scope": "overseas", "hints": ["robotics"], "ai_terms": ["AI"]})["in_scope"])
        self.assertTrue(stage0.baseline_predict(
            {"scope": "overseas", "hints": ["robotics"], "ai_terms": ["computer vision"]})["in_scope"])

    def test_evaluate_counts_cells_and_missing(self):
        gold = [{"id": "1", "in_scope": True, "maturity": "deployed"},
                {"id": "2", "in_scope": True},
                {"id": "3", "in_scope": False, "exclude_reason": "fusion"},
                {"id": "4", "in_scope": False, "exclude_reason": "domestic"},
                {"id": "5", "in_scope": False, "exclude_reason": "fusion"}]
        pred = [{"id": "1", "in_scope": True, "maturity": "deployed"},
                {"id": "2", "in_scope": False, "exclude_reason": "fusion"},
                {"id": "3", "in_scope": True},
                {"id": "4", "in_scope": False, "exclude_reason": "fusion"}]
        out = stage0.evaluate(gold, pred)
        self.assertEqual(out["confusion"], {"tp": 1, "fp": 1, "fn": 1, "tn": 1})
        self.assertEqual(out["missing"], 1)
        self.assertEqual(out["precision"], 0.5)
        self.assertEqual(out["recall"], 0.5)
        self.assertEqual(out["negative_reason_agreement"], 0.0)
        self.assertEqual(out["field_agreement_on_tp"]["maturity"], 1.0)

    def test_population_estimate_weights_strata(self):
        gold = [{"id": "1", "in_scope": True, "stratum": "a"},
                {"id": "2", "in_scope": True, "stratum": "b"}]
        weights = {"a": {"population": 10, "sampled": 10, "weight": 1.0},
                   "b": {"population": 40, "sampled": 10, "weight": 4.0}}
        out = stage0.population_estimate(gold, weights, days=10)
        self.assertEqual(out["estimated_positive_total"], 5.0)
        self.assertEqual(out["estimated_per_day"], 0.5)


class GoldArtifactTests(unittest.TestCase):
    """커밋된 gold 가 스키마를 지키는지. 값이 바뀌는 것은 막지 않는다(사람이 고친다)."""

    GOLD = ROOT / "eval_artifacts" / "ai_lane" / "gold_labels.jsonl"

    def test_gold_uses_controlled_values(self):
        if not self.GOLD.exists():
            self.skipTest("gold 없음")
        for line in self.GOLD.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            with self.subTest(id=row["id"]):
                self.assertIn(row["exclude_reason"], ai_lane.EXCLUDE_REASONS)
                self.assertIn(row["confidence"], ("high", "low"))
                if row["in_scope"]:
                    self.assertEqual(row["exclude_reason"], "none")
                    self.assertIn(row["application_area"], ai_lane.APPLICATION_AREAS)
                    self.assertIn(row["ai_tech"], ai_lane.AI_TECH)
                    self.assertIn(row["actor_type"], ai_lane.ACTOR_TYPES)
                    self.assertIn(row["maturity"], ai_lane.MATURITY)
                    self.assertIn(row["adoption_significance"], (0, 1, 2, 3))
                else:
                    self.assertNotEqual(row["exclude_reason"], "none")


if __name__ == "__main__":
    unittest.main()


class DigitalScopeTests(unittest.TestCase):
    """4단계 — 포함 범위 ②(AI + 현장 디지털 기술)와 러시아어·중국어 어휘."""

    def test_digital_terms_pass_without_ai(self):
        for title in ("영국 셀라필드, 원전 해체 가속화 위한 몰입형 환경 시설 구축",
                      "Framatome unveils digital I&C modernization strategy for nuclear plants",
                      "«Росатом» оснастил АЭС «Руппур» цифровым помощником операторов"):
            with self.subTest(title=title):
                verdict = ai_lane.prefilter(art(title))
                self.assertTrue(verdict["passed"], verdict)
                self.assertEqual(verdict["ai_terms"], [])
                self.assertTrue(verdict["digital_terms"])

    def test_conflict_context_blocks_digital_only_but_not_ai(self):
        drone_attack = ai_lane.prefilter(art("IAEA, 자포리자 원전 부지 드론 피격 사건 발생 확인"))
        self.assertFalse(drone_attack["passed"])
        self.assertEqual(drone_attack["dropped"], "conflict")
        ai_in_conflict = ai_lane.prefilter(art("AI 기반 드론 탐지 체계로 원전 공격 대비 — 운영사 발표"))
        self.assertTrue(ai_in_conflict["passed"])
        self.assertIn("conflict", ai_in_conflict["hints"])

    def test_russian_and_chinese_terms(self):
        ru = ai_lane.prefilter(art("Росатом с помощью ИИ разработал состав материалов для реакторов БРЕСТ"))
        self.assertTrue(ru["passed"])
        self.assertIn("ИИ", ru["ai_terms"])
        zh = ai_lane.prefilter(art("中广核发布核电人工智能大模型，用于核电站智能运维"))
        self.assertTrue(zh["passed"])
        # 키릴 약어도 라틴 약어처럼 경계를 본다 — 단어 속 ИИ 는 아니다.
        self.assertEqual(ai_lane.ai_terms("ЛИНИИ электропередачи АЭС"), [])

    def test_tech_scope_defaults_from_ai_tech(self):
        base = {"in_scope": True, "exclude_reason": "none"}
        self.assertEqual(ai_lane.normalize_judgment({**base, "ai_tech": "vr_immersive"})["tech_scope"], "digital")
        self.assertEqual(ai_lane.normalize_judgment({**base, "ai_tech": "genai_llm"})["tech_scope"], "ai")
        self.assertEqual(ai_lane.normalize_judgment({**base, "ai_tech": "other", "tech_scope": "digital"})["tech_scope"], "digital")
        self.assertEqual(ai_lane.normalize_judgment({**base, "ai_tech": "other", "tech_scope": "nope"})["tech_scope"], "ai")

    def test_prompt_asks_for_english_actor_names_and_scope(self):
        self.assertIn("영어 공식 표기", ai_lane.SYSTEM_PROMPT)
        self.assertIn("tech_scope", ai_lane.SYSTEM_PROMPT)
        for value in ai_lane.TECH_SCOPES:
            self.assertIn(value, ai_lane.LABELS_KR)
