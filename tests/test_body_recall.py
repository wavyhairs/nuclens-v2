"""본문 확보율 개선(2026-10-03)의 계약.

실측(아카이브 140건, 국내 IP): 제목 불일치로 버린 12건 중 9건이 맞는 기사였다.
세 가지 원인과 처방을 잠근다.
  1. <article> 이 여러 개(관련기사 블록)면 가장 긴 것이 아니라 제목과 겹치는 것.
  2. 페이지 제목(og:title)이 기사 제목과 맞으면 본문 겹침 하한을 낮춘다 — 단
     본문 겹침 0은 안 된다(폴리뉴스 사고가 되살아나면 안 된다).
  3. 원 매체가 막으면(운영 IP 403) 네이버 미러로 한 번 더 간다.
그리고 어느 매체가 막는지 로그에 남긴다.
"""

import unittest

import article_body as ab

LEAD = ("전력망 부족 문제로 추진 일정이 다소 늦춰지던 강릉 AI데이터센터 부지로 강릉시 최남단 "
        "옥계면 옥계산업단지로 최종 낙점됐다. 강원특별자치도와 강릉시는 400MW 규모의 데이터센터를 "
        "2028년까지 짓는다고 밝혔다. 옥계산업단지는 송전망이 가깝고 부지가 넓어 유리하다는 평가다. "
        "시는 이달 중 사업자와 협약을 맺고 내년 상반기 착공 절차에 들어갈 계획이다. 지역 주민 설명회도 "
        "연다. 전력 공급은 동해안 송전선로 보강이 끝나는 시점에 맞춘다.")
OTHER = ("개그우먼 심진화가 홈쇼핑에서 1시간 만에 8억원의 매출을 기록했다고 밝혔다. 1일 방송된 "
         "라디오에서 심진화는 출연료만 받는다고 말했다. 방송에는 가수 빽가와 함께 출연해 화제가 됐다. "
         "그는 앞으로도 다양한 방송 활동을 이어갈 계획이라고 덧붙였다. 팬들의 응원이 이어지고 있다. "
         "소속사는 하반기 예능 출연 일정을 조율 중이라고 전했다. 최근 그는 유튜브 채널도 열었다. "
         "구독자는 한 달 만에 십만 명을 넘었다.")


def page(*articles, og_title=""):
    head = f'<head><meta property="og:title" content="{og_title}"></head>' if og_title else ""
    body = "".join(f"<article><p>{text}</p></article>" for text in articles)
    return f"<html>{head}<body>{body}</body></html>"


class ArticleScopeTests(unittest.TestCase):
    def test_longest_article_block_is_not_automatically_the_body(self):
        html = page(OTHER + " " + OTHER, LEAD)   # 남의 기사가 더 길다
        self.assertIn("심진화", ab.extract_text(html))                     # 제목 없으면 예전대로
        chosen = ab.extract_text(html, title="강릉 AI데이터센터 400MW 부지로, ‘옥계’ 확정")
        self.assertIn("옥계산업단지", chosen)
        self.assertNotIn("심진화", chosen)

    def test_single_article_is_unchanged(self):
        self.assertIn("옥계", ab.extract_text(page(LEAD), title="아무 제목"))


class PageTitleAssistTests(unittest.TestCase):
    TITLE = "30조 넣고 수익률 15%?…대미투자 1호, 그래서 얼마 버나"
    BODY = ("정부가 약 30조원을 투입하는 미국 텍사스주 엔시날 가스발전 사업에서 연 15% 안팎의 "
            "내부수익률을 기대하고 있다. 사업은 2029년 상업 운전을 목표로 한다.")

    def test_page_title_rescues_a_colloquial_headline(self):
        self.assertFalse(ab.matches_title(self.BODY, self.TITLE))          # 본문만으론 3/8
        self.assertTrue(ab.matches_title(self.BODY, self.TITLE, page_title=self.TITLE))

    def test_page_title_alone_cannot_pass_a_foreign_body(self):
        """페이지는 맞는데 본문 범위가 남의 기사면 여전히 버린다."""
        self.assertFalse(ab.matches_title(OTHER, self.TITLE, page_title=self.TITLE))

    def test_polinews_incident_stays_rejected(self):
        """2026-08-10 라이브 사고: 해외건설 본문이 원전 부지 기사에 붙었다. 그 페이지의
        og:title 은 해외건설 제목이므로 완화 규칙으로 되살아나지 않는다."""
        body = ("국내 건설사들이 올해 해외건설 수주 500억달러 목표 달성을 위해 대형 프로젝트 "
                "확보에 나서고 있다. 원전과 발전·전력 인프라 등으로 수주 분야를 넓히는 모습이다.")
        self.assertFalse(ab.matches_title(
            body, "한수원, 신규 대형 원전 및 SMR 부지 후보지 선정",
            page_title="해외건설 500억 달러 시대 겨냥…K건설, 중동 플랜트서 원전·전력 선회"))

    def test_extract_page_title_prefers_og_title(self):
        html = '<head><title>사이트 | 제목B</title><meta property="og:title" content="제목A &amp; 더"></head>'
        self.assertEqual(ab.extract_page_title(html), "제목A & 더")
        self.assertEqual(ab.extract_page_title("<head><title> 제목B </title></head>"), "제목B")
        self.assertEqual(ab.extract_page_title("<p>없음</p>"), "")


class NaverMirrorTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, text="", status=200):
            self.text = text
            self.status_code = status
            self.encoding = "utf-8"
            self.apparent_encoding = "utf-8"

    class FakeSession:
        def __init__(self, pages, statuses):
            self.pages, self.statuses, self.requested = pages, statuses, []

        def get(self, url, **kwargs):
            self.requested.append(url)
            return NaverMirrorTests.FakeResponse(self.pages.get(url, ""), self.statuses.get(url, 200))

    ORIGIN = "https://www.press.example/a"
    MIRROR = "https://n.news.naver.com/mnews/article/001/0001"
    TITLE = "강릉 AI데이터센터 400MW 부지로 옥계 확정"

    def _session(self, origin_status):
        return self.FakeSession({self.MIRROR: page(LEAD, og_title=self.TITLE)},
                                {self.ORIGIN: origin_status})

    def test_blocked_origin_falls_back_to_the_mirror(self):
        session = self._session(403)
        bodies, stats = ab.fetch_bodies(
            [{"hash": "h1", "link": self.ORIGIN, "title": self.TITLE, "naver_link": self.MIRROR}],
            workers=1, session_factory=lambda: session)
        self.assertIn("옥계", bodies["h1"])
        self.assertEqual(stats["reasons"], {"ok_naver": 1})
        self.assertEqual(session.requested, [self.ORIGIN, self.MIRROR])
        self.assertNotIn("failed_domains", stats)

    def test_no_mirror_means_the_old_failure(self):
        session = self._session(403)
        bodies, stats = ab.fetch_bodies(
            [{"hash": "h1", "link": self.ORIGIN, "title": self.TITLE}],
            workers=1, session_factory=lambda: session)
        self.assertEqual(bodies, {})
        self.assertEqual(stats["reasons"], {"http_403": 1})
        self.assertEqual(stats["failed_domains"], {"http_403": {"press.example": 1}})
        self.assertIn("[body] http_403: press.example(1)", ab.format_stats(stats))

    def test_mirror_failure_is_visible_in_the_status(self):
        session = self.FakeSession({}, {self.ORIGIN: 403, self.MIRROR: 404})
        _bodies, stats = ab.fetch_bodies(
            [{"hash": "h1", "link": self.ORIGIN, "title": self.TITLE, "naver_link": self.MIRROR}],
            workers=1, session_factory=lambda: session)
        self.assertEqual(stats["reasons"], {"http_403>naver_http_404": 1})
        self.assertEqual(stats["failed_domains"], {"http_403": {"press.example": 1}})

    def test_title_mismatch_does_not_try_the_mirror(self):
        """원 페이지가 열렸는데 제목과 안 맞는 것은 차단이 아니다. 미러로 가면
        같은 판정을 두 번 하거나 다른 기사를 받을 뿐이다."""
        session = self.FakeSession({self.ORIGIN: page(OTHER)}, {})
        _bodies, stats = ab.fetch_bodies(
            [{"hash": "h1", "link": self.ORIGIN, "title": self.TITLE, "naver_link": self.MIRROR}],
            workers=1, session_factory=lambda: session)
        self.assertEqual(stats["reasons"], {"title_mismatch": 1})
        self.assertEqual(session.requested, [self.ORIGIN])


class NaverLinkRetentionTests(unittest.TestCase):
    def test_collector_keeps_the_mirror_without_changing_identity(self):
        from datetime import datetime, timezone
        from email.utils import format_datetime
        from unittest.mock import patch
        import news_bot as nb
        pub = format_datetime(datetime.now(timezone.utc))
        items = [{"originallink": "https://www.press.example/a?utm_source=x",
                  "link": "https://n.news.naver.com/mnews/article/001/0001?sid=100",
                  "title": "원전 수출 확대", "description": "한수원이 원전 수출을 확대한다고 밝혔다. " * 3,
                  "pubDate": pub},
                 {"originallink": "https://www.press.example/b",
                  "link": "https://www.press.example/b",
                  "title": "원전 안전 점검", "description": "원안위가 원전 안전 점검에 나섰다고 밝혔다. " * 3,
                  "pubDate": pub}]
        with patch.object(nb, "search_naver", return_value=items),              patch.object(nb, "article_seen", return_value=False),              patch.object(nb, "passes_anchor_filter", return_value=True),              patch.object(nb, "is_promotional", return_value=False),              patch.object(nb, "is_stub", return_value=False),              patch.object(nb, "source_score", return_value=10),              patch.object(nb.time, "sleep", lambda *_: None):
            got = nb.collect_articles("naver", ["원전"], [], {"sent": {}})
        by_title = {a["title"]: a for a in got}
        a = by_title["원전 수출 확대"]
        self.assertEqual(a["link"], nb.normalize_url("https://www.press.example/a?utm_source=x"))
        self.assertEqual(a["naver_link"], "https://n.news.naver.com/mnews/article/001/0001?sid=100")
        self.assertNotIn("naver_link", by_title["원전 안전 점검"])

if __name__ == "__main__":
    unittest.main()
