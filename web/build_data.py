"""원자력 뉴스 아카이브를 이슈 중심 웹 데이터로 빌드한다.

원본 봇 저장소는 읽기만 한다. 이 프로토타입 디렉터리의 ``public/data``에만
결과를 쓴다. ``BOT_DIR`` 환경 변수로 원본 봇 저장소 위치를 지정할 수 있다.

출력:
  - news.json: 기사 발행일 기준 전체 피드
  - briefings.json: 발송일 기준 브리핑 + 이슈 묶음
  - issues.json: 전체 기간에서 중복 제거한 고유 이슈 카탈로그
  - trend.json: 집계 데이터
  - insights.json: 봇이 생성한 흐름 해석
  - issue_audit.json: 날짜 간 이슈 연결 근거와 차단 진단
  - meta.json: 생성 시각, 건수, 통제 태그 커버리지

이슈 묶음은 외부 API를 호출하지 않는 보수적 MVP다. 제목·태그 유사도가 충분히
높을 때만 합치며, 불확실하거나 계산에 실패한 기사는 단독 이슈로 남긴다.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_quality import (  # noqa: E402
    clean_text,
    curation_errors,
    implication_is_hollow,
    display_publisher,
    invalid_url_reason,
    normalize_event_date_fields,
    normalize_url,
    source_profile,
    source_url,
    split_title_publisher,
    title_key,
)
from embedding_pipeline import EMBEDDING_MODEL, cached_vector  # noqa: E402
import article_quality_gate  # noqa: E402
import event_calendar  # noqa: E402
import issue_candidate_stats  # noqa: E402
import issue_insight  # noqa: E402
import issue_review  # noqa: E402
import keei_match  # noqa: E402
import story_cluster  # noqa: E402
import story_fingerprint  # noqa: E402
import story_identity  # noqa: E402
import weekly_sections  # noqa: E402
from web.publication_policy import (  # noqa: E402
    PUBLICATION_RELEVANCE_VALUES,
    publication_drop_reason,
    publication_relevance,
)
from web.publication_title import gist_adds_nothing, strip_org_prefix  # noqa: E402

try:
    # Actions 파이프에서는 stdout이 블록 버퍼링된다. 타임아웃으로 죽은 8/26
    # 실행은 6분 동안 내부 진행 로그가 한 줄도 보이지 않아 느린 계산과 정지를
    # 구분할 수 없었다. 모든 단계 로그를 즉시 내보낸다.
    sys.stdout.reconfigure(
        encoding="utf-8", errors="replace", line_buffering=True, write_through=True
    )
except (AttributeError, ValueError):
    pass

SITE_DIR = Path(__file__).resolve().parent
# 봇 저장소 web/ 아래로 이식됨 (2026-08-01) — 기본값은 저장소 루트(부모 폴더).
# 프로토타입 원본 위치에서 쓸 때는 BOT_DIR 환경 변수로 지정.
BOT_DIR = Path(os.environ.get("BOT_DIR", SITE_DIR.parent))
OUT_DIR = Path(os.environ.get("OUTPUT_DIR", SITE_DIR / "public" / "data"))
# 운영 콘솔 데이터는 **/data 밖**에 둔다. /data 는 독자 화면이 쓰는 공개 경로라
# 엣지의 접근 통제(functions/admin/_middleware.js)가 닿지 않는다. 화면만 잠그고
# 데이터를 공개 경로에 두면 URL 하나로 그대로 읽힌다 — 잠근 게 아니다.
ADMIN_OUT_DIR = Path(os.environ.get("ADMIN_OUTPUT_DIR", OUT_DIR.parent / "admin" / "data"))
GENERATION_ID = os.environ.get("GENERATION_ID", "")

SHOW_MARKET = False
NEWS_WINDOW_DAYS = 60
# 화면의 원문/이슈 상세는 최근 60일만 유지한다. 분기·반기·연간 흐름은
# delivery_log의 이미 중복 제거된 briefing story를 경량 집계해 별도로 만든다.
LONG_TREND_WINDOW_DAYS = 365
TREND_PERIOD_DAYS = (7, 30, 90, 180, 365)
# 워드 클라우드가 쓰는 태그 수. 표는 12개(순위)면 되지만 구름은 분포를 보이는
# 그림이라 그 정도로는 낱말 사이가 비어 아무 말도 하지 않는다. 40개면 화면 한
# 폭을 채우면서도 페이로드가 기간당 몇 KB 늘어나는 선에서 멈춘다.
TAG_CLOUD_LIMIT = 40
ISSUE_WINDOW_DAYS = 21

# 추적률을 재는 회차 수. **하루치로 재면 안 된다** — 한 회차의 분모가 이슈 8개
# 안팎이라 1건이 붙고 떨어질 때마다 지표가 0.125 씩 튄다. 2026-08-03 실측 17일에서
# 8일이 0.000 이었고, ≥0.20 기준은 10일(59%)에서 실패했다. 병합기 품질과 무관하게
# **뉴스가 한산한 날이 그대로 빨간불**이 된다. 7회차를 함께 보면 분모가 55~60 이라
# 지표가 병합기의 성질을 말하게 된다(같은 실측에서 7일 누적 0.193 / 14일 0.120).
TRACKING_WINDOW_BRIEFINGS = 7
ISSUE_EMBEDDING_THRESHOLD = 0.92
ISSUE_EMBEDDING_CANDIDATE_THRESHOLD = 0.70
LOCAL_EMBEDDING_CANDIDATE_THRESHOLD = 0.18
LOCAL_EMBEDDING_DIMENSION = 1024
STORY_CONTRACT_VERSION = "briefing-story-v2"
# 자동 병합 규칙의 문턱. issue_match_diagnostics 안에 숫자로 박혀 있던 것을 끌어
# 올린다 — 운영 콘솔(/admin)이 "무엇이 걸려서 붙었나"를 화면에 적으려면 규칙과
# 표시가 같은 값을 봐야 한다. 코드에만 있으면 화면이 옛 숫자를 말하게 된다.
# 값은 그대로다. 바꿀 때는 MERGE_RULES 의 설명도 같이 고칠 것.
TITLE_MATCH_RATIO = 0.78
TAGS_MATCH_MIN_SHARED = 2
TAGS_MATCH_TITLE_RATIO = 0.32
TAGS_MATCH_TOKEN_RATIO = 0.20
TITLE_TAGS_MATCH_RATIO = 0.55
FINGERPRINT_MATCH_SIMILARITY = 0.78
FINGERPRINT_MATCH_MIN_COMPARED = 3
FINGERPRINT_MATCH_MIN_SHARED_AXES = 2
# 지문에서 **신원**을 말하는 축(story_fingerprint.IDENTITY_AXES). 나라와
# event_family 는 닫힌 어휘라 여기 없다 — 자세한 실측은 그 모듈 주석에 있다.
FINGERPRINT_MATCH_AXES = story_fingerprint.IDENTITY_AXES
EVIDENCE_PRESELECT_TOP_N = 50
EVIDENCE_RETRIEVAL_POOL = 240
EVIDENCE_VECTOR_TOP_N = 80
EVIDENCE_RETRIEVAL_TERMS = 12
EVIDENCE_RETRIEVAL_MAX_POSTINGS = 240
EVIDENCE_RETRIEVAL_CANARY = 12
EVIDENCE_LSH_BANDS = 6
EVIDENCE_LSH_BITS_PER_BAND = 6

# 화면이 읽는 규칙표. id 는 diagnostics["method"] 값과 1:1 이다.
MERGE_RULES = (
    {"id": "story_id", "label": "사건 식별자",
     "detail": "발송 연속성에서 이어진 동일 story_id (국가·설비 충돌은 계속 차단)"},
    {"id": "title", "label": "제목 유사도",
     "detail": f"제목 유사도 {TITLE_MATCH_RATIO} 이상"},
    {"id": "tags", "label": "공통 태그",
     "detail": f"구체 태그 {TAGS_MATCH_MIN_SHARED}개 공유 + "
               f"제목 {TAGS_MATCH_TITLE_RATIO} 또는 토큰 {TAGS_MATCH_TOKEN_RATIO} 이상"},
    {"id": "title_tags", "label": "태그+제목",
     "detail": f"구체 태그 1개 공유 + 제목 유사도 {TITLE_TAGS_MATCH_RATIO} 이상"},
    {"id": "story_fingerprint", "label": "사건 지문",
     "detail": f"지문 유사도 {FINGERPRINT_MATCH_SIMILARITY} 이상 · "
               f"{FINGERPRINT_MATCH_MIN_COMPARED}축 이상 비교 · "
               f"행위자/대상/원인/행위 중 {FINGERPRINT_MATCH_MIN_SHARED_AXES}축 이상 "
               f"일치하고 어긋난 축이 없음"},
    {"id": "embedding", "label": "임베딩",
     "detail": f"코사인 유사도 {ISSUE_EMBEDDING_THRESHOLD} 이상"},
    {"id": "manual_approved", "label": "사람 승인",
     "detail": "issue_match_overrides.json 에서 승인"},
    {"id": "llm_approved", "label": "LLM 승인",
     "detail": "회색지대(0.84~0.92)를 LLM 검수가 같은 사건으로 판정"},
    {"id": "blocked", "label": "차단",
     "detail": "국가 또는 설비가 충돌해 병합하지 않음"},
    {"id": "fingerprint_chain_blocked", "label": "지문 연쇄 차단",
     "detail": "지문만으로 붙는 자리인데 묶음의 다른 기사와 "
               "행위자/대상/원인/행위가 어긋나 잇지 않음"},
)
# ---- issue_audit 의 두 층 ---------------------------------------------------------
#
# 이 파일은 배포 산출물 가운데 **혼자만 O(쌍)** 이다. 나머지(news·briefings·
# issues)는 60일 창 안의 기사 수에 비례해 선형으로 자라는데, 여기 실리는
# `review_candidates` 는 `기사 × 클러스터 × 최근멤버3` 으로 생긴다. 실측
# 2026-08-21 라이브: 31,679쌍 × 952 B = **28.8 MiB**, 파일의 93.9%. 나머지 전부를
# 합쳐도 0.3 MiB 다. 게다가 후보 판정이 코사인 0.70 부터라 임베딩 캐시가 찰수록
# (41건 → 3,352건) 더 많은 쌍이 점수를 받는다. 기사량이 2배면 후보는 약 4배다.
#
# 그래서 2026-08-21 06:48 UTC 크롤부터 Cloudflare Pages 의 **파일당 25 MiB** 상한에
# 걸려 배포가 통째로 거부됐고, 라이브가 그날 오후까지 굳어 있었다.
#
# 두 층은 크기 법칙이 다르다. 그래서 가른다.
#
#   판정 기록  clusters·matches·overrides·llm 판정·통계·entity_matches
#              → O(이슈 수). 창에 갇혀 있어 유한하다. **배포한다**
#              (data/issue_audit.json). 화면은 안 읽지만 테스트의 데이터 게이트가
#              읽고, 사람이 "왜 붙었나"를 물을 때 필요한 것이 전부 여기 있다.
#
#   후보 덤프  review_candidates 전수
#              → O(기사 × 클러스터). **배포하지 않는다.** web/_audit/ 에 따로 쓰고
#              워크플로가 아티팩트로 올린다. 임계값 실험처럼 전수가 필요한 일은
#              브라우저가 아니라 내려받아서 하는 일이다.
#
# 배포본에도 점수 상위 일부는 남긴다 — 공개 URL 하나로 "지금 경계선에 뭐가 있나"를
# 보던 절차(docs/AS_IS.md)를 끊지 않기 위해서다. 목록은 이미 점수 내림차순이라
# 앞에서 자르면 그대로 상위이고, 전수는 아티팩트에 그대로 있으므로 **잃는 것이 없다.**
#
# 5,000 은 콘솔이 보는 경계선 창(CONSOLE_BORDERLINE_TOTAL)의 20배이고, 한 회차 LLM
# 검수 밴드 전체(실측 2,244쌍)를 담고도 두 배 남는다. 러너 기준 약 4.5 MiB.
AUDIT_REVIEW_CANDIDATE_LIMIT = 5000

# ---- 콘솔이 회차 단위로 보는 창 ---------------------------------------------------
#
# 예전에는 경계선을 **점수 상위 40건**만 실었다. 전역 정렬이라 회차별로 갈라 보면
# 최신 회차가 거의 다 가져가고 과거 회차는 1건씩 남는다 — 실측 2026-08-21: 8월 18일
# 회차의 후보는 30쌍인데 전역 상위 40건 안에는 1쌍뿐이었다. 그 화면에서 "8월 18일
# 경계선 1건"은 **숫자가 거짓말을 하는 것**이다(운영자는 29쌍을 못 본 줄도 모른다).
#
# 그래서 회차마다 상위 몇 건을 싣는다. 전수는 그대로 아티팩트에 있고, 화면에는
# 회차별 **전수 건수**를 따로 실어 "N건 중 M건"이라고 적는다. 여기서 말하는 전수는
# 병합 문턱 바로 아래 구간(_near_merge_threshold)이고, 기록 문턱 위 전부는
# 회차 색인의 `scored` 로 따로 싣는다.
#
# 창은 회차 안에서 **경로마다** 따로 준다(발송 기사 · 미발송 근거). 근거 쪽이
# 압도적으로 많아서 — 실측 2026-08-22 빌드 회차 2,053쌍 대 0쌍 — 한 창에 세우면
# 점수 높은 근거가 창을 통째로 가져가고, 그날 실제로 붙을 뻔한 카드가 화면에서
# 사라진다. 화면도 "경로마다 상위 몇 쌍"이라고 적는다(renderBorderline).
CONSOLE_BORDERLINE_PER_ROUND = 10
CONSOLE_BORDERLINE_TOTAL = 240
# 대표 교체도 같은 이유로 회차별로 자른다(예전에는 전역 최신 60건이었다).
CONSOLE_PROMOTION_PER_ROUND = 24
CONSOLE_PROMOTION_TOTAL = 240
CONSOLE_VETO_PER_ROUND = 24
CONSOLE_VETO_TOTAL = 240

# 전수 덤프가 사는 곳. **web/public 밖이어야 한다** — 그 안이면 admin/data 처럼
# 엣지에서 가려도 wrangler 가 업로드하고, 그러면 25 MiB 벽이 그대로다
# (라이브에서 /admin/data/merges.json 이 404 가 아니라 401 인 것이 그 증거).
AUDIT_FULL_DIR = Path(os.environ.get("AUDIT_OUTPUT_DIR", SITE_DIR / "_audit"))

MATCH_OVERRIDES_FILE = BOT_DIR / "issue_match_overrides.json"
SITE_URL = os.environ.get("SITE_URL", "https://nuclens-v2.pages.dev").rstrip("/")
KST = timezone(timedelta(hours=9))

# 히어로 h1과 변화 문장의 하드 상한. 넘기면 카드가 아니라 문단이 된다.
# 70자는 1280px 히어로에서 두 줄. 요약이 이보다 길면 이슈 제목으로 넘어간다.
HEADLINE_LIMIT = 70
CHANGE_LINE_LIMIT = 140

# 라벨은 판정이 아니라 사실 진술이다. 단일 출처 보도는 결함이 아니라 흔한 정상
# 상태(실측 84%)라서 '일부 확인' 같은 부정 프레이밍을 쓰지 않는다.
VERIFICATION_LABELS = {
    "official": "공식 원문 포함",
    "corroborated": "독립 출처 2곳+",
    "partial": "단일 출처",
    "unverified": "확인 중",
}

_KR_DOMAIN_HINTS = (".kr", "khnp", "nssc", "motie", "kaeri", "kins", "korad", "yna", "korea")
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_NORM_RE = re.compile(r"[^0-9a-z가-힣]")

_FACILITY_NAMES = (
    "신월성", "신한울", "신고리", "후쿠시마", "자포리자", "체르노빌",
    "올킬루오토", "플라망빌", "힝클리", "사이즈웰", "두코바니", "테믈린",
    "세르나보다", "알마라즈", "바라카", "아투차", "엠발세", "보글틀",
    "새울", "고리", "월성", "한빛", "한울", "타이산", "파크스",
)
_FACILITY_PATTERN = "|".join(re.escape(name) for name in sorted(_FACILITY_NAMES, key=len, reverse=True))
_FACILITY_RE = re.compile(_FACILITY_PATTERN, re.IGNORECASE)
_UNIT_RE = re.compile(rf"({_FACILITY_PATTERN})\s*(\d+)\s*호기", re.IGNORECASE)

_GENERIC_TAGS = {
    "원전", "원자력", "에너지", "정책", "에너지정책", "원전정책", "해외원전",
    "국내원전", "산업동향", "시장동향", "기술개발", "국제협력", "안전",
    # 기관명만 같다고 같은 이슈는 아니다. 원안위가 다룬 서로 다른 사건이
    # 한 묶음으로 합쳐지는 false merge를 막는다.
    "원안위", "nssc", "iaea", "한수원", "khnp", "미국nrc", "미국doe", "정부",
}
_TAG_ALIASES = {
    "doe": "미국doe",
    "미에너지부": "미국doe",
    "미국에너지부": "미국doe",
    "nrc": "미국nrc",
    "미원자력규제위원회": "미국nrc",
    "전기본": "전력수급기본계획",
    "12차전기본": "12차전력수급기본계획",
}

_EVENT_REASON_LABELS = {
    "policy_decision": "정책 결정",
    "regulatory_action": "규제 조치",
    "contract_award": "계약 체결",
    "project_milestone": "사업 진전",
    "incident_safety": "안전 사건",
    "corporate_move": "기업 동향",
    "research_report": "연구·보고서",
    "market_signal": "시장 신호",
}

# 기존 자유 태그·제목을 통제 주제로 옮기는 프로토타입용 로컬 분류표. 원본
# 아카이브는 수정하지 않고 생성 JSON에만 적용한다. 규칙은 구체 표현 위주로 두어
# 단순히 "원전"이 들어갔다는 이유만으로 주제를 붙이지 않는다.
_TOPIC_RULES = {
    "fukushima": ("후쿠시마", "alps", "처리수", "오염수"),
    "fusion": ("핵융합", "fusion", "iter", "tokamak", "토카막"),
    "smr": ("smr", "소형모듈", "소형 모듈", "mmr", "마이크로원자로", "advanced reactor"),
    "restart_lto": ("계속운전", "계속 운전", "수명연장", "수명 연장", "재가동", "life extension", "restart"),
    "newbuild": ("신규원전", "신규 원전", "원전건설", "원전 건설", "new nuclear", "nuclear program", "nuclear programme"),
    "fuel_cycle": ("핵연료", "haleu", "우라늄", "uranium", "농축", "연료주기", "fuel cycle"),
    "waste": ("사용후핵연료", "방사성폐기물", "방폐", "고준위", "폐기물 처분", "decommission"),
    "regulation": ("규제", "인허가", "허가 연장", "nrc", "원안위", "nssc", "행정예고", "입법예고", "안전심사"),
    "datacenter_ai": ("데이터센터", "데이터 센터", "ai 전력", "인공지능 전력", "빅테크", "hyperscaler"),
    "power_market": ("전력수급", "전기본", "전력시장", "전력망", "전기요금", "전력 수요", "전력공급"),
    "finance": (
        "원전금융", "프로젝트 금융", "자금조달", "투자계약", "글로벌원전투자", "민간금융",
        "투자 유치", "eib", "대출", "ppa", "전력구매계약",
    ),
    "security_trade": (
        "원전수출", "수출 계약", "원자력협력", "핵협력", "협력 협정", "에너지안보",
        "공급망", "통상", "제재", "양자협정", "안전조치 협정",
    ),
    "operations": ("원전운영", "설비이용률", "운영효율", "가동중단", "장기운전", "wano", "리튜빙", "설비개선", "개보수"),
    "safety": ("원전안전", "핵안전", "안전사고", "화재", "비상대비", "방사선안전"),
    "decommissioning": ("원전해체", "원전 해체", "해체 작업", "폐로"),
    "workforce": ("원전인력", "원전 인력", "인력증가", "인력동향", "전문인력"),
    "policy_general": ("원자력정책", "미국원자력정책", "미국정책", "원자력확대", "에너지전환", "에너지로드맵", "원자력혁신"),
    "research": ("원자력연구", "연구개발", "r&d", "센서기술", "핵과학", "시험 시설", "기술실증"),
    "applications": ("원자력수소", "원자력 기반 수소", "동위원소", "방사선 활용", "핵 과학 활용"),
}

# 국가 코드는 ISO 3166-1 alpha-2를 쓴다. 기업 국적이 아니라 실제 정책 관할,
# 사업 부지, 사건 무대가 텍스트에 드러나는 경우만 추론한다.
_COUNTRY_RULES = {
    "KR": ("한국", "대한민국", "한수원", "khnp", "원안위", "고리", "월성", "한울", "신한울", "새울", "영덕", "경주"),
    "US": (
        "미국", "u.s.", "united states", "미 에너지부", "미 원자력규제위원회", "백악관",
        "로스앨러모스", "패듀카", "사바나강", "오이스터크릭", "화이트메사", "샌디아",
        "텍사스", "버지니아", "아이다호",
    ),
    "CA": ("캐나다", "온타리오", "서스캐처원", "브루스 파워", "달링턴"),
    "FR": ("프랑스", "플라망빌", "팔리", "마르쿨", "카다라슈"),
    "GB": ("영국", "united kingdom", "잉글랜드", "스코틀랜드", "웨일스", "사이즈웰", "힝클리", "헤이샴", "하틀풀"),
    "DE": ("독일", "germany", "도이칠란트", "막스 플랑크", "벤델슈타인"),
    "ES": ("스페인", "spain"),
    "RS": ("세르비아", "serbia"),
    "HU": ("헝가리", "hungary", "팍스 원전"),
    "RO": ("루마니아", "romania", "체르나보다"),
    "CZ": ("체코", "czech", "두코바니", "테멜린"),
    "PL": ("폴란드", "poland"),
    "SE": ("스웨덴", "sweden"),
    "NL": ("네덜란드", "netherlands", "보르셀레"),
    "FI": ("핀란드", "finland", "올킬루오토"),
    "SK": ("슬로바키아", "slovakia", "모호프체"),
    "BG": ("불가리아", "bulgaria", "코즐로두이"),
    "UA": ("우크라이나", "ukraine", "자포리자"),
    "BE": ("벨기에", "belgium"),
    "IT": ("이탈리아", "italy"),
    "PT": ("포르투갈", "portugal"),
    "CH": ("스위스", "switzerland"),
    "NO": ("노르웨이", "norway"),
    "DK": ("덴마크", "denmark"),
    "JP": ("일본", "후쿠시마", "도쿄전력", "tepco"),
    "RU": ("러시아", "russia"),
    "CN": ("중국", "china"),
    "AR": ("아르헨티나", "argentina", "아투차"),
    "IN": ("인도", "india"),
    "AU": ("호주", "australia"),
    "BR": ("브라질", "brazil"),
    "ZA": ("남아공", "남아프리카공화국", "south africa"),
    "SA": ("사우디", "saudi arabia"),
    "AE": ("아랍에미리트", "uae", "바라카"),
    "TR": ("튀르키예", "터키", "turkey", "아쿠유"),
    "KZ": ("카자흐스탄", "kazakhstan"),
    "UZ": ("우즈베키스탄", "uzbekistan"),
}
_EU_INSTITUTION_RULES = (
    "유럽연합", "european union", "eu 집행위", "eu 집행위원회", "유럽위원회",
    "유럽의회", "european commission", "european parliament", "euratom",
)
_EUROPE_REGION_RULES = ("유럽", "범유럽", "europe-wide", "pan-european")
_GLOBAL_RULES = (
    "글로벌", "전 세계", "세계 원자력", "세계원자력", "국제원자력기구", "iaea",
    "world nuclear association", "세계은행", "oecd/nea",
)
_EUROPEAN_COUNTRY_CODES = {
    "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE",
    "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
    "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "RS", "SK",
    "SI", "ES", "SE", "CH", "UA", "GB",
}
_COUNTRY_ALIASES = {"UK": "GB"}
_LEGACY_COUNTRY_BUCKETS = {"EU_ETC", "OTHER"}
_COUNTRY_TOKEN_RULES = {
    "US": ("nrc", "doe", "pjm", "inl", "llnl", "inpo"),
}
_GLOBAL_TOKEN_RULES = ("iter",)


_SOURCE_BACKFILL: dict[str, dict] | None = None


def source_backfill() -> dict[str, dict]:
    """뒤늦게 채운 매체명·실주소(`tools/backfill_sources.py` 산출).

    두 필드는 2026-08-11 수집분부터 붙는다. 그 전 기록은 재크롤이 없어 영영
    비는데, **자료 팩이 그 기록을 인용한다** — 실측 표시 기사 1,136건 중 777건이
    호스트명 매체명이거나 리다이렉트 링크였고 그중 645건이 최근 7일분이었다.
    "오래된 것만 그렇다"가 아니라 지금 읽는 구간이 그랬다.

    아카이브 파일은 건드리지 않는다(append-only 2,500건을 다시 쓰는 것은 되돌리기
    어렵다). 빌드가 매번 전체를 지나가므로 옆에 얹기만 하면 된다.
    """
    global _SOURCE_BACKFILL
    if _SOURCE_BACKFILL is None:
        try:
            raw = json.loads((BOT_DIR / "archive_source_backfill.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        _SOURCE_BACKFILL = raw if isinstance(raw, dict) else {}
    return _SOURCE_BACKFILL


def _normalize_archive_record(record: dict) -> dict:
    """구버전 레코드를 웹 빌드의 현재 출처·사건일 계약으로 읽는다."""
    normalized = dict(record)
    # 레코드가 이미 들고 있으면 그것이 이긴다 — 백필은 빈자리만 메운다.
    filled = source_backfill().get(str(record.get("hash") or "")) or {}
    for field in ("site_name", "resolved_url"):
        if filled.get(field) and not normalized.get(field):
            normalized[field] = filled[field]
    normalized["url"] = normalize_url(record.get("url"))
    title = record.get("title") or ""
    publisher = record.get("publisher") or ""
    domain = record.get("domain") or ""
    if ("news.google." in domain or "news.google." in normalized["url"]) and not publisher:
        title, publisher = split_title_publisher(title)
    profile = source_profile(domain, publisher)
    normalized.update({
        "title": title,
        # 백필로 얻은 매체명이 있으면 호스트명 자리를 대신한다. profile 은 원래
        # publisher 로 이미 계산돼 있어 등급·유형 판정은 흔들리지 않는다.
        "publisher": display_publisher(publisher or profile["publisher"],
                                       normalized.get("site_name") or ""),
        "source_type": record.get("source_type") or profile["source_type"],
        "evidence_role": record.get("evidence_role") or profile["evidence_role"],
        "source_tier": record.get("source_tier") or profile["source_tier"],
    })
    normalized.update(normalize_event_date_fields(record))
    return normalized


def validate_archive_records(records: list[dict]) -> None:
    """중복·오류 URL·불완전 문장이 있으면 배포 빌드를 중단한다."""
    errors: list[str] = []
    seen_urls: dict[str, str] = {}
    seen_titles: dict[str, str] = {}
    for record in records:
        article_hash = record.get("hash") or "(no-hash)"
        url = record.get("url") or ""
        url_error = invalid_url_reason(url)
        if url_error:
            errors.append(f"{article_hash}:url:{url_error}")
        elif url in seen_urls:
            errors.append(f"{article_hash}:duplicate_url:{seen_urls[url]}")
        else:
            seen_urls[url] = article_hash

        normalized_title = title_key(record.get("title"))
        if normalized_title and normalized_title in seen_titles:
            errors.append(f"{article_hash}:duplicate_title:{seen_titles[normalized_title]}")
        elif normalized_title:
            seen_titles[normalized_title] = article_hash

        if record.get("source_tier") not in {1, 2, 3}:
            errors.append(f"{article_hash}:source_tier:missing")
        if not record.get("publisher"):
            errors.append(f"{article_hash}:publisher:missing")
        if record.get("importance") != "noise":
            # v1 아카이브의 완결문은 최대 120자를 허용하되 신규 생성기는 80자
            # 게이트를 적용한다. 과거 문장을 잘라 맞추는 데이터 훼손을 피한다.
            errors.extend(
                f"{article_hash}:{error}"
                for error in curation_errors(record, summary_limit=120)
            )

    if errors:
        preview = " | ".join(errors[:20])
        raise ValueError(f"data quality gate failed ({len(errors)}): {preview}")


def load_archive() -> list[dict]:
    records = []
    archive_dir = BOT_DIR / "archive"
    for path in sorted(archive_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            article_hash = record.get("hash")
            if not article_hash:
                continue
            records.append(_normalize_archive_record(record))
    return records


# 수집·발송 단계가 이미 판정해 기록한 상태. 사이트·RSS 는 그 판정을 다시
# 뒤집지 않는다 — 텔레그램에서 막힌 기사가 웹에서 되살아나면 막은 의미가 없다.
# 여기서 보는 것은 **명시적으로 적힌** 값뿐이다. 옛 레코드에는 이 필드가 없고,
# 없는 것을 추론해 숨기면 정상 기사가 대량으로 사라진다.
SITE_HIDDEN_STATUSES = frozenset({"quarantined"})
# fallback 은 사실이 틀린 것이 아니라 검토를 못 받은 것이라 숨기지 않는다.
# 다만 검토받지 않은 **해석**은 내보내지 않는다 — 사실은 원문이 받쳐 주지만
# 해석은 받쳐 주는 것이 없다. assess_delivery_eligibility 의 limitations 와 같은 목록.
FALLBACK_WITHHELD_FIELDS = ("implication", "why_important", "open_question",
                            "watch_next")


def apply_archive_integrity_gate(records: list[dict]) -> tuple[list[dict], dict]:
    """과거 아카이브도 사이트에 내보내기 직전 같은 원문-큐레이션 계약을 적용한다.

    아카이브에는 기사 본문을 저장하지 않으므로 없는 근거를 오류로 단정하지 않는다.
    원제목만으로도 확인되는 명백한 사건 전환·핵심 수치 충돌은 숨기고, 불가능한
    사건일은 날짜만 비운다. 원본 JSONL은 감사 이력을 위해 다시 쓰지 않는다.

    여기서 다시 판정할 수 없는 것도 있다. 발송 시점에는 있었던 근거(원문 발췌,
    최종 카드 검증)가 아카이브에는 남지 않으므로, 그때 격리된 기사를 제목만으로
    다시 격리해 낼 수는 없다. 그래서 **적혀 있는 상태를 먼저 존중한다.**
    """
    visible: list[dict] = []
    quarantined: list[dict] = []
    sanitized: list[dict] = []
    status_blocked: list[dict] = []
    fallback_trimmed: list[dict] = []
    for record in records:
        status = clean_text(record.get("curation_status")).lower()
        sample = {
            "hash": record.get("hash", ""),
            "title": (record.get("title") or "")[:100],
            "title_kr": (record.get("title_kr") or "")[:100],
            "codes": [],
        }
        if status in SITE_HIDDEN_STATUSES:
            status_blocked.append({**sample, "codes": [f"status:{status}"]})
            continue
        result = article_quality_gate.audit_article_integrity(
            record,
            source={"title": record.get("title", ""),
                    "published_at": record.get("pub") or record.get("archived_at")},
            reference_date=record.get("pub") or record.get("archived_at"),
        )
        sample["codes"] = [finding.code for finding in result.findings]
        if not result.eligible:
            quarantined.append(sample)
            continue
        value = result.value
        if status == "fallback":
            withheld = [field for field in FALLBACK_WITHHELD_FIELDS
                        if clean_text(value.get(field))]
            if withheld:
                value = {**value, **{field: "" for field in withheld}}
                fallback_trimmed.append({**sample, "codes": withheld})
        if result.action == "sanitize":
            sanitized.append(sample)
        visible.append(value)
    return visible, {
        "checked": len(records),
        "quarantined": len(quarantined),
        "sanitized": len(sanitized),
        "status_blocked": len(status_blocked),
        "fallback_trimmed": len(fallback_trimmed),
        "quarantine_samples": quarantined[:20],
        "sanitize_samples": sanitized[:20],
        "status_blocked_samples": status_blocked[:20],
        "fallback_trimmed_samples": fallback_trimmed[:20],
    }


def brief_ranks_by_hash(path: Path | None = None) -> dict[str, int]:
    """기사 hash → 텔레그램 카드 번호(지역별 1부터).

    `daily_brief._item_meta` 가 2026-08-17 부터 `brief_rank` 를 적는다. 그 이전
    발송분에는 없으므로 **파일에 적힌 순서**로 메운다 — `append_delivery_log` 가
    `outbox["items"]`(국내 순서 → 해외 순서)를 그대로 이어 쓰므로 (날짜, 지역)
    안의 등장 순서가 곧 카드 번호다. 완벽한 복원은 아니지만, 없는 값을 0 으로
    두면 오디오가 옛 회차에서 순서를 통째로 잃는다.
    """
    path = path or (BOT_DIR / "delivery_log.jsonl")
    if not path.exists():
        return {}
    ranks: dict[str, int] = {}
    counters: dict[tuple[str, str], int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("record_type"):
            continue
        article_hash = str(row.get("hash") or "")
        day = str(row.get("date") or "")
        if not article_hash or not day:
            continue
        region = str(row.get("brief_region") or row.get("region") or "")
        explicit = row.get("brief_rank")
        if isinstance(explicit, int) and explicit > 0:
            ranks[article_hash] = explicit
            counters[(day, region)] = max(counters.get((day, region), 0), explicit)
            continue
        counters[(day, region)] = counters.get((day, region), 0) + 1
        ranks[article_hash] = counters[(day, region)]
    return ranks


def load_deliveries() -> dict[str, dict]:
    """기사 hash별 마지막 발송 메타를 읽는다.

    발송일만 배지처럼 사용하지 않고 점수 내역과 함께 보존한다. 동일 기사가 다시
    발송된 경우 마지막 정상 레코드를 사용한다.
    """
    out: dict[str, dict] = {}
    path = BOT_DIR / "delivery_log.jsonl"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            delivery = json.loads(line)
        except json.JSONDecodeError:
            continue
        # record_type 이 붙은 줄은 기사가 아니라 부가 레코드(selection_stats).
        if delivery.get("record_type"):
            continue
        article_hash = delivery.get("hash")
        briefing_date = delivery.get("date")
        if article_hash and briefing_date:
            out[article_hash] = delivery
    return out


# 선정 통계는 hash 가 없어 (date, hash) 멱등이 안 걸린다. 워크플로 재실행이 같은
# 날짜에 여러 줄을 남기므로 읽는 쪽에서 하나를 고른다.
#   ① pipeline_status 가 좋은 것 우선 (실패한 재실행이 정상 기록을 덮지 않게)
#   ② 같은 등급이면 generated_at 이 늦은 것
_PIPELINE_RANK = {"ok": 3, "partial": 2, "error": 1}


def pick_selection_stats(rows: list[dict]) -> dict[str, dict]:
    """날짜 → 그날의 대표 selection_stats 레코드."""
    best: dict[str, dict] = {}
    for row in rows:
        if row.get("record_type") != "selection_stats":
            continue
        day = row.get("date") or ""
        if not day:
            continue
        current = best.get(day)
        if current is None or _stats_key(row) > _stats_key(current):
            best[day] = row
    return best


def _stats_key(row: dict) -> tuple:
    return (_PIPELINE_RANK.get(row.get("pipeline_status") or "", 0),
            row.get("generated_at") or "")


# 상태 판정은 두 개의 독립 heartbeat 로 한다.
#
#   수집기      = 아카이브 최신 archived_at (crawl 이 매시간 append — 선정과 무관)
#   브리핑 파이프라인 = selection_stats.generated_at + pipeline_status
#
# "최신 기사 날짜"만 보고 판정하면 안 된다. 선정 하한을 도입한 뒤에는 며칠간 새
# 브리핑 항목이 없는 게 정상일 수 있고, 그걸 장애로 표시하면 컷오프 도입의 취지가
# 무너진다. **콘텐츠가 없는 것과 프로세스가 안 돈 것은 별개다.**
COLLECTOR_STALE_HOURS = 6      # crawl 은 매시간 — 6시간이면 확실히 멈춘 것
BRIEFING_STALE_HOURS = 36      # daily-brief 는 하루 1회 — 36시간이면 한 회차를 건너뛴 것


def _latest_archive_stamp(records: list[dict]) -> str:
    stamps = [str(r.get("archived_at") or "") for r in records if r.get("archived_at")]
    return max(stamps) if stamps else ""


def _hours_since(stamp: str, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() / 3600.0


def system_status(records: list[dict], selection_stats: dict, now: datetime) -> dict:
    """status.json 본문. app.js renderSystemStatus 가 이 계약을 이미 렌더한다."""
    ok_days = {day: row for day, row in selection_stats.items()
               if row.get("pipeline_status") == "ok"}
    last_ok = max((row.get("generated_at") or "" for row in ok_days.values()),
                  default="")
    latest_brief = max(selection_stats) if selection_stats else ""
    latest_row = selection_stats.get(latest_brief) or {}

    collector_age = _hours_since(_latest_archive_stamp(records), now)
    briefing_age = _hours_since(latest_row.get("generated_at") or "", now)

    state, message, watcher = "ok", "", True

    if collector_age is not None and collector_age > COLLECTOR_STALE_HOURS:
        state, watcher = "error", False
        message = f"수집이 {collector_age:.0f}시간째 멈춰 있습니다"
    elif latest_row.get("pipeline_status") == "error":
        state = "error"
        message = "브리핑 선정이 실패했습니다"
    elif briefing_age is None and selection_stats:
        watcher = False
        message = "브리핑 실행 기록을 찾지 못했습니다"
    elif briefing_age is not None and briefing_age > BRIEFING_STALE_HOURS:
        watcher = False
        message = f"브리핑이 {briefing_age / 24:.0f}일째 갱신되지 않았습니다"
    elif latest_row.get("pipeline_status") == "partial":
        message = "브리핑 일부가 발송되지 않았습니다"

    return {
        "state": state,
        # 마지막 '정상 브리핑' 시각. 빌드 시각이 아니다 — 빌드는 실패한 날에도 돈다.
        # 통계가 아직 없는 구간(기능 도입 직후)에서는 수집 시각으로 내려간다.
        "last_success_at": last_ok or _latest_archive_stamp(records) or now.isoformat(),
        "watcher_running": watcher,
        "message": message,
        "collector_stamp": _latest_archive_stamp(records),
        "briefing_date": latest_brief,
    }


def load_selection_stats() -> dict[str, dict]:
    path = BOT_DIR / "delivery_log.jsonl"
    if not path.exists():
        return {}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return pick_selection_stats(rows)


def infer_region(record: dict, countries: list[str] | None = None,
                 country_source: str = "") -> tuple[str, str]:
    """기사의 대상 지역을 수집 경로가 아니라 기사 내용 기준으로 정규화한다.

    명시적인 scope가 있으면 우선 사용한다. 그 외에는 국가 태그를 우선하고,
    국가를 특정하지 못한 경우에만 section과 domain을 보조 신호로 사용한다.
    Google News 한국 도메인에 실린 해외 기사까지 국내로 잡히던 오류를 막는다.
    """
    # 사람이 나라를 고쳤으면 `scope` 는 건너뛴다. scope 는 큐레이션이 명시할 때만
    # 채워지는 신뢰 낮은 필드인데(실측 157건 중 148건이 None) 여기 맨 앞에 있어서,
    # 고쳐 놓은 나라가 옛 scope 하나에 다시 덮이고 있었다.
    if country_source != "manual-repair":
        scope = (record.get("scope") or "").lower()
        if scope == "kr":
            return "국내", "scope"
        if scope == "overseas":
            return "해외", "scope"

    confident_countries = {
        str(country).strip().upper()
        for country in (countries or [])
        if str(country).strip().upper() not in {"", "OTHER"}
    }
    if confident_countries:
        return ("국내" if "KR" in confident_countries else "해외"), "countries"

    section = (record.get("section") or "").lower()
    if section in {"domestic", "khnp"}:
        return "국내", "section"
    if section in {"international", "overseas", "global"}:
        return "해외", "section"

    domain = (record.get("domain") or "").lower()
    return (
        "국내" if any(hint in domain for hint in _KR_DOMAIN_HINTS) else "해외",
        "domain",
    )


def region_of(record: dict, countries: list[str] | None = None,
              country_source: str = "") -> str:
    return infer_region(record, countries, country_source)[0]


def date_of(record: dict) -> str:
    for key in ("pub", "archived_at"):
        value = record.get(key) or ""
        try:
            return datetime.fromisoformat(value).astimezone(KST).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    return ""


def selection_reasons(delivery: dict | None, source: dict | None = None) -> list[str]:
    """내부 점수 내역을 카드용 설명 배지 최대 2개로 바꾼다.

    복원된 회차(tools/restore_v1_briefing.py)는 breakdown 이 없다 — v1 이 배포한
    published data 에 점수 내역이 없어서, 지어내는 대신 비워 두기로 했다. 대신
    그날 실제로 화면에 나갔던 문구가 레코드에 실려 오므로 그걸 그대로 쓴다.
    breakdown 이 있는 정상 회차는 예전과 똑같이 아래 계산을 탄다.
    """
    if not delivery:
        return []
    restored = delivery.get("selection_reasons")
    if isinstance(restored, list) and restored:
        return [str(reason) for reason in restored if str(reason).strip()][:2]
    breakdown = delivery.get("breakdown") or {}
    reasons: list[str] = []

    event_rows = []
    for key, value in breakdown.items():
        if not key.startswith("event:"):
            continue
        event = key.split(":", 1)[1]
        label = _EVENT_REASON_LABELS.get(event)
        if label:
            event_rows.append((float(value or 0), label))
    if event_rows:
        reasons.append(max(event_rows)[1])

    if source and source.get("evidence_role") == "primary":
        reasons.append("공식 원문")
    elif source and source.get("source_type") == "specialist_media" and float(
        breakdown.get("source_tier1") or 0
    ) > 0:
        reasons.append("전문 매체")
    elif float(breakdown.get("korea_relevance") or 0) >= 2.4:
        reasons.append("국내 관련성 높음")
    elif float(breakdown.get("policy_materiality") or 0) >= 2:
        reasons.append("정책 영향 큼")
    elif float(breakdown.get("evidence_strength") or 0) >= 1.6:
        reasons.append("근거 강도 높음")

    if not reasons and delivery.get("score") is not None:
        reasons.append("브리핑 우선순위")
    return list(dict.fromkeys(reasons))[:2]


def _canonical_tag(tag: object) -> str:
    value = str(tag or "").strip().lstrip("#").lower().replace(" ", "")
    return _TAG_ALIASES.get(value, value)


def _taxonomy_text(record: dict) -> str:
    values = [
        record.get("title_kr") or record.get("title") or "",
        record.get("title") or "",
        record.get("summary") or "",
        record.get("implication") or "",
        record.get("section") or "",
        " ".join(str(tag).lstrip("#") for tag in (record.get("tags") or [])),
    ]
    return " ".join(values).lower()


def infer_topics(record: dict) -> tuple[list[str], str]:
    native = [str(topic) for topic in (record.get("topics") or []) if str(topic).strip()]
    if native:
        return list(dict.fromkeys(native))[:3], "native"

    text = _taxonomy_text(record)
    topics = [topic for topic, needles in _TOPIC_RULES.items() if any(needle in text for needle in needles)]

    event_type = ((record.get("features") or {}).get("event_type") or "").strip()
    if event_type == "regulatory_action" and "regulation" not in topics:
        topics.append("regulation")
    if event_type == "incident_safety" and "safety" not in topics:
        topics.append("safety")
    if event_type == "policy_decision" and not topics:
        topics.append("policy_general")
    if event_type == "research_report" and not topics:
        topics.append("research")
    if (record.get("section") or "").lower() == "smr" and "smr" not in topics:
        topics.append("smr")
    return topics[:3], "heuristic-v1" if topics else "unclassified"


def _country_scopes_from_text(text: str) -> list[str]:
    """텍스트에서 국가와 명시적 지역 범위를 서로 다른 축으로 판정한다."""
    concrete = [
        country
        for country, needles in _COUNTRY_RULES.items()
        if any(needle in text for needle in needles)
        or any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
            for token in _COUNTRY_TOKEN_RULES.get(country, ())
        )
    ]
    if len(concrete) > 2:
        # 0~2개 스키마에서 임의의 두 국가만 남기지 않는다. 유럽 국가만으로 된
        # 다국가 기사면 지리적 유럽, 그 밖의 다국가 기사면 글로벌로 올린다.
        scopes = [
            "EUROPE" if set(concrete).issubset(_EUROPEAN_COUNTRY_CODES) else "GLOBAL"
        ]
    else:
        scopes = concrete

    if any(needle in text for needle in _EU_INSTITUTION_RULES):
        scopes.append("EU")
    if scopes:
        return list(dict.fromkeys(scopes))[:2]
    if any(needle in text for needle in _EUROPE_REGION_RULES):
        return ["EUROPE"]
    if any(needle in text for needle in _GLOBAL_RULES) or any(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
        for token in _GLOBAL_TOKEN_RULES
    ):
        return ["GLOBAL"]
    return []


_COUNTRY_REPAIRS: dict[str, list[str]] | None = None


def country_repairs() -> dict[str, list[str]]:
    """`archive_repairs.json` 의 `countries` 수선 — 사람이 판정을 뒤집는 자리.

    큐레이션이 나라를 틀리게 붙이는 일은 드물지만(실측 1,037건 중 GLOBAL+KR
    동시 태그 2건) 한 건이 세 군데를 동시에 망가뜨린다: ①지역이 국내로 바뀌어
    국내 풀에서 경쟁하고 ②국가 지도의 한국 칸을 부풀리고 ③같은 사건을 다룬
    미국 기사와 한 이슈로 묶일 때 **국경 충돌**로 잡혀 배포 게이트를 막는다.

    드문 오판은 규칙을 풀어서 고치지 않는다 — 이 저장소가 이슈 병합에서 얻은
    원칙 그대로다("틀린 것이 판정이면 판정을 고친다"). 그 목적의 파일이 이미
    있으니 거기에 적는다.

    아카이브를 다시 쓰지 않는다(`--migrate-quality` 는 파일을 통째로 갈아엎는
    유지보수 명령이다). 빌드가 매번 아카이브 전체를 지나가므로 여기서 얹으면
    과거분까지 즉시 반영된다.
    """
    global _COUNTRY_REPAIRS
    if _COUNTRY_REPAIRS is None:
        try:
            raw = json.loads((BOT_DIR / "archive_repairs.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        _COUNTRY_REPAIRS = {
            key: [str(code).strip().upper() for code in entry["countries"]
                  if str(code).strip()]
            for key, entry in (raw.items() if isinstance(raw, dict) else [])
            if isinstance(entry, dict) and isinstance(entry.get("countries"), list)
        }
    return _COUNTRY_REPAIRS


def infer_countries(record: dict) -> tuple[list[str], str]:
    repaired = country_repairs().get(str(record.get("hash") or ""))
    if repaired:
        return repaired, "manual-repair"
    text = _taxonomy_text(record)
    raw_native = [
        str(country).strip().upper()
        for country in (record.get("countries") or [])
        if str(country).strip()
    ]
    if raw_native:
        has_legacy_bucket = bool(set(raw_native) & _LEGACY_COUNTRY_BUCKETS)
        normalized = [_COUNTRY_ALIASES.get(country, country) for country in raw_native]

        # EU_ETC/OTHER는 과거의 모호한 묶음이다. 기존 동반 태그까지 신뢰하지 않고
        # 제목·요약의 실제 관할/부지를 기준으로 전체 범위를 다시 판정한다.
        if has_legacy_bucket:
            refined = _country_scopes_from_text(text)
            return (refined or ["UNSPECIFIED"]), "legacy-refined-v2"

        # 과거 EU 태그가 단순한 '유럽' 기사에도 쓰였다. EU 기관·공동정책이
        # 명시되지 않으면 국가 또는 지리적 EUROPE 범위로 바로잡는다.
        if "EU" in normalized and not any(needle in text for needle in _EU_INSTITUTION_RULES):
            concrete_native = [country for country in normalized if country != "EU"]
            refined = list(dict.fromkeys(concrete_native + _country_scopes_from_text(text)))[:2]
            return (refined or ["UNSPECIFIED"]), "eu-refined-v2"

        deduped = list(dict.fromkeys(normalized))[:2]
        source = "native-normalized-v2" if deduped != list(dict.fromkeys(raw_native))[:2] else "native"
        return deduped, source

    countries = _country_scopes_from_text(text)
    if not countries:
        countries = ["KR"] if region_of(record) == "국내" else ["UNSPECIFIED"]
    return countries, "heuristic-v2"


# 브리핑이 이만큼 있는 주만 주제 추이에 쓴다. 시작 주(2일)와 진행 중인 주는
# 모수가 작아서 화살표가 보도량이 아니라 달력을 말한다.
TOPIC_WEEK_MIN_BRIEFING_DAYS = 6

# 잔여 버킷은 추이에서 뺀다. 둘 다 `classify_topics` 에서 **다른 주제가 하나도
# 안 붙었을 때만** 달리는 폴백이다(`if ... and not topics`). 그래서 이 둘의
# 증감은 그 주제가 늘고 줄었다는 뜻이 아니라 분류기가 얼마나 못 맞췄나를 말한다.
# 실측 2026-08-08 라이브: '원자력 정책 ▼ 16% → 1%' — 정책 보도가 사라진 게
# 아니라 구체 주제로 더 잘 붙은 것뿐이었다.
TOPIC_TREND_EXCLUDED = {"policy_general", "research"}


def _iso_week(date_str: str) -> str:
    year, week, _ = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()
    return f"{year}-W{week:02d}"


def build_topic_weeks(
    issue_catalog: list[dict],
    briefing_dates: list[str],
    limit: int = 6,
    window: int = 8,
) -> tuple[list[str], dict[str, list[int]]]:
    """주제별 주간 **이슈** 수. 커버리지가 온전한 주만 남긴다.

    원래는 archive 레코드의 (기사 × 주제) 쌍을 셌다. 실측 2026-08-08 주별 합계
    22 / 59 / 86 / 580 — 그 주 실제 이슈는 73건인데 한 주제가 185건으로 떴다.
    아카이브가 최근 2주만 밀도 있고 `topics` 필드도 분류기 도입 이후 레코드에만
    붙어 있어서, 화살표가 원자력 보도량이 아니라 수집량 변화를 말하고 있었다.

    이슈 단위로 다시 세면 14 / 51 / 52 / 58 이 되고, 남는 왜곡은 부분 주뿐이라
    브리핑 6일 미만인 주를 버리면 51 / 52 / 58 만 남는다. 한 이슈는 살아 있던
    주마다 1건으로 세고, 주제가 여럿이면 주제별로 1건씩 — 합계는 이슈 수보다
    클 수 있다(`countries_30d` 와 같은 규칙).
    """
    days: Counter = Counter()
    for date_str in briefing_dates:
        if date_str:
            days[_iso_week(date_str)] += 1
    full = {week for week, count in days.items()
            if count >= TOPIC_WEEK_MIN_BRIEFING_DAYS}

    by_week: dict[str, Counter] = defaultdict(Counter)
    for issue in issue_catalog:
        topics = [topic for topic in (issue.get("topics") or [])
                  if topic and topic not in TOPIC_TREND_EXCLUDED]
        first, last = issue.get("first_seen") or "", issue.get("last_seen") or ""
        if not topics or not first or not last:
            continue
        day, end = datetime.strptime(first, "%Y-%m-%d"), datetime.strptime(last, "%Y-%m-%d")
        alive = set()
        while day <= end:
            alive.add(_iso_week(day.strftime("%Y-%m-%d")))
            day += timedelta(days=1)
        for week in alive & full:
            by_week[week].update(topics)

    weeks = sorted(by_week)[-window:]
    totals: Counter = Counter()
    for week in weeks:
        totals.update(by_week[week])
    series = {
        topic: [by_week[week].get(topic, 0) for week in weeks]
        for topic, _ in totals.most_common(limit)
    }
    return weeks, series


def count_country_issues(issues: list[dict], since_date: str) -> Counter:
    """기간 내 연결 이슈를 국가·지역별로 중복 없이 센다.

    같은 이슈의 기사가 여러 번 보도돼도 한 국가에는 1건만 더한다. 한 이슈가
    복수 국가에 걸치면 해당 국가마다 1건씩 집계하므로 전체 합은 이슈 수보다 클 수 있다.
    """
    counts = Counter()
    for issue in issues:
        scopes = {
            country
            for member in issue.get("members", [])
            if (member.get("article_date") or "") >= since_date
            for country in (member.get("countries") or [])
        }
        counts.update(scopes)
    return counts


def _strong_tags(article: dict) -> set[str]:
    tags = set(article.get("canonical_tags") or [])
    if not tags:
        tags = {_canonical_tag(tag) for tag in article.get("tags") or []}
    return {tag for tag in tags if tag and tag not in _GENERIC_TAGS}


def _title_norm(article: dict) -> str:
    title = (article.get("title_kr") or article.get("title") or "").lower()
    return _NORM_RE.sub("", title)


def _tokens(article: dict) -> set[str]:
    # Daily Brief에서 하나의 story로 합친 다른 제목/요약도 issue 연결의 보조 lexical
    # 증거로 사용한다. 대표 제목 하나만 보면 사실기사↔분석기사처럼 표현이 달라진 같은
    # 사건이 다시 갈라질 수 있다. 이 토큰은 자동 병합의 단독 근거가 아니고 보조 신호다.
    parts = [article.get("title_kr") or article.get("title") or "", article.get("summary") or ""]
    parts.extend(str(x) for x in (article.get("story_related_titles") or [])[:6])
    for ctx in (article.get("story_context") or [])[:3]:
        if isinstance(ctx, dict):
            parts.append(ctx.get("summary") or ctx.get("detail") or "")
    text = " ".join(str(x or "") for x in parts)
    return {token.lower()[:8] for token in _TOKEN_RE.findall(text) if len(token) >= 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _text_tokens(text: object) -> set[str]:
    return {token.lower()[:8] for token in _TOKEN_RE.findall(str(text or "")) if len(token) >= 2}


_EVENT_STAGE_PATTERNS = {
    "mou": r"(?:mou|moa|양해각서)",
    "agreement": r"(?:협약|합의|계약|서명)",
    "approval": r"(?:승인|허가|인가|의결)",
    "review": r"(?:검토|심사|심의|평가)",
    "construction": r"(?:착공|건설\s*(?:시작|개시))",
    "grid": r"(?:전력망|계통).{0,8}(?:연결|접속)",
    "criticality": r"(?:첫\s*)?임계",
    "operation": r"(?:상업\s*운전|가동\s*(?:개시|시작))",
    "shutdown": r"(?:가동|운전).{0,8}(?:중단|정지)",
    "launch": r"(?:출범|설립)",
    "declaration": r"(?:선포|공표)",
}
_TENTATIVE_RE = re.compile(r"(?:검토|예정|계획|전망|가능성|수\s*있)", re.I)
_FINAL_RE = re.compile(r"(?:최종|공식|결정|의결|체결|서명|달성|개시|시작|선포)", re.I)


def _event_stage_signature(text: object) -> tuple[frozenset[str], str]:
    raw = str(text or "").lower()
    stages = {
        name for name, pattern in _EVENT_STAGE_PATTERNS.items()
        if re.search(pattern, raw, re.I)
    }
    certainty = "tentative" if _TENTATIVE_RE.search(raw) and not _FINAL_RE.search(raw) else "final"
    return frozenset(stages), certainty


def _normalized_sentence(text: object) -> str:
    normalized = str(text or "").lower()
    normalized = re.sub(r"양해각서", "mou", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return _NORM_RE.sub("", normalized)


# 이번 빌드에서 뺀 빈껍데기 해석. 조용히 지우면 큐레이션 프롬프트가 망가진 것을
# 아무도 모른다 — 빌드 끝에 건수를 찍는다.
_HOLLOW_IMPLICATIONS: list[str] = []


def _is_restatement(before: object, after: object, threshold: float = 0.45) -> bool:
    """두 문장이 같은 사실을 다시 쓴 것인지 판단한다.

    후속 보도의 요약과 직전 브리핑의 요약이 표현만 다른 같은 사실인 경우가 잦다.
    이때 '이전 → 현재'로 이어 붙이면 같은 내용을 두 번 읽히므로 변화로 취급하지
    않는다. 임계값 0.45는 봇의 패러프레이즈 dedup과 같은 기준이다.
    """
    left = _text_tokens(before)
    right = _text_tokens(after)
    if not left or not right:
        return True
    before_stage, before_certainty = _event_stage_signature(before)
    after_stage, after_certainty = _event_stage_signature(after)
    if before_stage and after_stage and (
        before_stage != after_stage or before_certainty != after_certainty
    ):
        return False
    if _jaccard(left, right) >= threshold:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter & longer) / len(shorter) >= 0.8:
        return True

    # 같은 이슈 안에서 MOU·승인·임계처럼 사건 단계가 동일하고 문장 골격도
    # 겹치면 후속 기사로 표현만 바뀐 재진술이다. 반대로 검토/예정에서
    # 의결/착공/가동으로 넘어간 경우에는 certainty나 stage가 달라져 보존된다.
    if before_stage and before_stage == after_stage and before_certainty == after_certainty:
        left_sentence = _normalized_sentence(before)
        right_sentence = _normalized_sentence(after)
        similarity = difflib.SequenceMatcher(None, left_sentence, right_sentence).ratio()
        token_overlap = len(shorter & longer) / len(shorter)
        return similarity >= 0.4 or token_overlap >= 0.35
    return False


def split_interpretation(record: dict) -> tuple[str, str]:
    """AI 해석 두 줄을 각자의 축으로 내보낸다 → (implication, why_important).

    `implication`(시사점, 60자, 전 등급)과 `why_important`(왜 중요, 150자,
    must_read 전용)는 큐레이션 프롬프트가 서로 다른 축으로 만든 문장이다. 그런데
    이 빌드가 `implication or why_important` 로 둘을 하나로 뭉개고 있었다.
    아카이브 실측(2026-08-04, must_read 55건): 둘 다 있는 22건은 긴 쪽이 통째로
    버려졌고, why_important 만 있는 19건은 그 문장이 시사점 라벨을 달고 나갔다.
    정상은 55건 중 1건이었다 (docs/2026-08-04-gap-review.md).

    둘이 같은 사실을 다시 쓴 날은 한 줄만 남긴다 — 블록이 둘로 늘면 읽을 거리가
    늘었다는 신호인데 같은 문단이면 그 신호가 거짓이 된다. 남기는 쪽은 **긴
    쪽**이다. 짧은 쪽을 남기면 지금 고치려는 그 손실이 그대로 남는다.
    """
    implication = str(record.get("implication") or "").strip()
    why_important = str(record.get("why_important") or "").strip()
    # 빈껍데기 해석은 화면에서 뺀다. 봇의 큐레이션 단계에서도 같은 게이트가 돌지만
    # (news_bot.drop_hollow_implication) 아카이브에 이미 쌓인 옛 문장은 그 게이트를
    # 거치지 않았다 — 여기서 한 번 더 걸러야 다음 빌드에 바로 정리된다.
    # 카드는 implication 이 비면 summary 로 물러나므로(app.js issueCard) 빈칸이
    # 되는 것이 아니라 'AI' 배지가 붙은 무정보 문장이 사라지는 것이다.
    if implication_is_hollow(implication):
        _HOLLOW_IMPLICATIONS.append(implication)
        implication = ""
    if implication and why_important and _is_restatement(implication, why_important):
        if len(implication) >= len(why_important):
            return implication, ""
        return "", why_important
    return implication, why_important


def pick_report_topic(members: list[dict]) -> str:
    """이 이슈의 기사 중 '보고서 검토 추천'을 받은 것이 있으면 그 주제.

    추천은 기사 단위로 붙는데 화면은 이슈 단위다. 한 이슈에 추천 기사가 둘일
    일은 거의 없지만(하루 최대 2건) 있으면 최신 기사 쪽을 쓴다.
    """
    for member in sorted(members, key=lambda m: str(m.get("article_date") or ""), reverse=True):
        topic = str(member.get("report_pick") or "").strip()
        if topic:
            return topic
    return ""


def pick_report_metadata(members: list[dict]) -> tuple[str, str, list[str]]:
    """최신 보고서 추천의 주제·선정 이유·분석 각도를 함께 보존한다."""
    for member in sorted(members, key=lambda m: str(m.get("article_date") or ""), reverse=True):
        topic = str(member.get("report_pick") or "").strip()
        if not topic:
            continue
        why = str(member.get("report_pick_why") or "").strip()
        angles = [
            str(angle).strip() for angle in (member.get("report_pick_angles") or [])
            if str(angle).strip()
        ][:3]
        return topic, why, angles
    return "", "", []


def load_embeddings_cache() -> dict[str, list[float]]:
    """현행 Gemini 모델의 임베딩 캐시만 읽기 전용으로 정규화한다.

    진단 한 줄을 반드시 남긴다. 파이프라인이 coverage 1.0 을 보고하는데도
    ``embedding_cache_entries`` 가 0 으로 나오는 상태를 2026-08-03 에 만났고,
    경로·모델명·파일 존재 중 무엇이 어긋났는지 로그가 없어 가릴 수 없었다.
    빈 dict 는 '파일이 없다'와 '전부 모델 불일치로 탈락했다'를 구분하지 못한다.
    """
    path = Path(os.environ.get("EMBEDDINGS_FILE", BOT_DIR / "embeddings.json"))
    diag: dict[str, object] = {"path": str(path), "exists": path.exists(),
                               "model_wanted": EMBEDDING_MODEL}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        diag["error"] = f"{type(exc).__name__}: {exc}"
        print("[build_data:embeddings] " + json.dumps(diag, ensure_ascii=False))
        return {}
    embeddings: dict[str, list[float]] = {}
    models_seen: Counter = Counter()
    for article_hash, payload in raw.items():
        if isinstance(payload, dict):
            models_seen[str(payload.get("model"))] += 1
        vector = cached_vector(payload, model=EMBEDDING_MODEL)
        if vector:
            embeddings[str(article_hash)] = vector
    diag.update(raw_entries=len(raw), accepted=len(embeddings),
                models_seen=dict(models_seen.most_common(5)))
    print("[build_data:embeddings] " + json.dumps(diag, ensure_ascii=False))
    return embeddings


def _local_embedding_features(article: dict) -> Counter:
    """API 장애 때도 후보 탐색을 계속할 수 있는 언어 독립 특징 벡터."""
    features = Counter()
    title = _title_norm(article)
    summary = _NORM_RE.sub("", str(article.get("summary") or "").lower())
    for ngram_size, weight in ((2, 1.6), (3, 2.2), (4, 1.2)):
        for index in range(max(0, len(title) - ngram_size + 1)):
            features[f"t{ngram_size}:{title[index:index + ngram_size]}"] += weight
    for index in range(max(0, len(summary) - 3 + 1)):
        features[f"s3:{summary[index:index + 3]}"] += 0.45
    for token in _tokens(article):
        features[f"w:{token}"] += 1.0
    for tag in _strong_tags(article):
        features[f"tag:{tag}"] += 4.0
    for topic in article.get("topics") or []:
        features[f"topic:{topic}"] += 2.2
    return features


def build_local_embeddings(articles: list[dict]) -> dict[str, list[float]]:
    """문자 n-gram TF-IDF를 feature hashing해 21일 후보 탐색 벡터를 만든다.

    Gemini 벡터와 다른 공간이므로 둘을 섞지 않는다. 이 로컬 벡터는 낮은 임계값
    후보를 만드는 데만 쓰고, 자동 병합은 기존 보수 규칙/Gemini가 담당한다.
    """
    feature_rows = {
        str(article.get("hash") or ""): _local_embedding_features(article)
        for article in articles if article.get("hash")
    }
    document_frequency = Counter()
    for features in feature_rows.values():
        document_frequency.update(features.keys())
    total = max(1, len(feature_rows))
    embeddings = {}
    for article_hash, features in feature_rows.items():
        vector = [0.0] * LOCAL_EMBEDDING_DIMENSION
        for feature, term_weight in features.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % LOCAL_EMBEDDING_DIMENSION
            inverse_frequency = math.log((1 + total) / (1 + document_frequency[feature])) + 1.0
            vector[index] += float(term_weight) * inverse_frequency
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            embeddings[article_hash] = [value / norm for value in vector]
    return embeddings


AUDIT_FULL_NAME = "issue_audit.full.json"


def shipped_issue_audit(audit: dict) -> dict:
    """배포용 issue_audit. 검수 후보만 상위 N 건으로 줄인 **사본**이다.

    원본은 건드리지 않는다 — `build_admin_merges` 와 `meta` 가 전수를 세야 하고,
    `write_full_issue_audit` 이 전수를 그대로 파일로 남긴다.

    자른 사실은 숫자로 남긴다. 개수가 조용히 줄면 다음 사람이 "후보가 왜
    이것뿐이지"를 잘못된 곳에서 찾게 된다.
    """
    rows = audit.get("review_candidates") or []
    trimmed = len(rows) > AUDIT_REVIEW_CANDIDATE_LIMIT
    shipped = {
        **audit,
        "review_candidates": rows[:AUDIT_REVIEW_CANDIDATE_LIMIT] if trimmed else rows,
        "review_candidate_total": len(rows),
        # 배포본만 보고 있는 사람에게 나머지가 어디 있는지 알려 준다.
        "review_candidates_truncated": trimmed,
        "review_candidates_full_artifact": AUDIT_FULL_NAME if trimmed else "",
    }
    if trimmed:
        print(f"[build_data] issue_audit 검수 후보 {len(rows)}건 → 배포본에는 상위 "
              f"{AUDIT_REVIEW_CANDIDATE_LIMIT}건 (Cloudflare 파일 상한 25 MiB). "
              f"전수는 {AUDIT_FULL_DIR / AUDIT_FULL_NAME} 에 있고 워크플로가 "
              f"아티팩트로 올린다.")
    return shipped


def report_candidate_diagnostics(diagnostics: dict) -> None:
    """후보 진단을 로그 한 덩어리로. **평소엔 조용하고 위험할 때만 크게 말한다.**

    수치는 늘 두 줄로 요약하고, 컷의 여유가 사라졌을 때만 `::warning::` /
    `::error::` 를 얹는다. 사람이 매번 아티팩트를 열어 보게 하면 결국 아무도
    안 보기 때문이다 — 알림이 왔을 때만 열면 되게 만든다.

    종료 코드는 바꾸지 않는다. 배포를 막는 게이트가 아니다(data_gate_metrics
    머리말의 원칙과 같다). 운영 알림으로 밀어 넣는 것은 data_gate_metrics 다.
    """
    bands = diagnostics.get("bands") or {}
    total = bands.get("total") or 0
    if not total:
        return
    band_rows = bands.get("review_band_count") or 0
    print(f"[issue_audit] 검수 후보 {total}건 "
          f"(evidence {bands.get('evidence_share', 0):.1%}) → "
          f"LLM 밴드 {issue_review.REVIEW_BAND_LOW}~{issue_review.REVIEW_BAND_HIGH} "
          f"안은 {band_rows}건 ({band_rows / total:.1%})")
    for row in bands.get("by_band") or []:
        print(f"    {row['band']:>18}  {row['count']:>6}건 ({row['share']:>6.2%})  "
              f"evidence {row['evidence']:>5} / card {row['card']:>5}")
    for space in diagnostics.get("search_space") or []:
        rank = space.get("preselect_rank") or {}
        canary = space.get("retrieval_canary") or {}
        print(f"    [{space.get('path')}] 이슈방문 {space.get('issue_visits'):,} → "
              f"클러스터비교 {space.get('clusters_compared'):,} → "
              f"쌍채점 {space.get('pairs_scored'):,} | "
              f"색인후보 {space.get('index_candidates') or 0:,}/"
              f"전체 {space.get('index_corpus_total') or 0:,} "
              f"(최대 {space.get('index_candidate_max') or 0}) | "
              f"기사당 비교 평균 {(space.get('clusters_per_article') or {}).get('mean')} "
              f"(최대 {(space.get('clusters_per_article') or {}).get('max')}) | "
              f"어휘예선 정답 순위 중앙 {rank.get('median')} · p99 {rank.get('p99')} · "
              f"최대 {rank.get('max')} (표본 {rank.get('landed')}) | "
              f"canary 누락 자동 {canary.get('auto_missed') or 0} · "
              f"LLM {canary.get('review_missed') or 0}")
    for guard in diagnostics.get("guards") or []:
        level = "error" if guard.get("severity") == "critical" else "warning"
        print(f"::{level}::[{guard.get('id')}] {guard.get('title')} — {guard.get('detail')}")


def artifact_ready_block(shipped: dict, full_path: Path,
                         diagnostics: dict | None = None) -> str:
    """전수 덤프가 실제로 만들어졌는지를 **로그 한 곳**에서 확인할 수 있게 한다.

    워크플로의 업로드 스텝은 `if-no-files-found: ignore` 라 파일이 없어도 조용히
    지나간다. 그 침묵 때문에 PR #42 병합 뒤 며칠이 지나도록 아티팩트가 한 번도
    안 만들어진 것을 아무도 몰랐다. 그래서 개수·크기·경로를 한 블록으로 찍는다.

    `full_path` 는 **워크플로의 `path:` 와 대조할 수 있는 값**이어야 한다 —
    다르면 업로드가 빈손으로 끝난다. 아티팩트 안에서는 이 파일이 최상위에
    놓인다(upload-artifact 가 공통 조상을 루트로 잡는다).
    """
    diagnostics = diagnostics or {}
    bands = diagnostics.get("bands") or {}
    try:
        relative = full_path.resolve().relative_to(ROOT_DIR).as_posix()
    except (ValueError, OSError):
        relative = full_path.as_posix()
    size = full_path.stat().st_size if full_path.exists() else 0
    lines = [
        "[issue_audit] artifact-ready",
        f"full_candidates={shipped.get('review_candidate_total', 0)}",
        f"shipped_candidates={len(shipped.get('review_candidates') or [])}",
        f"full_size={size / 1024 / 1024:.1f}MiB",
        f"truncated={str(bool(shipped.get('review_candidates_truncated'))).lower()}",
        f"full_path={relative}",
        f"band_{issue_review.REVIEW_BAND_LOW}_{issue_review.REVIEW_BAND_HIGH}="
        f"{bands.get('review_band_count', 0)}",
        f"evidence_share={bands.get('evidence_share', 0)}",
        f"guards={len(diagnostics.get('guards') or [])}",
    ]
    return "\n".join(lines)


def publish_artifact_ready(shipped: dict, full_path: Path,
                           diagnostics: dict | None = None) -> str:
    """위 블록을 빌드 로그와 (있으면) GitHub 실행 요약 양쪽에 남긴다."""
    block = artifact_ready_block(shipped, full_path, diagnostics)
    print(block)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(f"\n```\n{block}\n```\n")
        except OSError as exc:  # 요약 기록 실패가 빌드를 죽이면 안 된다
            print(f"[issue_audit] 실행 요약 기록 실패: {exc}")
    return block


def write_full_issue_audit(audit: dict) -> Path:
    """전수 후보 덤프를 배포 경로 **밖**에 쓴다. 아티팩트로 올라갈 파일이다.

    배포본에서 잘려 나간 것이 여기 그대로 있어야 '잘랐다'가 '버렸다'가 되지 않는다.
    """
    AUDIT_FULL_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIT_FULL_DIR / AUDIT_FULL_NAME
    path.write_text(
        json.dumps(audit, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"[build_data] issue_audit 전수 {len(audit.get('review_candidates') or [])}건 "
          f"→ {path} ({path.stat().st_size / 1024 / 1024:.1f} MiB, 배포 대상 아님)")
    return path


def _pair_id(left_hash: object, right_hash: object) -> str:
    left, right = sorted((str(left_hash or ""), str(right_hash or "")))
    return f"{left}--{right}"


def load_match_overrides(path: Path = MATCH_OVERRIDES_FILE) -> dict[str, set[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}

    # 운영 콘솔에서 누른 잇기·끊기를 저장소 파일과 **같은 통**에 붓는다. 둘을
    # 따로 두면 "왜 안 붙었나"를 두 곳에서 찾아야 하고, 한쪽만 보고 규칙을 고치게 된다.
    console = {"approved": [], "rejected": []}
    try:
        import admin_overrides  # noqa: PLC0415

        console = admin_overrides.issue_pair_overrides()
    except Exception as exc:  # noqa: BLE001 — 덧칠 실패가 빌드를 세우면 안 된다
        print(f"[admin] 이슈 판정 덧칠 실패 → 저장소 파일만 사용: {exc}")

    def keys(name: str) -> set[str]:
        result = set()
        for row in list(raw.get(name) or []) + list(console.get(name) or []):
            if isinstance(row, str) and "--" in row:
                result.add(row)
            elif isinstance(row, dict):
                result.add(_pair_id(row.get("left_hash"), row.get("right_hash")))
        return {value for value in result if not value.startswith("--") and not value.endswith("--")}

    return {"approved": keys("approved"), "rejected": keys("rejected")}


# ---- 편집 override -------------------------------------------------------------
#
# 알고리즘 결과에 사람이 최종 판단을 얹는 자리. 텔레그램 브리핑은 이른 아침
# (04:05 시작) 무인 발송이라 개입할 창이 없지만, 웹은 발송 뒤에도 고칠 수 있다 —
# 잘못 올라온 카드를 내리고 놓친 이슈를 올리는 게 실제로 가능한 유일한 지점이다.
#
# 적용은 반드시 2단계다.
#   ① 클러스터링 전 — promote 대상에 briefing_date 를 주입한다. 브리핑 이슈는
#      '발송된 기사'에서만 나오므로(delivery_log 조인), 이걸 안 하면 미발송 기사는
#      배열에 없어서 정렬로는 절대 올릴 수 없다.
#   ② 클러스터링 후 — hash 가 속한 **이슈 클러스터 전체**에 적용한다. 기사 하나만
#      건드리면 같은 클러스터의 다른 멤버가 briefing_date 를 갖고 있어 카드가 그대로
#      남는다. 사용자에게 보이는 단위가 이슈 카드이므로 판정 단위도 이슈여야 한다.
SELECTION_OVERRIDES_FILE = BOT_DIR / "selection_overrides.json"

HIDE_ACTION = "hide_from_today"
DEMOTE_ACTIONS = {HIDE_ACTION, "demote_only"}


def _short_hash(value: object) -> str:
    return str(value or "").strip().lower()[:8]


def load_selection_overrides(path: Path = SELECTION_OVERRIDES_FILE) -> dict:
    """{'promote': {(hash8, date): reason}, 'demote': {(hash8, date): action}}.

    date 는 필수다. 없으면 한 번 승격한 이슈가 몇 달 뒤에도 맨 위에 남는다.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    promote: dict[tuple[str, str], str] = {}
    demote: dict[tuple[str, str], str] = {}
    skipped = 0
    for name, sink in (("promote", promote), ("demote", demote)):
        for row in raw.get(name) or []:
            if not isinstance(row, dict):
                skipped += 1
                continue
            key = _short_hash(row.get("hash8") or row.get("hash"))
            day = str(row.get("date") or "").strip()
            if len(key) < 8 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                skipped += 1
                continue
            if name == "demote":
                action = str(row.get("action") or HIDE_ACTION)
                sink[(key, day)] = action if action in DEMOTE_ACTIONS else HIDE_ACTION
            else:
                sink[(key, day)] = str(row.get("reason") or "")
    # 같은 hash 가 양쪽에 있으면 demote 가 이긴다 — 실수로 내리는 쪽이
    # 실수로 올리는 쪽보다 안전하다.
    conflicts = set(promote) & set(demote)
    for key in conflicts:
        promote.pop(key, None)
    if skipped or conflicts:
        print(f"[overrides] 무시 {skipped}건 (hash8/date 누락) / "
              f"promote·demote 충돌 {len(conflicts)}건 → demote 우선")
    return {"promote": promote, "demote": demote, "matched": set()}


def apply_promotions(visible: list[dict], overrides: dict) -> int:
    """1단계 — promote 대상을 그날 브리핑 후보로 끌어올린다(클러스터링 전)."""
    promote = overrides.get("promote") or {}
    if not promote:
        return 0
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for item in visible:
        by_hash[_short_hash(item.get("hash"))].append(item)
    count = 0
    for (key, day), _reason in promote.items():
        for item in by_hash.get(key, []):
            if item.get("briefing_date") != day:
                item["briefing_date"] = day
                item["promoted_by_editor"] = True
            overrides["matched"].add((key, day))
            count += 1
    return count


def override_verdict(members: list[dict], briefing_date: str, overrides: dict) -> str:
    """2단계 — 이슈 클러스터 단위 판정. '' | 'promote' | 'hide' | 'demote'.

    한 클러스터에 promote 와 demote 가 섞이면 demote 가 이긴다(로더와 같은 원칙).
    """
    keys = {(_short_hash(m.get("hash")), briefing_date) for m in members}
    demote = overrides.get("demote") or {}
    promote = overrides.get("promote") or {}
    hit_demote = [demote[k] for k in keys if k in demote]
    hit_promote = [k for k in keys if k in promote]
    for key in keys:
        if key in demote or key in promote:
            overrides["matched"].add(key)
    if hit_demote:
        if hit_promote:
            print(f"[overrides] {briefing_date} 한 이슈에 promote·demote 공존 → demote 적용")
        return "hide" if HIDE_ACTION in hit_demote else "demote"
    return "promote" if hit_promote else ""


def report_unmatched_overrides(overrides: dict) -> None:
    """없는 hash 는 조용히 무시하되 흔적은 남긴다 — 오타를 영영 모르면 안 된다."""
    everything = set(overrides.get("promote") or {}) | set(overrides.get("demote") or {})
    missing = sorted(everything - (overrides.get("matched") or set()))
    if missing:
        preview = ", ".join(f"{h}@{d}" for h, d in missing[:5])
        print(f"[overrides] 해당 날짜 데이터에 없는 항목 {len(missing)}건 무시: {preview}")


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if not left_norm or not right_norm:
        return None
    return dot / (left_norm * right_norm)


def _facility_signature(article: dict) -> tuple[set[str], set[str]]:
    text = " ".join([
        str(article.get("title_kr") or article.get("title") or ""),
        " ".join(str(tag).lstrip("#") for tag in (article.get("tags") or [])),
    ]).lower()
    plants = {match.group(0).lower() for match in _FACILITY_RE.finditer(text)}
    units = {
        f"{match.group(1).lower()}-{match.group(2)}"
        for match in _UNIT_RE.finditer(text)
    }
    return plants, units


def _facility_conflict(left: dict, right: dict) -> bool:
    left_plants, left_units = _facility_signature(left)
    right_plants, right_units = _facility_signature(right)
    if left_units and right_units:
        left_by_plant = defaultdict(set)
        right_by_plant = defaultdict(set)
        for unit in left_units:
            plant, number = unit.rsplit("-", 1)
            left_by_plant[plant].add(number)
        for unit in right_units:
            plant, number = unit.rsplit("-", 1)
            right_by_plant[plant].add(number)
        for plant in left_by_plant.keys() & right_by_plant.keys():
            if left_by_plant[plant].isdisjoint(right_by_plant[plant]):
                return True
    return bool(left_plants and right_plants and left_plants.isdisjoint(right_plants))


def _country_conflict(left: dict, right: dict) -> bool:
    left_countries = set(left.get("countries") or []) - {"OTHER"}
    right_countries = set(right.get("countries") or []) - {"OTHER"}
    return bool(left_countries and right_countries and left_countries.isdisjoint(right_countries))


# 나라를 특정하지 못하는 범위 태그. 'EUROPE' 두 건은 서로 다른 나라일 수 있어
# 국가 대조에서 뺀다. 위 쌍 단위 _country_conflict 가 'OTHER' 만 빼는 것은
# 그쪽이 "이 둘은 확실히 다른 나라"를 잡는 좁은 판정이기 때문이고, 여기서는
# 화면 게이트와 같은 불변식을 쓰므로 범위를 맞춘다.
NON_COUNTRY_SCOPES = frozenset({"OTHER", "UNSPECIFIED", "GLOBAL", "EUROPE", "EU"})


def _cluster_countries(article: dict) -> set[str]:
    return set(article.get("countries") or []) - NON_COUNTRY_SCOPES


def _cluster_country_conflict(article: dict, members: list[dict]) -> bool:
    """이 기사를 묶음에 넣으면 국경이 어긋나는가.

    쌍 단위 판정은 전이적이지 않은데(위 '클러스터 전체 거부권'과 같은 함정)
    매칭은 최근 멤버 3건하고만 비교한다. 그 사이를 **국가가 겹치는 다국가
    기사**가 이어 주면 양 끝이 서로 다른 나라인 묶음이 만들어진다. 인접 쌍은
    전부 국가가 겹쳐 _country_conflict 를 통과하므로 아무도 못 막는다.

    실측 2026-08-15 라이브 issue-5190f5f0f0d050de:

        RO ──[HU,RO]── HU ──[FR,HU]── FR

    『다뉴브강 역대 최저 수위, 헝가리·루마니아 원전에 기후 위험 노출』 19건
    안에 『프랑스 원전 13기, 가뭄과 해파리로 발전 용량 감소』가 들어가 있었다.
    가뭄이라는 주제는 같지만 다뉴브강과 프랑스 원전은 다른 사건이다.

    국경을 넘는 **하나의** 사건은 막으면 안 된다 — 두코바니처럼 한국·체코를
    함께 다루는 보도가 실제로 있는 경우다. 그래서 양국을 함께 명시한 멤버
    (브리지)가 있으면 통과시킨다. 화면 데이터 게이트
    (test_generated_issue_clusters_have_no_country_or_facility_conflicts)가
    검사하는 것과 **같은 불변식**이다 — 배포 직전에 잡던 것을 병합 시점에서
    막는다.
    """
    incoming = _cluster_countries(article)
    if not incoming:
        return False
    member_countries = [_cluster_countries(member) for member in members]
    for other in member_countries:
        if not other or not incoming.isdisjoint(other):
            continue
        if any(bridge & incoming and bridge & other for bridge in member_countries):
            continue
        return True
    return False


def _cluster_facility_conflict(article: dict, members: list[dict]) -> bool:
    """설비도 같은 전이 구멍이 있다. 이쪽은 브리지 예외가 없다 —
    한빛 3호기와 한빛 4호기는 어떤 기사를 경유해도 같은 사건이 아니다."""
    return any(_facility_conflict(article, member) for member in members)


def _cluster_fingerprint_conflict(article: dict, members: list[dict]) -> bool:
    """이 기사가 묶음의 **누군가와** 신원 축에서 어긋나는가.

    아래 매칭에서 이것을 보는 것은 **지문 경로 하나뿐**이다. 나머지 경로
    (제목·태그·임베딩)에는 걸지 않는다 — 실측이 그러지 말라고 했다.

    2026-08-19 데이터로 '모든 합류'에 이 거부권을 걸어 봤더니, 멀쩡한 이슈가
    조각났다. 테라파워 국내 협력 12건짜리 묶음이 5개로, 체르나보다 저수위
    묶음이 2개로, 산업용 전기요금 차등제 묶음이 2개로 갈렸다. 이유는 분명하다 —
    **하나의 긴 사건 안에서 원인·대상 축은 원래 움직인다**(같은 테라파워
    이슈가 어떤 날은 `SMR commercialization`, 어떤 날은 `AI data center` 를
    원인으로 적는다). 제목과 태그가 강하게 붙여 놓은 것을 지문의 표현 차이로
    떼면 안 된다.

    거꾸로, 제목도 태그도 아무 말을 못 해서 **지문만으로** 붙는 자리에서는
    같은 모순이 결정적이다(그 경로의 실측 정밀도는 11쌍 중 1쌍이었다). 그래서
    범위를 그 경로로 좁힌다: 약한 근거는 연쇄하지 못한다.

    같은 이슈에 지문이 아예 없는 멤버가 섞여 있으면 그 멤버는 판단에서 빠진다
    (`compare` 가 빈 결과를 돌려준다) — 없는 것은 모순이 아니다.
    """
    fingerprint = article.get("story_fingerprint")
    if not isinstance(fingerprint, dict) or not fingerprint:
        return False
    identity = set(FINGERPRINT_MATCH_AXES)
    return any(
        identity & set(
            story_fingerprint.compare(fingerprint, member.get("story_fingerprint")).contested
        )
        for member in members
    )


# 같은 **설비·프로젝트**를 다루는 쌍은 후속 보도일 가능성이 높다. 기관·기업까지
# 넣으면 신호가 죽는다 — 실측(2026-08-05, 판정 완료 185쌍):
#
#     공유 엔티티 범위        같은 사건    다른 사건
#     전체(기관·기업 포함)         3          40   ← NRC·DOE·한수원이 나라마다 매 기사에
#     설비·프로젝트만              3           0      나오므로 판별력이 없다
#
# 표본이 3건뿐이라 **자동 병합 근거로는 약하다**. 그래서 병합 판정에는 쓰지 않고
# LLM 검수 큐의 우선순위에만 쓴다(판정 결과를 바꾸지 않으므로 오병합 위험 0).
# 한 빌드에서 새로 묻는 쌍은 40개로 묶여 있는데 밀린 후보가 519건이라, 어느 쌍을
# 먼저 묻느냐가 실제로 추적률을 정한다.
FOLLOW_UP_ENTITY_TYPES = ("plant", "project")


def facility_alias_entries(registry: list[dict]) -> list[tuple[str, bool, dict]]:
    """설비·프로젝트 엔티티만 추린 별칭 표."""
    return _entity_alias_entries(
        [entity for entity in registry if entity.get("type") in FOLLOW_UP_ENTITY_TYPES]
    )


def facility_entities_by_hash(articles: list[dict], alias_entries) -> dict[str, set[str]]:
    """기사 hash → 설비·프로젝트 엔티티 id 집합. 클러스터링 루프 밖에서 한 번만 돈다."""
    if not alias_entries:
        return {}
    out: dict[str, set[str]] = {}
    for article in articles:
        entity_ids, _ = entity_ids_for_members([article], alias_entries)
        if entity_ids:
            out[str(article.get("hash") or "")] = set(entity_ids)
    return out


def issue_similarity(
    left: dict,
    right: dict,
    embeddings: dict[str, list[float]] | None = None,
    local_embeddings: dict[str, list[float]] | None = None,
    facility_entities: dict[str, set[str]] | None = None,
) -> tuple[bool, float, dict]:
    """두 기사가 같은 이슈인지 보수적으로 판정한다.

    false merge가 누락보다 해롭기 때문에 넓은 주제 태그 하나만으로는 합치지 않는다.
    반환 진단값은 테스트와 임계값 조정에 사용한다.
    """
    left_title, right_title = _title_norm(left), _title_norm(right)
    title_ratio = (
        difflib.SequenceMatcher(None, left_title, right_title).ratio()
        if left_title and right_title else 0.0
    )
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    token_ratio = _jaccard(left_tokens, right_tokens)
    left_tags, right_tags = _strong_tags(left), _strong_tags(right)
    tag_shared = len(left_tags & right_tags)
    tag_ratio = _jaccard(left_tags, right_tags)
    left_topics, right_topics = set(left.get("topics") or []), set(right.get("topics") or [])
    topic_shared = len(left_topics & right_topics)
    embedding_similarity = cosine_similarity(
        (embeddings or {}).get(str(left.get("hash") or "")),
        (embeddings or {}).get(str(right.get("hash") or "")),
    )
    local_embedding_similarity = cosine_similarity(
        (local_embeddings or {}).get(str(left.get("hash") or "")),
        (local_embeddings or {}).get(str(right.get("hash") or "")),
    )
    shared_facility_entities = sorted(
        (facility_entities or {}).get(str(left.get("hash") or ""), set())
        & (facility_entities or {}).get(str(right.get("hash") or ""), set())
    )
    country_conflict = _country_conflict(left, right)
    facility_conflict = _facility_conflict(left, right)
    blocked_by = []
    if country_conflict:
        blocked_by.append("country_conflict")
    if facility_conflict:
        blocked_by.append("facility_conflict")

    fingerprint_similarity, fingerprint_diag = story_fingerprint_similarity(left, right)
    evidence_overlap = story_cluster.evidence_overlap(left, right)
    score = 0.55 * title_ratio + 0.25 * token_ratio + 0.20 * tag_ratio
    method = "none"
    matched = False
    if not blocked_by:
        left_story_id = str(left.get("story_id") or "").strip()
        right_story_id = str(right.get("story_id") or "").strip()
        if (left_story_id and left_story_id == right_story_id
                and story_identity.trusted_same_id(left, right)):
            matched, method, score = True, "story_id", max(score, 1.0)
        elif (left_story_id and left_story_id == right_story_id
              and (
                  (
                      "assets" in set(fingerprint_diag.get("shared") or ())
                      and not ({"countries", "assets"}
                               & set(fingerprint_diag.get("contested") or ()))
                  )
                  or evidence_overlap.shared >= 2
              )):
            # Pre-v2 IDs are retrieval hints, never proof by themselves.  Preserve a
            # normal legacy follow-up only when concrete asset identity or immutable
            # article evidence independently corroborates it.
            matched, method, score = True, "legacy_story_id_confirmed", max(score, 0.95)
        elif title_ratio >= TITLE_MATCH_RATIO:
            matched, method = True, "title"
        elif tag_shared >= TAGS_MATCH_MIN_SHARED and (
            title_ratio >= TAGS_MATCH_TITLE_RATIO or token_ratio >= TAGS_MATCH_TOKEN_RATIO
        ):
            matched, method = True, "tags"
        # 구체 태그가 같고 제목 절반 이상이 겹치면 같은 후속 이슈로 본다.
        # 실측 예: "12차 전기본 … 정책 혼선"과
        # "12차 전력수급기본계획 … 정부 부처 간 혼선".
        elif tag_shared >= 1 and title_ratio >= TITLE_TAGS_MATCH_RATIO:
            matched, method = True, "title_tags"
        # Daily Brief의 story fingerprint를 웹 issue 연결에도 사용한다. 다만 자유형
        # LLM 필드라 단독 느슨 매칭은 금지한다. 이 경로는 제목·태그가 전부 실패한
        # 뒤에 오는 **마지막 수단**이므로, 지문 스스로가 두 가지를 만족해야 한다.
        #
        # ① 신원 축이 둘 이상 겹칠 것. 나라와 event_family 는 못 센다 — 닫힌
        #    어휘라 '같은 값'이 같은 사건을 뜻하지 않는다(실측 71건에서
        #    event_family 는 15종뿐이고 policy_decision 하나가 45%).
        # ② 신원 축 가운데 **어긋난 것이 없을 것**. 어긋남은 희석이 아니라
        #    반대 증거다 — `_country_conflict`·`_facility_conflict` 가 이미
        #    같은 원리로 서 있다.
        #
        # 왜 이 두 조건인지 (2026-08-19 라이브 빌드에서 지문만으로 붙은 11쌍 전수):
        #
        #     오병합 10건 — 전부 원인 축이 어긋나 있었다. 겹친 것은 나라·부처·
        #                   policy_decision 뿐. 예: 『12차 전기본 재정비』가
        #                   『산업부 장관 대미투자 방미』에 붙었다(제목 유사도
        #                   0.16, 공통 태그 0). 사용자가 타임라인에서 본 것이 이것이다.
        #     정상  1건 — 『대미 전략투자 1호 막판 조율』↔『대미투자 방미』.
        #                 여기만 원인 축(`investment`)을 공유했다.
        #
        # 두 조건 다 ①의 신원 축 정의에 기대므로 정의는 story_fingerprint 모듈에 있다.
        elif (
            fingerprint_similarity >= FINGERPRINT_MATCH_SIMILARITY
            and fingerprint_diag.get("compared", 0) >= FINGERPRINT_MATCH_MIN_COMPARED
            and not (set(fingerprint_diag.get("contested") or []) & set(FINGERPRINT_MATCH_AXES))
            and len(set(fingerprint_diag.get("shared") or []) & set(FINGERPRINT_MATCH_AXES))
            >= FINGERPRINT_MATCH_MIN_SHARED_AXES
        ):
            matched, method = True, "story_fingerprint"
            score = max(score, fingerprint_similarity)
        # 보조 조건(tag/topic/title)은 게이트 역할을 못 했다. topics 가 통제 어휘
        # 12개라 원자력 기사 둘이면 topic_shared>=1 이 사실상 항상 참이었고,
        # 실측 자동 병합 60건 중 38건이 그 조건만으로 통과했다. 남는 판정이
        # 코사인 하나뿐이었는데 0.82 는 한국어 원자력 요약문에서
        # "같은 사건"이 아니라 "같은 분야"를 잡는 높이다(오병합 쌍의 코사인
        # 중앙값 0.856, 제목 유사도 중앙값 0.24). 게이트를 걷어내고 코사인만
        # 0.92 로 올린다 — 0.92 미만은 사람/LLM 검수 큐로 보낸다.
        elif (
            embedding_similarity is not None
            and embedding_similarity >= ISSUE_EMBEDDING_THRESHOLD
        ):
            matched, method = True, "embedding"
            score = max(score, embedding_similarity)
    elif blocked_by:
        method = "blocked"

    left_plants, left_units = _facility_signature(left)
    right_plants, right_units = _facility_signature(right)
    return matched, round(score, 4), {
        "title_ratio": round(title_ratio, 4),
        "token_ratio": round(token_ratio, 4),
        "tag_ratio": round(tag_ratio, 4),
        "tag_shared": tag_shared,
        "topic_shared": topic_shared,
        "embedding_similarity": round(embedding_similarity, 4) if embedding_similarity is not None else None,
        "local_embedding_similarity": (
            round(local_embedding_similarity, 4)
            if local_embedding_similarity is not None else None
        ),
        "story_fingerprint_similarity": round(fingerprint_similarity, 4),
        "story_fingerprint_shared": fingerprint_diag.get("shared") or [],
        "story_fingerprint_compared": fingerprint_diag.get("compared") or 0,
        # 비교했는데 하나도 안 겹친 축. 운영 콘솔이 "왜 안 붙었나"를 말하려면
        # 겹친 축만으로는 부족하다 — 막은 것은 이쪽이다.
        "story_fingerprint_contested": fingerprint_diag.get("contested") or [],
        "method": method,
        "blocked_by": blocked_by,
        "shared_facility_entities": shared_facility_entities,
        "left_facilities": sorted(left_units or left_plants),
        "right_facilities": sorted(right_units or right_plants),
    }


def has_review_context(diagnostics: dict) -> bool:
    """코사인을 보기 **전에** 통과해야 하는 최소 맥락.

    `is_review_candidate` 안에 있던 식을 이름만 붙여 꺼냈다 — 값도 순서도
    그대로다. 꺼낸 이유는 계측이다: 이 게이트에서 떨어진 쌍과 코사인에서
    떨어진 쌍은 **완전히 다른 이야기**인데, 안에 묻혀 있으면 둘 다 그냥
    '후보 아님'으로 보인다(실측 evidence 경로에서 전자가 162,194쌍).
    """
    return bool(
        diagnostics.get("tag_shared")
        or diagnostics.get("topic_shared")
        or float(diagnostics.get("title_ratio") or 0) >= 0.28
        or float(diagnostics.get("token_ratio") or 0) >= 0.16
        or float(diagnostics.get("story_fingerprint_similarity") or 0) >= 0.55
    )


def is_review_candidate(diagnostics: dict) -> tuple[bool, str, float]:
    """자동 병합 아래 구간을 사람 확인 큐로 보낸다."""
    if diagnostics.get("blocked_by"):
        return False, "", 0.0
    remote = diagnostics.get("embedding_similarity")
    local = diagnostics.get("local_embedding_similarity")
    title_ratio = float(diagnostics.get("title_ratio") or 0)
    token_ratio = float(diagnostics.get("token_ratio") or 0)
    if not has_review_context(diagnostics):
        return False, "", 0.0
    if remote is not None and remote >= ISSUE_EMBEDDING_CANDIDATE_THRESHOLD:
        return True, "gemini_candidate", float(remote)
    if (
        local is not None
        and local >= LOCAL_EMBEDDING_CANDIDATE_THRESHOLD
        and (
            (diagnostics.get("tag_shared") and title_ratio >= 0.20)
            or (
                diagnostics.get("topic_shared")
                and (title_ratio >= 0.25 or token_ratio >= 0.12)
            )
            or title_ratio >= 0.45
        )
    ):
        return True, "local_candidate", float(local)
    return False, "", 0.0


def _pair_outcome(matched: bool, diagnostics: dict, recorded: bool) -> str:
    """채점한 쌍 하나의 결말을 한 낱말로. **계측 전용 — 판정에 쓰이지 않는다.**

    '후보가 아니다'가 네 가지 서로 다른 사실을 덮고 있었다: 붙었다 / 국가·설비가
    막았다 / 맥락이 없었다 / 맥락은 있는데 코사인이 모자랐다. 마지막 둘의 비율이
    임계값을 올릴지 비교를 줄일지를 가른다.
    """
    if matched:
        return f"matched:{diagnostics.get('method') or '?'}"
    if diagnostics.get("blocked_by"):
        return "blocked"
    if recorded:
        return "candidate"
    if not has_review_context(diagnostics):
        return "no_context"
    return "below_threshold"


def _lexical_score(diagnostics: dict) -> float:
    """`issue_similarity` 의 어휘 점수를 이미 계산된 진단값으로 다시 조립한다.

    **계측 전용.** 같은 식이 issue_similarity 안에 있고 그쪽이 원본이다 —
    여기서 다시 재는 것은 "임베딩을 조회하기 **전에** 묶음을 줄 세웠다면 정답이
    몇 위였을까"를 그림자로 재기 위해서다. 값이 두 곳에 있으므로 식을 고칠 때는
    둘 다 고쳐야 한다(web/tests 가 두 값이 같은지 대조한다).

    진단값이 이미 소수 넷째 자리에서 반올림돼 있어 원본과 최대 1e-4 어긋난다.
    순위를 매기는 용도라 무해하다 — 정확한 점수가 필요하면 원본을 쓸 것.
    """
    return (0.55 * float(diagnostics.get("title_ratio") or 0)
            + 0.25 * float(diagnostics.get("token_ratio") or 0)
            + 0.20 * float(diagnostics.get("tag_ratio") or 0))


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _representative_key(article: dict) -> tuple:
    return (
        1 if article.get("importance") == "must_read" else 0,
        float(article.get("selection_score") or 0),
        1 if article.get("source_tier") == 1 else 0,
        len(article.get("summary") or ""),
        article.get("article_date") or "",
    )


def flow_takeaway(direction: object, limit: int = 86) -> str:
    """긴 흐름 해석에서 중간 절단 없이 완결된 첫 문장을 만든다.

    원문 첫 문장이 이미 짧으면 그대로 쓴다. 길면서 쉼표로 사건이 이어질 때는
    첫 절만 취하고 연결 어미를 종결 어미로 바꾼다. 안전하게 종결할 수 없는
    문장은 억지로 자르지 않고 첫 문장 전체를 유지한다.
    """
    text = re.sub(r"\s+", " ", str(direction or "")).strip()
    if not text:
        return ""
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if len(first_sentence) <= limit:
        return first_sentence

    first_clause = re.split(r"[,;]\s*", first_sentence, maxsplit=1)[0].strip()
    if not first_clause or len(first_clause) < 28:
        return first_sentence

    ending_rules = (
        (r"고 있으며$", "고 있습니다."),
        (r"해 왔으며$", "해 왔습니다."),
        (r"했으며$", "했습니다."),
        (r"됐으며$", "됐습니다."),
        (r"였으며$", "였습니다."),
        (r"이며$", "입니다."),
        (r"하고$", "했습니다."),
        (r"되고$", "됐습니다."),
        (r"되어$", "됐습니다."),
    )
    for pattern, replacement in ending_rules:
        if re.search(pattern, first_clause):
            completed = re.sub(pattern, replacement, first_clause)
            return completed if len(completed) <= limit else first_sentence
    return first_sentence


def _evidence_overlap(left: dict, right: dict) -> float:
    left_hashes = {row.get("hash") for row in left.get("evidence", []) if row.get("hash")}
    right_hashes = {row.get("hash") for row in right.get("evidence", []) if row.get("hash")}
    if not left_hashes or not right_hashes:
        return 0.0
    return len(left_hashes & right_hashes) / min(len(left_hashes), len(right_hashes))


def _insight_signal_score(item: dict) -> float:
    current = float(item.get("count_now") or 0)
    previous = float(item.get("count_prev") or 0)
    evidence_count = len(item.get("evidence") or [])
    return current + max(0.0, current - previous) * 0.6 + min(evidence_count, 6) * 0.15


def select_featured_insights(items: list[dict], limit: int = 3) -> list[dict]:
    """강도·근거 중복·국내외 커버리지를 함께 보는 주간 대표 흐름 선택."""
    candidates = [item for item in items if item.get("direction") and item.get("evidence")]
    selected: list[dict] = []
    covered_regions: set[str] = set()

    while candidates and len(selected) < limit:
        def adjusted(item: dict) -> tuple:
            regions = set(item.get("evidence_regions") or [])
            new_regions = regions - covered_regions
            region_bonus = 2.5 * len(new_regions) if selected else 0.0
            if "해외" in new_regions and item.get("region_scope") == "해외":
                region_bonus += 0.35
            redundancy = max((_evidence_overlap(item, other) for other in selected), default=0.0)
            score = _insight_signal_score(item) + region_bonus - 6.0 * redundancy
            return (score, _insight_signal_score(item), item.get("keyword") or "")

        best = max(candidates, key=adjusted)
        candidates.remove(best)
        selected.append(best)
        covered_regions.update(best.get("evidence_regions") or [])
    return selected


# 두 흐름이 근거를 이만큼 공유하면 같은 사건을 키워드만 바꿔 되풀이한 것이다.
# 흐름 해석은 키워드마다 하나씩 만들어지는데, 한 사건이 여러 키워드를 달고
# 있으면 같은 이야기가 그 수만큼 재포장된다.
# 실측(2026-08-03 라이브): '기후변화'와 '원전운영'이 근거 7건 중 4건(57%)을
# 공유했다 — 둘 다 헝가리 가뭄으로 인한 원전 가동 중단 이야기였다.
# 나머지 쌍은 1건(7~17%)이라 경계가 뚜렷하다.
INSIGHT_DUPLICATE_RATIO = 0.4


def dedupe_insights(items: list[dict]) -> list[dict]:
    """근거가 크게 겹치는 흐름을 접는다. 근거가 많은 쪽을 남긴다."""
    ordered = sorted(
        items,
        key=lambda item: (len(item.get("evidence") or []), item.get("signal_score") or 0),
        reverse=True,
    )
    kept: list[dict] = []
    for item in ordered:
        hashes = {row.get("hash") for row in item.get("evidence") or [] if row.get("hash")}
        if not hashes:
            kept.append(item)
            continue
        duplicate_of = None
        for other in kept:
            other_hashes = {row.get("hash") for row in other.get("evidence") or [] if row.get("hash")}
            shared = hashes & other_hashes
            if shared and len(shared) / min(len(hashes), len(other_hashes)) >= INSIGHT_DUPLICATE_RATIO:
                duplicate_of = other
                break
        if duplicate_of is None:
            kept.append(item)
        else:
            # 접힌 키워드는 남은 흐름에 함께 표기한다 — 정보를 버리지 않는다
            merged = duplicate_of.setdefault("merged_keywords", [])
            keyword = item.get("keyword")
            if keyword and keyword not in merged:
                merged.append(keyword)
    # 원래 순서(입력 순)를 유지해 화면 배치가 흔들리지 않게 한다
    order = {id(item): index for index, item in enumerate(items)}
    return sorted(kept, key=lambda item: order.get(id(item), 0))


WEEKLY_MOVER_COUNT = 4


def build_weekly_movers(issue_catalog: list[dict], end_date: str,
                        days: int = 7) -> list[dict]:
    """이번 주 가장 크게 움직인 이슈.

    흐름 해석을 **키워드 단위**로 만들던 것을 이슈(사건) 단위로 바꾼다.
    키워드 단위에서는 한 사건이 달고 있는 키워드 수만큼 같은 이야기가 재포장됐다
    (실측 2026-08-03: 헝가리 가뭄 원전 중단 하나가 기후변화·원전운영·전력시장·
    에너지안보 네 흐름에 동시 등장). 이슈는 이미 사건 단위로 묶여 있으므로
    중복이 구조적으로 생기지 않는다.

    '움직임'은 이번 주에 실제로 쌓인 양으로 잰다 — 이번 주 원문 수, 며칠에 걸쳐
    보도됐는지, 서로 다른 매체가 몇 곳인지. 해석 문장을 붙이지 않는다.
    """
    end = _parse_day(end_date)
    if not end:
        return []
    start = (end - timedelta(days=days - 1)).isoformat()

    movers = []
    for issue in issue_catalog:
        in_week = [
            article for article in issue.get("related_articles") or []
            if article.get("member_role", "card") == "card"
            and str(article.get("briefing_date") or "") >= start
        ]
        if not in_week:
            continue
        publishers = {
            (article.get("publisher") or article.get("domain") or "").strip()
            for article in in_week
        }
        publishers.discard("")
        days_covered = len({article.get("briefing_date") for article in in_week})
        movers.append({
            "issue_id": issue["issue_id"],
            "title": issue["title"],
            "summary": issue.get("summary", ""),
            "region": issue.get("region", ""),
            "topics": issue.get("topics") or [],
            "week_article_count": len(in_week),
            "week_days": days_covered,
            "publisher_count": len(publishers),
            "total_article_count": issue.get("article_count", len(in_week)),
            "is_continuing": bool(issue.get("first_seen", "") < start),
            "first_seen": issue.get("first_seen", ""),
            "last_seen": issue.get("last_seen", ""),
            "verification": issue.get("verification") or {},
            # 이 이슈가 이번 주에 실제로 무엇으로 구성됐는지 — 해석 대신 사실
            "events": [
                {"date": article.get("article_date", ""),
                 "title": article.get("title_kr", ""),
                 "publisher": article.get("publisher") or article.get("domain") or "",
                 "url": source_url(article)}
                for article in sorted(
                    in_week, key=lambda a: str(a.get("article_date") or ""), reverse=True)[:4]
            ],
        })

    # 많이·여러 날·여러 매체에서 다뤄진 순. 국내 이슈는 업무 관련성이 높아
    # 동률일 때 앞세운다.
    movers.sort(key=lambda row: (
        row["week_article_count"], row["week_days"], row["publisher_count"],
        row["region"] == "국내",
    ), reverse=True)
    return movers[:WEEKLY_MOVER_COUNT]


def prepare_insights(insights: dict, news_items: list[dict]) -> dict:
    """흐름 근거에 지역 메타를 붙이고 다양화된 대표 3개를 만든다."""
    by_hash = {item["hash"]: item for item in news_items}
    items = []
    for raw_item in insights.get("items", []):
        item = dict(raw_item)
        evidence = []
        seen = set()
        for raw_evidence in item.get("evidence") or []:
            article_hash = raw_evidence.get("hash")
            if not article_hash or article_hash in seen:
                continue
            article = by_hash.get(article_hash)
            # 아카이브 품질 마이그레이션에서 삭제·병합된 기사는 더 이상
            # 공개 근거가 아니다. 빈 메타로 남기지 말고 인사이트에서도 제거한다.
            if article is None:
                continue
            seen.add(article_hash)
            evidence.append({
                **raw_evidence,
                "region": article.get("region", ""),
                "countries": article.get("countries") or [],
                "topics": article.get("topics") or [],
                "publisher": article.get("publisher", ""),
                "domain": article.get("domain", ""),
            })
        regions = {row["region"] for row in evidence if row.get("region") in {"국내", "해외"}}
        item["evidence"] = evidence
        item["evidence_regions"] = sorted(regions, key=lambda value: (value != "국내", value))
        item["domestic_evidence_count"] = sum(1 for row in evidence if row.get("region") == "국내")
        item["overseas_evidence_count"] = sum(1 for row in evidence if row.get("region") == "해외")
        item["region_scope"] = (
            "국내·해외" if regions == {"국내", "해외"}
            else next(iter(regions), "범위 미분류")
        )
        item["takeaway"] = flow_takeaway(item.get("direction"))
        item["signal_score"] = round(_insight_signal_score(item), 3)
        items.append(item)

    items = dedupe_insights(items)

    prepared = dict(insights)
    prepared["items"] = items
    prepared["featured_items"] = select_featured_insights(items)
    prepared["selection_method"] = "signal-region-evidence-diversity-v2-deduped"
    return prepared


def cluster_selected_articles(
    news_items: list[dict],
    embeddings: dict[str, list[float]] | None = None,
    local_embeddings: dict[str, list[float]] | None = None,
    match_overrides: dict[str, set[str]] | None = None,
    review_candidates: list[dict] | None = None,
    facility_entities: dict[str, set[str]] | None = None,
    telemetry: issue_candidate_stats.SearchTelemetry | None = None,
) -> list[dict]:
    """발송된 기사들을 최근 이슈 묶음으로 연결한다.

    issue_id는 최초 기사 hash에서 만들어 안정적으로 유지한다. 대표 기사는 더 좋은
    출처나 중요 기사로 바뀔 수 있지만 issue_id는 바뀌지 않는다.

    `telemetry` 는 **계수기일 뿐이다** — 넘기든 안 넘기든 반환값이 같아야 하고,
    `None` 이면 계측 호출 자체를 하지 않는다(web/tests 가 두 경로의 산출물이
    바이트 단위로 같은지 잠근다).
    """
    selected = [item for item in news_items if item.get("briefing_date")]
    selected.sort(key=lambda item: (item["briefing_date"], item["article_date"], item["hash"]))
    issues: list[dict] = []
    overrides = match_overrides or {"approved": set(), "rejected": set()}
    # '다른 사건'으로 이미 판정된 쌍. 사람 판정과 LLM 판정을 같이 본다.
    veto_pairs = set(overrides.get("rejected") or ()) | set(overrides.get("llm_rejected") or ())
    candidate_rows = review_candidates if review_candidates is not None else []
    seen_candidates = {_pair_id(row.get("left_hash"), row.get("right_hash")) for row in candidate_rows}

    for article in selected:
        article_day = _parse_day(article.get("briefing_date", ""))
        best_issue = None
        best_score = -1.0
        best_diag = None

        for issue in issues:
            if telemetry is not None:
                telemetry.visit()
            last_day = _parse_day(issue["last_seen"])
            if article_day and last_day and (article_day - last_day).days > ISSUE_WINDOW_DAYS:
                if telemetry is not None:
                    telemetry.skip("window")
                continue
            # 클러스터 전체 거부권 — 쌍 단위 판정은 전이적이지 않다.
            #
            # A=B 와 A=C 를 각각 승인해도 B≠C 라면 셋을 한 묶음으로 만들면 안 된다.
            # 아래 매칭은 멤버 하나만 맞으면 합류시키는 탐욕적 구조라, 거부된 짝이
            # 같은 이슈 안에 있어도 다른 멤버를 통해 들어올 수 있다.
            #
            # 실제 사고(2026-08-03 라이브, issue-6b93ed7e22e9bb4b): 서로 다른 NRC
            # 규정 제정 2건(환경영향평가 / 방사성 물질 운송)이 '공청회서 신규 규정
            # 제안'이라는 일반적 제목을 경유해 한 이슈로 합쳐졌다. LLM 은 그 둘을
            # "서로 다른 규정 제안"으로 **정확히 기각한 상태였다** — 판정기가 아니라
            # 판정을 이어붙이는 이 지점이 문제였다.
            #
            # 승인이 함께 있어도 거부권이 이긴다 — 완화해 보고 되돌렸다(2026-08-05).
            # 사용자가 지적한 팍스 건은 구조가 위 NRC 사고와 **똑같다**:
            #   팍스: 터빈 ↔ 가뭄 대표 승인 / 터빈 ↔ '중대한 시기 경고' 기각
            #   NRC : a↔b 승인 / a↔c 승인 / b↔c 기각
            # 둘 다 "승인 하나 + 기각 하나"이고, 팍스에선 기각이 틀렸고 NRC 에선
            # 옳았다. **그 차이는 코드가 볼 수 없다.** 승인을 우선하도록 풀면
            # test_rejected_pair_vetoes_the_whole_cluster... 가 바로 깨진다.
            # 틀린 판정은 판정을 고쳐야 한다 — issue_match_overrides.json 의 사람
            # 승인으로 뒤집는다(그 목적으로 만들어진 파일이다).
            if veto_pairs and any(
                _pair_id(article["hash"], member["hash"]) in veto_pairs
                for member in issue["members"]
            ):
                if telemetry is not None:
                    telemetry.skip("veto")
                continue
            # 국가·설비 충돌에도 같은 전체 거부권을 준다. 아래 매칭은 최근 3건만
            # 보므로 blocked_by 는 그 3건에 대해서만 계산된다 — 더 오래된 멤버와
            # 나라가 어긋나도 통과한다(_cluster_country_conflict 주석의 실측 사고).
            if _cluster_country_conflict(article, issue["members"]):
                if telemetry is not None:
                    telemetry.skip("country_conflict")
                continue
            if _cluster_facility_conflict(article, issue["members"]):
                if telemetry is not None:
                    telemetry.skip("facility_conflict")
                continue
            if telemetry is not None:
                telemetry.compare(article["hash"])
            # 지문 경로만 묶음 전체와 대조한다 — 약한 근거는 연쇄하지 못한다.
            # (왜 이 경로만인지는 _cluster_fingerprint_conflict 주석에 실측이 있다.)
            fingerprint_chain_blocked = _cluster_fingerprint_conflict(article, issue["members"])
            # 대표 기사 한 건만 보면 표현이 단계적으로 바뀌는 A→B→C 후속 보도가
            # 끊길 수 있다. 최근 기사 3건 중 가장 가까운 연결을 사용한다.
            for reference in issue["members"][-3:]:
                pair_id = _pair_id(article["hash"], reference["hash"])
                recorded = False
                matched, score, diag = issue_similarity(
                    article, reference, embeddings, local_embeddings, facility_entities
                )
                # 승인 override 보다 위에 있어도 사람 판정을 뒤집지 않는다:
                # 이 게이트는 blocked_by 를 건드리지 않으므로 아래 승인 분기가
                # 그대로 matched 를 되살린다. 그리고 판정이 뒤집힌 쌍은
                # `elif not matched` 로 흘러 검수 큐에 남는다 — 정말 같은
                # 사건이면 사람이 다시 이을 자리가 있어야 한다.
                if matched and diag.get("method") == "story_fingerprint" and (
                        fingerprint_chain_blocked):
                    matched = False
                    diag = {**diag, "method": "fingerprint_chain_blocked"}
                    if telemetry is not None:
                        telemetry.fingerprint_chain_demoted()
                if pair_id in veto_pairs:
                    if telemetry is not None:
                        telemetry.pair(article["hash"], "veto",
                                       issue["issue_id"], _lexical_score(diag))
                    continue
                if pair_id in overrides.get("approved", set()) and not diag.get("blocked_by"):
                    matched, score = True, max(score, 1.0)
                    diag = {**diag, "method": "manual_approved"}
                # 회색지대(0.84~0.92)를 LLM 이 같은 사건으로 판정한 쌍. 사람 승인과
                # 구분해 audit 에 남긴다. 사람 승인(1.0)보다 낮은 점수를 줘서
                # 같은 기사가 양쪽에 붙을 때 사람 판정이 이기게 한다.
                elif pair_id in overrides.get("llm_approved", set()) and not diag.get("blocked_by"):
                    matched, score = True, max(score, 0.99)
                    diag = {**diag, "method": "llm_approved"}
                elif not matched:
                    is_candidate, candidate_method, candidate_score = is_review_candidate(diag)
                    if is_candidate and pair_id not in seen_candidates:
                        seen_candidates.add(pair_id)
                        recorded = True
                        candidate_rows.append({
                            "candidate_id": pair_id,
                            "left_hash": reference["hash"],
                            "right_hash": article["hash"],
                            "left_date": reference.get("briefing_date"),
                            "right_date": article.get("briefing_date"),
                            "left_title": reference.get("title_kr") or reference.get("title"),
                            "right_title": article.get("title_kr") or article.get("title"),
                            "candidate_method": candidate_method,
                            "candidate_score": round(candidate_score, 4),
                            # issue_review 는 build_data 를 import 하지 않는다
                            # (순환 방지) — 우선순위 판단 재료를 행에 실어 보낸다.
                            "shared_facility_entities": diag.get("shared_facility_entities") or [],
                            "left_story_fingerprint": reference.get("story_fingerprint") or {},
                            "right_story_fingerprint": article.get("story_fingerprint") or {},
                            "diagnostics": diag,
                            "review_state": "pending",
                        })
                if telemetry is not None:
                    telemetry.pair(article["hash"], _pair_outcome(matched, diag, recorded),
                                   issue["issue_id"], _lexical_score(diag))
                if matched and score > best_score:
                    best_issue, best_score = issue, score
                    best_diag = {**diag, "reference_hash": reference["hash"]}

        if telemetry is not None:
            telemetry.settle((best_issue or {}).get("issue_id"))
        if best_issue is None:
            stable_story_id = str(article.get("story_id") or "").strip()
            if article.get("story_id_source") == "legacy_hash":
                stable_story_id = ""
            issues.append({
                "issue_id": stable_story_id or f"issue-{article['hash']}",
                "first_seen": article["briefing_date"],
                "last_seen": article["briefing_date"],
                "representative": article,
                "members": [article],
                "match_diagnostics": [],
            })
            continue

        best_issue["members"].append(article)
        best_issue["last_seen"] = article["briefing_date"]
        best_issue["match_diagnostics"].append({
            "hash": article["hash"],
            "score": best_score,
            **(best_diag or {}),
        })
        if _representative_key(article) > _representative_key(best_issue["representative"]):
            best_issue["representative"] = article

    return issues


def _cheap_issue_score(left: dict, right: dict) -> float:
    """Lexical score used only to order evidence candidates."""
    left_title, right_title = _title_norm(left), _title_norm(right)
    title_ratio = (
        difflib.SequenceMatcher(None, left_title, right_title).ratio()
        if left_title and right_title else 0.0
    )
    return (
        0.55 * title_ratio
        + 0.25 * _jaccard(_tokens(left), _tokens(right))
        + 0.20 * _jaccard(_strong_tags(left), _strong_tags(right))
    )


def _title_grams(article: dict, width: int = 3) -> set[str]:
    title = _title_norm(article)
    if len(title) < width:
        return {title} if title else set()
    return {title[index:index + width] for index in range(len(title) - width + 1)}


def _embedding_bands(vector: list[float] | None) -> tuple[tuple[int, int], ...]:
    """Sparse random-hyperplane LSH bands for bounded cosine candidate retrieval."""
    if not vector:
        return ()
    dimension = len(vector)
    total_bits = EVIDENCE_LSH_BANDS * EVIDENCE_LSH_BITS_PER_BAND
    bits: list[int] = []
    for bit in range(total_bits):
        state = (dimension * 2654435761 + bit * 2246822519 + 3266489917) & 0xFFFFFFFF
        projection = 0.0
        for _ in range(16):
            state = (1664525 * state + 1013904223) & 0xFFFFFFFF
            index = state % dimension
            sign = 1.0 if state & 0x80000000 else -1.0
            projection += float(vector[index]) * sign
        bits.append(1 if projection >= 0 else 0)
    out = []
    width = EVIDENCE_LSH_BITS_PER_BAND
    for band in range(EVIDENCE_LSH_BANDS):
        value = 0
        for flag in bits[band * width:(band + 1) * width]:
            value = (value << 1) | flag
        out.append((band, value))
    return tuple(out)


def _build_evidence_issue_index(
    issues: list[dict],
    facility_entities: dict[str, set[str]] | None,
    embeddings: dict[str, list[float]] | None,
    local_embeddings: dict[str, list[float]] | None,
) -> dict:
    """Build linear-size inverted indexes once; queries never scan every issue."""
    index = {
        "issues": {}, "order": {}, "members": defaultdict(set),
        "story": defaultdict(set), "facility": defaultdict(set),
        "tags": defaultdict(set), "tokens": defaultdict(set), "grams": defaultdict(set),
        "fingerprint": {axis: defaultdict(set) for axis in FINGERPRINT_MATCH_AXES},
        "remote_lsh": defaultdict(set), "local_lsh": defaultdict(set),
    }
    for order, issue in enumerate(issues):
        issue_id = str(issue.get("issue_id") or f"@{order}")
        index["issues"][issue_id] = issue
        index["order"][issue_id] = order
        members = issue.get("members") or []
        recent = members[-3:]
        for member in members:
            member_hash = str(member.get("hash") or "")
            if member_hash:
                index["members"][member_hash].add(issue_id)
                for facility in (facility_entities or {}).get(member_hash, set()):
                    index["facility"][facility].add(issue_id)
            story_id = str(member.get("story_id") or "").strip()
            if story_id:
                index["story"][story_id].add(issue_id)
        for member in recent:
            for tag in _strong_tags(member):
                index["tags"][tag].add(issue_id)
            for token in _tokens(member):
                index["tokens"][token].add(issue_id)
            for gram in _title_grams(member):
                index["grams"][gram].add(issue_id)
            fingerprint = member.get("story_fingerprint") or {}
            for axis in FINGERPRINT_MATCH_AXES:
                for value in story_fingerprint.axis_values(fingerprint, axis):
                    index["fingerprint"][axis][value].add(issue_id)
            member_hash = str(member.get("hash") or "")
            for band in _embedding_bands((embeddings or {}).get(member_hash)):
                index["remote_lsh"][band].add(issue_id)
            for band in _embedding_bands((local_embeddings or {}).get(member_hash)):
                index["local_lsh"][band].add(issue_id)
    return index


def _rare_postings(mapping: dict, keys: set[str]) -> list[tuple[str, set[str]]]:
    rows = [(key, mapping.get(key, set())) for key in keys if mapping.get(key)]
    rows = [row for row in rows if len(row[1]) <= EVIDENCE_RETRIEVAL_MAX_POSTINGS]
    return sorted(rows, key=lambda row: (len(row[1]), row[0]))[:EVIDENCE_RETRIEVAL_TERMS]


def _lsh_candidates(mapping: dict, vector: list[float] | None) -> set[str]:
    out: set[str] = set()
    width = EVIDENCE_LSH_BITS_PER_BAND
    for band, value in _embedding_bands(vector):
        out.update(mapping.get((band, value), set()))
        for bit in range(width):
            out.update(mapping.get((band, value ^ (1 << bit)), set()))
    return out


def _preselect_evidence_issues(
    article: dict,
    issue_index: dict,
    overrides: dict[str, set[str]],
    facility_entities: dict[str, set[str]] | None,
    embeddings: dict[str, list[float]] | None,
    local_embeddings: dict[str, list[float]] | None,
    telemetry: issue_candidate_stats.SearchTelemetry | None,
) -> list[dict]:
    """Retrieve bounded candidates, then return lexical/vector heads plus identity lanes."""
    article_day = _parse_day(article.get("article_date", ""))
    article_hash = str(article.get("hash") or "")
    article_story_id = str(article.get("story_id") or "").strip()
    article_facilities = (facility_entities or {}).get(article_hash, set())
    approvals = (
        set(overrides.get("approved") or ())
        | set(overrides.get("llm_approved") or ())
    )
    mandatory_ids: set[str] = set(issue_index["story"].get(article_story_id, set()))
    for facility in article_facilities:
        mandatory_ids.update(issue_index["facility"].get(facility, set()))
    for pair_id in approvals:
        left, separator, right = str(pair_id).partition("--")
        if not separator:
            continue
        other = right if left == article_hash else left if right == article_hash else ""
        if other:
            mandatory_ids.update(issue_index["members"].get(other, set()))

    # Fingerprint identity is an intersection of at least two concrete axes.  Common actors
    # alone therefore cannot fan out the query, while actor+asset/action remains lossless.
    axis_hits: list[set[str]] = []
    fingerprint = article.get("story_fingerprint") or {}
    for axis in FINGERPRINT_MATCH_AXES:
        matches: set[str] = set()
        for value in story_fingerprint.axis_values(fingerprint, axis):
            matches.update(issue_index["fingerprint"][axis].get(value, set()))
        if matches:
            axis_hits.append(matches)
    identity_counts: Counter = Counter()
    for matches in axis_hits:
        identity_counts.update(matches)
    mandatory_ids.update(issue_id for issue_id, count in identity_counts.items() if count >= 2)

    retrieval_scores: Counter = Counter()
    for _key, postings in _rare_postings(issue_index["tags"], _strong_tags(article)):
        retrieval_scores.update({issue_id: 8 for issue_id in postings})
    for _key, postings in _rare_postings(issue_index["tokens"], _tokens(article)):
        retrieval_scores.update({issue_id: 3 for issue_id in postings})
    for _key, postings in _rare_postings(issue_index["grams"], _title_grams(article)):
        retrieval_scores.update(postings)
    retrieval_ids = {
        issue_id for issue_id, _score in sorted(
            retrieval_scores.items(),
            key=lambda row: (-row[1], issue_index["order"].get(row[0], 0), row[0]),
        )[:EVIDENCE_RETRIEVAL_POOL]
    }

    remote_vector = (embeddings or {}).get(article_hash)
    local_vector = (local_embeddings or {}).get(article_hash)
    vector_ids = (
        _lsh_candidates(issue_index["remote_lsh"], remote_vector)
        | _lsh_candidates(issue_index["local_lsh"], local_vector)
    )
    vector_scores: list[tuple[float, str]] = []
    for issue_id in vector_ids:
        issue = issue_index["issues"].get(issue_id) or {}
        best = 0.0
        for member in (issue.get("members") or [])[-3:]:
            member_hash = str(member.get("hash") or "")
            for left_vector, vector_table in (
                (remote_vector, embeddings or {}), (local_vector, local_embeddings or {})
            ):
                similarity = cosine_similarity(left_vector, vector_table.get(member_hash))
                if similarity is not None:
                    best = max(best, similarity)
        vector_scores.append((best, issue_id))
    vector_head = {
        issue_id for _score, issue_id in sorted(
            vector_scores, key=lambda row: (-row[0], row[1])
        )[:EVIDENCE_VECTOR_TOP_N]
    }

    candidate_ids = retrieval_ids | vector_head | mandatory_ids
    scored: list[tuple[float, str, dict]] = []
    for issue_id in candidate_ids:
        issue = issue_index["issues"].get(issue_id)
        if issue is None:
            continue
        members = issue.get("members") or []
        card_days = [
            _parse_day(member.get("article_date") or member.get("briefing_date") or "")
            for member in members
        ]
        if article_day and card_days and all(
            card_day and abs((article_day - card_day).days) > ISSUE_WINDOW_DAYS
            for card_day in card_days
        ):
            continue
        recent = members[-3:]
        lexical = max((_cheap_issue_score(article, member) for member in recent), default=0.0)
        scored.append((lexical, issue_id, issue))
        if telemetry is not None:
            telemetry.prefilter(article_hash, issue_id, lexical)

    ranked = sorted(scored, key=lambda row: (-row[0], row[1]))
    selected_ids = {issue_id for _, issue_id, _ in ranked[:EVIDENCE_PRESELECT_TOP_N]}
    selected_ids.update(vector_head)
    selected_ids.update(mandatory_ids)
    shortlisted = [
        issue_index["issues"][issue_id]
        for issue_id in sorted(selected_ids, key=lambda key: issue_index["order"].get(key, 0))
        if issue_id in issue_index["issues"]
    ]
    if telemetry is not None:
        telemetry.retrieve(len(issue_index["issues"]), len(candidate_ids))
        telemetry.shortlist(len(shortlisted), len(mandatory_ids))
    return shortlisted


def _evidence_retrieval_canary(
    article: dict,
    issues: list[dict],
    shortlisted: list[dict],
    embeddings: dict[str, list[float]] | None,
    local_embeddings: dict[str, list[float]] | None,
    overrides: dict[str, set[str]],
    facility_entities: dict[str, set[str]] | None,
) -> tuple[list[dict], int, int, int]:
    """Exhaustively audit a fixed sample and rescue any indexed-retrieval miss."""
    selected = {str(issue.get("issue_id") or "") for issue in shortlisted}
    veto_pairs = set(overrides.get("rejected") or ()) | set(
        overrides.get("llm_rejected") or ())
    approvals = set(overrides.get("approved") or ()) | set(
        overrides.get("llm_approved") or ())
    article_day = _parse_day(article.get("article_date", ""))
    rescued: list[dict] = []
    auto_missed = review_missed = excluded_checked = 0
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "")
        if issue_id in selected:
            continue
        members = issue.get("members") or []
        card_days = [
            _parse_day(member.get("article_date") or member.get("briefing_date") or "")
            for member in members
        ]
        if article_day and card_days and all(
            card_day and abs((article_day - card_day).days) > ISSUE_WINDOW_DAYS
            for card_day in card_days
        ):
            continue
        if veto_pairs and any(
            _pair_id(article.get("hash"), member.get("hash")) in veto_pairs
            for member in members
        ):
            continue
        if (_cluster_country_conflict(article, members)
                or _cluster_facility_conflict(article, members)):
            continue
        excluded_checked += 1
        fingerprint_chain_blocked = _cluster_fingerprint_conflict(article, members)
        found_auto = found_review = False
        for reference in members[-3:]:
            pair_id = _pair_id(article.get("hash"), reference.get("hash"))
            matched, _score, diag = issue_similarity(
                article, reference, embeddings, local_embeddings, facility_entities
            )
            if (matched and diag.get("method") == "story_fingerprint"
                    and fingerprint_chain_blocked):
                matched = False
            if pair_id in approvals and not diag.get("blocked_by"):
                matched = True
            found_auto = found_auto or matched
            found_review = found_review or issue_review.in_review_band(diag)
        if found_auto or found_review:
            rescued.append(issue)
            selected.add(issue_id)
            auto_missed += int(found_auto)
            review_missed += int(found_review and not found_auto)
    return rescued, auto_missed, review_missed, excluded_checked


def attach_evidence_articles(
    news_items: list[dict],
    issues: list[dict],
    embeddings: dict[str, list[float]] | None = None,
    local_embeddings: dict[str, list[float]] | None = None,
    match_overrides: dict[str, set[str]] | None = None,
    review_candidates: list[dict] | None = None,
    facility_entities: dict[str, set[str]] | None = None,
    telemetry: issue_candidate_stats.SearchTelemetry | None = None,
) -> int:
    """미발송 기사를 이미 고정된 카드 이슈에 근거로만 부착한다.

    근거 기사는 새 이슈를 만들지 않고 다른 근거 기사의 연결 기준도 되지 않는다.
    따라서 수집 분모를 넓혀도 카드 소속·대표 제목·정렬이 바뀌지 않는다.

    이슈 전체는 한 번만 역색인한다. 기사별 쿼리는 희소 어휘·문자 n-gram·지문 축과
    임베딩 LSH 버킷에서 제한된 후보를 찾고, 수동/LLM 승인·story_id·설비는 필수
    경로로 합친다. 따라서 정밀 비교량은 기사×전체 이슈가 아니라 기사×후보 상한으로
    증가한다.
    """
    if not issues:
        return 0
    latest_card_day = max(
        (_parse_day(issue.get("last_seen", "")) for issue in issues),
        default=None,
    )
    if not latest_card_day:
        return 0
    cutoff = latest_card_day - timedelta(days=ISSUE_WINDOW_DAYS)
    evidence = [
        item for item in news_items
        if not item.get("briefing_date")
        and item.get("importance") != "noise"
        and (_parse_day(item.get("article_date", "")) or cutoff) >= cutoff
    ]
    evidence.sort(key=lambda item: (item.get("article_date") or "", item["hash"]))
    overrides = match_overrides or {"approved": set(), "rejected": set()}
    veto_pairs = set(overrides.get("rejected") or ()) | set(overrides.get("llm_rejected") or ())
    candidate_rows = review_candidates if review_candidates is not None else []
    seen_candidates = {_pair_id(row.get("left_hash"), row.get("right_hash")) for row in candidate_rows}
    issue_index = _build_evidence_issue_index(
        issues, facility_entities, embeddings, local_embeddings
    )
    canary_hashes = {
        str(item.get("hash") or "")
        for item in sorted(evidence, key=lambda row: str(row.get("hash") or ""))[
            :EVIDENCE_RETRIEVAL_CANARY
        ]
    }
    attached = 0

    for evidence_index, article in enumerate(evidence, 1):
        if evidence_index == 1 or evidence_index % 100 == 0:
            print(
                f"[build_data:progress] evidence {evidence_index}/{len(evidence)} "
                f"attached={attached}",
                flush=True,
            )
        article_day = _parse_day(article.get("article_date", ""))
        best_issue = None
        best_score = -1.0
        best_diag = None
        shortlisted = _preselect_evidence_issues(
            article, issue_index, overrides, facility_entities,
            embeddings, local_embeddings, telemetry
        )
        if str(article.get("hash") or "") in canary_hashes:
            rescued, auto_missed, review_missed, checked = _evidence_retrieval_canary(
                article, issues, shortlisted, embeddings, local_embeddings,
                overrides, facility_entities
            )
            shortlisted.extend(rescued)
            if telemetry is not None:
                telemetry.retrieval_canary(checked, auto_missed, review_missed)
        for issue in shortlisted:
            if telemetry is not None:
                telemetry.visit()
            card_members = issue["members"]
            card_days = [
                _parse_day(member.get("article_date") or member.get("briefing_date") or "")
                for member in card_members
            ]
            if article_day and card_days and all(
                card_day and abs((article_day - card_day).days) > ISSUE_WINDOW_DAYS
                for card_day in card_days
            ):
                if telemetry is not None:
                    telemetry.skip("window")
                continue
            if veto_pairs and any(
                _pair_id(article["hash"], member["hash"]) in veto_pairs
                for member in card_members
            ):
                if telemetry is not None:
                    telemetry.skip("veto")
                continue
            # 카드 묶음과 같은 전체 거부권 — 근거 기사도 묶음 멤버로 화면에 실리고
            # 데이터 게이트의 검사 대상이라, 여기서 빠지면 같은 구멍이 남는다.
            if _cluster_country_conflict(article, card_members):
                if telemetry is not None:
                    telemetry.skip("country_conflict")
                continue
            if _cluster_facility_conflict(article, card_members):
                if telemetry is not None:
                    telemetry.skip("facility_conflict")
                continue
            if telemetry is not None:
                telemetry.compare(article["hash"])
            # 지문 경로만 묶음 전체와 대조한다 — 약한 근거는 연쇄하지 못한다.
            # (왜 이 경로만인지는 _cluster_fingerprint_conflict 주석에 실측이 있다.)
            fingerprint_chain_blocked = _cluster_fingerprint_conflict(article, card_members)
            # 근거끼리 chaining 되지 않도록 카드 멤버만 앵커로 사용한다.
            for reference in card_members[-3:]:
                pair_id = _pair_id(article["hash"], reference["hash"])
                recorded = False
                matched, score, diag = issue_similarity(
                    article, reference, embeddings, local_embeddings, facility_entities
                )
                # 승인 override 보다 위에 있어도 사람 판정을 뒤집지 않는다:
                # 이 게이트는 blocked_by 를 건드리지 않으므로 아래 승인 분기가
                # 그대로 matched 를 되살린다. 그리고 판정이 뒤집힌 쌍은
                # `elif not matched` 로 흘러 검수 큐에 남는다 — 정말 같은
                # 사건이면 사람이 다시 이을 자리가 있어야 한다.
                if matched and diag.get("method") == "story_fingerprint" and (
                        fingerprint_chain_blocked):
                    matched = False
                    diag = {**diag, "method": "fingerprint_chain_blocked"}
                    if telemetry is not None:
                        telemetry.fingerprint_chain_demoted()
                if pair_id in veto_pairs:
                    if telemetry is not None:
                        telemetry.pair(article["hash"], "veto",
                                       issue["issue_id"], _lexical_score(diag))
                    continue
                if pair_id in overrides.get("approved", set()) and not diag.get("blocked_by"):
                    matched, score = True, max(score, 1.0)
                    diag = {**diag, "method": "manual_approved"}
                elif pair_id in overrides.get("llm_approved", set()) and not diag.get("blocked_by"):
                    matched, score = True, max(score, 0.99)
                    diag = {**diag, "method": "llm_approved"}
                elif not matched:
                    is_candidate, candidate_method, candidate_score = is_review_candidate(diag)
                    if is_candidate and pair_id not in seen_candidates:
                        seen_candidates.add(pair_id)
                        recorded = True
                        candidate_rows.append({
                            "candidate_id": pair_id,
                            "left_hash": reference["hash"],
                            "right_hash": article["hash"],
                            "left_date": reference.get("briefing_date") or reference.get("article_date"),
                            "right_date": article.get("article_date"),
                            "left_title": reference.get("title_kr") or reference.get("title"),
                            "right_title": article.get("title_kr") or article.get("title"),
                            "candidate_method": candidate_method,
                            "candidate_score": round(candidate_score, 4),
                            "shared_facility_entities": diag.get("shared_facility_entities") or [],
                            "diagnostics": diag,
                            "review_state": "pending",
                            "member_role": "evidence",
                        })
                if telemetry is not None:
                    telemetry.pair(article["hash"], _pair_outcome(matched, diag, recorded),
                                   issue["issue_id"], _lexical_score(diag))
                if matched and score > best_score:
                    best_issue, best_score = issue, score
                    best_diag = {**diag, "reference_hash": reference["hash"]}
        if telemetry is not None:
            telemetry.settle((best_issue or {}).get("issue_id"))
        if best_issue is None:
            continue
        best_issue.setdefault("evidence_members", []).append(article)
        best_issue["match_diagnostics"].append({
            "hash": article["hash"],
            "score": best_score,
            "member_role": "evidence",
            **(best_diag or {}),
        })
        attached += 1
    return attached


def card_cluster_snapshot(issues: list[dict]) -> list[dict]:
    """Capture the card-only order, membership, and representative before P1 evidence attachment."""
    return [
        {
            "representative_hash": str((issue.get("representative") or {}).get("hash") or ""),
            "representative_title": str(
                (issue.get("representative") or {}).get("title_kr")
                or (issue.get("representative") or {}).get("title")
                or ""
            ),
            "card_hashes": [str(member.get("hash") or "") for member in issue.get("members") or []],
        }
        for issue in issues
    ]


def assert_card_clusters_unchanged(before: list[dict], issues: list[dict]) -> dict:
    """Fail the build if P1 evidence attachment changes card order, membership, or title."""
    after = card_cluster_snapshot(issues)
    if after != before:
        raise ValueError("p1_card_cluster_regression")
    encoded = json.dumps(after, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "passed": True,
        "definition_version": "same-run-card-cluster-v1",
        "card_count": len(after),
        "signature": hashlib.sha256(encoded).hexdigest()[:16],
    }


_DETAIL_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}")
# 요지와 제목이 어긋난 기사 — 빌드 로그로 사람에게 넘긴다.
_DETAIL_MISMATCHES: list[dict] = []
# 요지가 제 기사 제목과 이만큼도 안 겹치면 다른 기사의 본문이다.
_DETAIL_MIN_OVERLAP = 0.30


def usable_detail(article: dict) -> str:
    """그 기사의 요지가 맞을 때만 돌려준다. 아니면 빈 문자열.

    수집 단계(`article_body.matches_title`)가 걸러야 할 일이지만 그 판정이
    느슨했던 기간의 기록이 아카이브에 남아 있다 — 2026-08-10 라이브에서
    '한수원, 신규 대형 원전 및 SMR 부지 후보지 선정' 이슈의 '기사 내용'이
    **해외건설 수주** 이야기였다. 수집기를 고쳐도 이미 쌓인 것은 안 고쳐지므로
    화면에 내보내는 이 자리에서 한 번 더 본다(빌드마다 아카이브 전체를 다시
    지나가므로 과거분까지 즉시 걷힌다).

    실측(아카이브 요지 237건): 중앙값 0.80, 0.30 미만은 5건(2.1%)이고 그 안에
    위 오탐 사례가 들어 있다. 해외 기사 65건은 0.30 미만이 0건 — 번역 제목이라
    불리하지 않다. 버리면 요지 없이 제목·요약으로 돌아갈 뿐이지만, 남기면
    다른 사건의 본문이 '이 기사의 내용'으로 전문가에게 제시된다.
    """
    detail = str(article.get("detail") or "").strip()
    title = str(article.get("title_kr") or article.get("title") or "")
    if not detail or not title:
        return detail
    tokens = set(_DETAIL_TOKEN_RE.findall(title))
    if not tokens:
        return detail
    haystack = detail.lower()
    hits = 0
    for token in tokens:
        needle = token.lower()
        # 조사 한 글자를 떼고도 본다('원전이' → '원전') — keei_match 와 같은 이유.
        if needle in haystack or (len(needle) > 2 and needle[:-1] in haystack):
            hits += 1
    if hits / len(tokens) >= _DETAIL_MIN_OVERLAP:
        return detail
    # **조용히 버리지 않는다.** 요지는 원문 본문에서, 제목·요약은 모델에서 나온다.
    # 둘이 어긋나면 둘 중 하나가 틀린 것인데 겹침만으로는 어느 쪽인지 못 가른다 —
    # 그리고 2026-08-11 실사고에서 틀린 쪽은 **제목·요약**이었다(원 제목 '해외건설
    # 500억 달러 시대 겨냥…'인 기사가 '한수원, 신규 원전 부지 후보지 선정'으로
    # 둔갑해 must_read 로 올라갔다. 사람이 selection_overrides 로 내렸다).
    # 요지만 지우면 **거짓말은 남고 진실이 사라진다.** 사람이 볼 수 있게 남긴다.
    # 어긋난 당사자인 **요지를 같이 남긴다.** 2026-08-15 이전에는 제목 두 개
    # (title_kr·title)만 적었는데, 국내 기사는 둘이 같은 문자열이라 로그에 같은 말이
    # 두 번 찍혔고 정작 무엇과 어긋났는지가 빠져 있었다 — 어느 쪽이 틀렸는지 사람이
    # 가르라는 자리인데 가를 재료가 없었다.
    _DETAIL_MISMATCHES.append({
        "hash": str(article.get("hash") or "")[:8],
        "title_kr": title[:60],
        "title": str(article.get("title") or "")[:60],
        "detail": detail[:80],
        "overlap": hits / len(tokens),
        "importance": str(article.get("importance") or ""),
    })
    return ""


def _story_contract(article: dict) -> dict:
    """브리핑 선정 단계의 story-cluster 메타데이터를 웹 계약으로 투영한다.

    웹의 issue cluster는 여러 날짜의 후속 보도를 잇는 계층이고, story는 같은 날
    같은 underlying event를 여러 기사에서 하나로 접은 계층이다. 둘을 섞지 않고
    별도 필드로 보존해야 `동일 보도 통합`과 `후속 이슈 추적`을 각각 설명할 수 있다.
    """
    try:
        article_count = max(1, int(article.get("story_article_count") or 1))
    except (TypeError, ValueError):
        article_count = 1
    try:
        outlet_count = max(1, int(article.get("story_outlet_count") or 1))
    except (TypeError, ValueError):
        outlet_count = 1
    try:
        tier1_count = max(0, int(article.get("story_tier1_count") or 0))
    except (TypeError, ValueError):
        tier1_count = 0
    try:
        independent_count = max(0, int(article.get("story_independent_outlet_count") or 0))
    except (TypeError, ValueError):
        independent_count = 0
    fingerprint = article.get("story_fingerprint")
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    titles = article.get("story_related_titles")
    if not isinstance(titles, list):
        titles = []
    sources = article.get("story_sources")
    if not isinstance(sources, list):
        sources = []
    contexts = article.get("story_context")
    if not isinstance(contexts, list):
        contexts = []
    hashes = article.get("story_article_hashes")
    if not isinstance(hashes, list):
        hashes = []
    members = article.get("story_members")
    if not isinstance(members, list):
        members = []
    return {
        "story_id": str(article.get("story_id") or story_cluster.fallback_story_id(article)),
        "story_id_source": str(article.get("story_id_source") or "legacy_hash"),
        "story_id_trust": str(article.get("story_id_trust") or "legacy"),
        "story_identity_version": int(article.get("story_identity_version") or 0),
        "story_contract_available": bool(article.get("story_contract_available", False)),
        "story_article_count": article_count,
        "story_outlet_count": outlet_count,
        "story_tier1_count": tier1_count,
        "story_independent_outlet_count": independent_count,
        "story_relation": str(article.get("story_relation") or "single"),
        "story_reason": str(article.get("story_reason") or "")[:300],
        "story_dedup_stage": str(article.get("story_dedup_stage") or ""),
        "story_fingerprint": fingerprint,
        "story_article_hashes": [str(x) for x in hashes[:12] if str(x).strip()],
        "story_related_titles": [str(x)[:180] for x in titles[:12] if str(x).strip()],
        # 운영 콘솔의 수동 분리 단위 (hash↔제목 짝). 옛 회차에는 없다.
        "story_members": [x for x in members[:16] if isinstance(x, dict) and x.get("hash")],
        "story_sources": [x for x in sources[:12] if isinstance(x, dict)],
        "story_context": [x for x in contexts[:8] if isinstance(x, dict)],
    }


def story_fingerprint_similarity(left: dict, right: dict) -> tuple[float, dict]:
    """선정 단계 fingerprint가 두 날짜의 issue 연결을 얼마나 지지하는지 계산한다.

    축 정의와 대조는 ``story_fingerprint`` 모듈 하나에 있다. 예전에는 이 함수와
    ``issue_continuity.fingerprint_similarity`` 가 각자 별칭표를 들고 있다가
    어긋났고, 이쪽 표만 프롬프트가 실제로 쓰는 ``drivers`` 를 빠뜨려 **원인 축을
    한 번도 읽지 못했다**(그 모듈 첫머리에 실측이 있다).

    겹친 축뿐 아니라 **어긋난 축(contested)** 도 함께 돌려준다 — 판정은 겹침만
    세는 것이 아니라 어긋남을 거부권으로 쓴다.
    """
    comparison = story_fingerprint.compare(
        left.get("story_fingerprint"), right.get("story_fingerprint")
    )
    return comparison.similarity, {
        "shared": comparison.shared,
        "compared": comparison.compared,
        "contested": comparison.contested,
    }


def _article_view(article: dict, member_role: str = "card") -> dict:
    return {
        "hash": article["hash"],
        "article_date": article["article_date"],
        "briefing_date": article.get("briefing_date"),
        "title_kr": article["title_kr"],
        "summary": article.get("summary", ""),
        "detail": usable_detail(article),
        "domain": article.get("domain", ""),
        "publisher": article.get("publisher", ""),
        "source_type": article.get("source_type", "unknown"),
        "evidence_role": article.get("evidence_role", "unknown"),
        "source_tier": article.get("source_tier", 3),
        "article_type": article.get("article_type", ""),
        "event_date": article.get("event_date"),
        "event_date_type": article.get("event_date_type", "unknown"),
        "region": article.get("region", ""),
        "countries": article.get("countries") or [],
        "topics": article.get("topics") or [],
        "url": source_url(article),
        "importance": article.get("importance", ""),
        # 텔레그램 카드 번호. 오디오가 이슈 순서를 여기서 되찾는다.
        "brief_rank": article.get("brief_rank"),
        "brief_region": article.get("brief_region", ""),
        **_story_contract(article),
        "member_role": member_role,
    }


def _clip(text: str, limit: int) -> str:
    """예산을 못 맞출 때만 쓰는 마지막 수단. 잘렸다는 사실을 …로 남긴다."""
    text = text.strip()
    return text if len(text) <= limit else text[:max(1, limit - 1)].rstrip() + "…"


def latest_change_line(current: list[dict], history: list[dict]) -> str:
    """추적 이슈의 '이전 상태 → 현재 상태' 한 문장. 이전이 없으면 빈 문자열.

    예전에는 비교 대상이 없거나 재진술이면 최신 기사 요약을 그대로 돌려줬다.
    그 결과 카드의 `이번에 달라진 점` 자리에 변화가 아니라 기사 요약이 앉았다 —
    2026-08-09 감사에서 무작위 20건 중 19건이 그랬고(화살표 보유 10/127),
    `change_rate` 75.6% 는 재료가 늘어서가 아니라 이 fallback 이 만든 숫자였다.
    실무자가 그 줄을 '달라진 점'으로 읽고 보고서에 인용하면 그대로 사고다.

    변화 문장은 정의상 이전 상태가 있어야 성립한다. 못 만들면 만들지 않는다 —
    빈 자리는 클러스터 입력을 넓혀 history 를 채워서 메울 일이지(로드맵 P1-1)
    요약으로 덮을 일이 아니다. 8/8 에 재진술 기준을 summary → 제목+시사점으로
    바꾼 것도 같은 착시였다: 변화 행이 비는 진짜 원인은 판정 기준이 아니라
    history 가 비어 있는 것이었다.
    """
    if not current:
        return ""
    newest = max(
        current,
        key=lambda member: (member.get("article_date") or "", _representative_key(member)),
    )
    previous_candidates = [
        member for member in history
        if (member.get("article_date") or "") < (newest.get("article_date") or "")
    ]
    if not previous_candidates:
        return ""

    previous = max(
        previous_candidates,
        key=lambda member: (member.get("briefing_date") or "", member.get("article_date") or "", _representative_key(member)),
    )
    previous_text = previous.get("summary") or previous.get("title_kr") or previous.get("title") or ""
    before = flow_takeaway(previous_text, limit=48).strip().rstrip(".!?")
    if not before:
        return ""
    text = newest.get("summary") or newest.get("title_kr") or newest.get("title") or ""
    after = flow_takeaway(text, limit=112).strip().rstrip(".!?")
    if not after or _is_restatement(before, after):
        return ""
    # flow_takeaway 의 limit 은 상한이 아니다 — 안전하게 종결할 수 없으면 첫 문장을
    # 그대로 돌려준다(설계). 그래서 한쪽만으로 140 을 넘기는 쌍이 나온다(실측 15건 중 6).
    # 예전에는 그 초과분이 요약 fallback 으로 새어나가 상한이 지켜지는 것처럼 보였다 —
    # 상한을 지키던 코드가 곧 이 함수를 망가뜨린 코드였던 셈이다. fallback 을 걷은 이상
    # 예산은 여기서 직접 맞춘다. 진짜 변화를 글자 수 때문에 버리지는 않는다.
    #
    # 깎는 순서는 앞쪽(직전 상태)부터다. 뒤쪽(현재 상태)은 카드의 제목·요약이 이미
    # 말하고 있어 잘려도 복구되지만, 앞쪽은 화면 어디에도 없는 유일한 정보라
    # 30자 아래로는 내리지 않는다 — 그 밑으로 가면 비교 자체가 성립하지 않는다.
    room = CHANGE_LINE_LIMIT - len(" → ") - len(".")
    if len(before) + len(after) > room:
        before = _clip(before, max(30, room - len(after)))
    if len(before) + len(after) > room:
        after = _clip(after, room - len(before))
    return f"{before} → {after}."


def card_visible_text(title: str, implication: str, why_important: str) -> str:
    """카드에 실제로 노출되는 문장들. 중복 판정의 유일한 기준선이다.

    장문 summary 는 카드에서 내려가 상세로만 간다(2026-08-08 UX 개편). 예전에는
    summary 를 기준으로 재진술을 걸렀는데, '달라진 것'은 대표 기사 요약으로
    만들어지므로 summary 와 겹치는 게 당연했고 그 결과 168건 중 12건만 살아남아
    변화 행이 사실상 비어 있었다(오늘 브리핑 19건 중 0건). 카드에 안 보이는
    문장과 비교해 카드의 정보를 지우고 있었던 셈이다.
    """
    return f"{title or ''} {implication or why_important or ''}".strip()


def change_line_for_card(current: list[dict], history: list[dict], visible: str) -> str:
    """카드에 이미 보이는 문장을 그대로 되풀이하는 변화 문장은 비운다.

    `visible` 은 `card_visible_text` 가 만든 제목+영향 한 덩어리다. 화살표가 있는
    문장은 이전 상태 대비 새 정보이므로 그대로 둔다.
    """
    change = latest_change_line(current, history)
    if not change or "→" in change:
        return change
    return "" if _is_restatement(visible, change) else change


def card_change_display(change: str, title: str, implication: str, why_important: str) -> str:
    """카드에 실을 변화 문장 — 화면에 이미 보이는 문장의 재진술을 걷어낸다.

    화살표 문장의 뒤쪽(B)은 현재 요약으로 만들어지므로 구조적으로 카드와
    겹친다(2026-08-04 실측: 오늘 브리핑 8건 중 2건이 summary 포함률 1.00 —
    카드가 제목·요약·변화로 같은 말을 세 번 했다). 오늘 상태는 제목과 둘째
    줄이 이미 말하고 있으니, 화면에 없는 정보는 앞쪽(직전 상태)뿐이다 —
    그쪽만 '직전 브리핑:' 라벨로 남긴다.

    비교 기준은 `card_visible_text` 하나뿐이다 — summary 는 더 이상 카드에 없다.

    `latest_change` 원본은 그대로 둔다 — changed_issue_count 의 화살표 집계와
    RSS·og 설명이 그 필드를 세고 있다. 이 함수는 표시 전용 필드를 만든다.

    **라벨은 여기서 붙이지 않는다**(2026-08-10). 예전에는 `직전 브리핑: ` 을
    문장 앞에 이어 붙였는데, 화면은 그 줄에 다시 `달라진 것` 라벨을 세운다 —
    "달라진 것 / 직전 브리핑: 그리스 국무회의는 …위원회를 구성했다" 처럼
    **바뀐 것을 묻는 라벨 아래 바뀌기 전 상태만** 남는다(라이브 실측 10/160).
    문장은 사실만 담고, 무슨 자리인지는 `change_kind` 로 알려 화면이 라벨을
    고르게 한다.
    """
    change = str(change or "").strip()
    if not change or "→" not in change:
        return change
    before, _, after = change.partition("→")
    before = before.strip().rstrip(",")
    visible = card_visible_text(title, implication, why_important)
    if not _is_restatement(visible, after.strip()):
        return change
    if not before or _is_restatement(visible, before):
        return ""
    return before


def finalize_card_fields(rows: list[dict]) -> None:
    """카드 3칸의 역할 분리를 마지막에 한 번 강제한다.

    행을 만들 때 한 번 걸렀는데도 중복이 통과하던 이유는 순서였다 —
    `issue_insight.apply` 가 행 생성 **뒤에** `implication` 을 덮어쓰기 때문에,
    변화 문장은 덮어쓰기 전(대개 빈) 해석과 비교되고 있었다. 실측(2026-08-08
    issue-aeda14d4): 비교 시점 `implication=''` → 통과 → 나중에 채워진 해석이
    변화 문장과 같은 말이 됨.

    그래서 모든 해석 단계가 끝난 뒤 여기서 한 번만 정한다. 우선순위는 스펙 순서
    그대로 — 관찰 가능한 사실(`달라진 것`)이 영향(`왜 중요해요`)보다 앞이고,
    겹치면 뒤쪽을 버린다.

    `latest_change` 원본은 손대지 않는다. changed_issue_count·atlas·RSS 가 이미
    그 필드로 집계를 끝냈고, 여기서 만드는 건 표시 전용 두 필드다.
    """
    for row in rows:
        title = str(row.get("title") or "")
        # visible 에 빈 문자열을 넘겨 제목만 기준으로 삼는다 — 해석은 아직 안 골랐다.
        display = card_change_display(str(row.get("latest_change") or ""), title, "", "")
        if display and _is_restatement(title, display):
            display = ""

        why = ""
        for candidate in (row.get("implication"), row.get("why_important")):
            candidate = str(candidate or "").strip()
            if not candidate or _is_restatement(title, candidate):
                continue
            if display and _is_restatement(display, candidate):
                continue
            why = candidate
            break

        row["change_display"] = display
        # 그 문장이 '지금 달라진 것'인지 '직전 상태'인지. 화면이 라벨을 고르는
        # 근거이며, 이것이 없으면 바뀌기 전 상태가 '달라진 것' 이라는 이름으로
        # 나간다. 화살표가 있었는데 앞쪽만 남았으면 그건 직전 상태다.
        source = str(row.get("latest_change") or "").strip()
        if not display:
            row["change_kind"] = ""
        elif "→" in source and display != source:
            row["change_kind"] = "previous"
        else:
            row["change_kind"] = "change"
        # 카드가 읽는 유일한 '왜 중요해요' 필드. 화면에서 or 폴백을 하면 이 계약이
        # 두 곳에 흩어져 드리프트한다.
        row["card_why"] = why


def _is_primary_source(article: dict) -> bool:
    return article.get("evidence_role") == "primary" or article.get("source_tier") == 1


def _source_identity(article: dict) -> str:
    """같은 매체가 쓴 여러 기사를 하나의 출처로 묶는 키."""
    publisher = _NORM_RE.sub("", str(article.get("publisher") or "").lower())
    if publisher:
        return f"pub:{publisher}"
    domain = str(article.get("domain") or "").lower()
    # 구글 뉴스는 집계 도메인이라 매체를 식별하지 못한다. 매체명이 비어 있으면
    # 서로 다른 출처로 합치지 않고 기사 단위로 남긴다(과대 계상 방지).
    if not domain or "news.google." in domain:
        return f"hash:{article.get('hash') or id(article)}"
    return f"dom:{domain}"


def _is_official_source(article: dict) -> bool:
    """규제기관·사업자의 공식 문서인지."""
    return article.get("evidence_role") == "primary" or article.get("source_type") == "official"


def _is_independent_source(article: dict) -> bool:
    """독립 취재 보도인지. 보도자료 전재(distributed_claim)는 재인용으로 제외한다."""
    return article.get("evidence_role") == "independent"


def _story_source_identity(source: dict) -> str:
    """story 근거 목록의 매체를 기사 출처와 **같은 키 체계**로 정규화한다.

    두 목록이 다른 키를 쓰면 같은 매체가 두 출처로 세어져 검증 상태가 부풀어 오른다.
    'Le Monde' 가 기사 쪽에서는 `pub:lemonde`, story 쪽에서는 `le monde` 로 잡히던
    자리다 — 검증 배지는 과대 계상하는 순간 배지가 아니라 소음이 된다.
    """
    return _source_identity({
        "publisher": source.get("publisher") or "",
        "domain": source.get("domain") or "",
        # publisher·domain 이 둘 다 비면 기사 단위로 남긴다(합치지 않는다).
        "hash": f"story:{source.get('identity') or ''}",
    })


def _story_sources_of(article: dict) -> list[dict]:
    """선정 단계에서 이 기사에 접힌 보도 매체 목록.

    수집 단계에서 접힌 기사(raw_sources)까지 story_cluster 가 이미 합쳐 둔 목록이다.
    예전에는 수집 단계에서 통째로 삭제돼 여기 오지 못했고, 그래서 하나의 사건을 열
    매체가 보도해도 검증은 '단일 출처'였다.
    """
    values = article.get("story_sources")
    return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []


def pick_open_question(members: list[dict]) -> str:
    """이슈에 붙일 '아직 확정되지 않은 것' 한 문장.

    대표 기사 필드를 그대로 복사하면 안 된다 — 미확정 내용이 대표 기사에는 없고
    같은 이슈의 다른 공식 기사에만 있는 경우가 흔하다. 공식 → tier1 → 최신 순으로
    비어 있지 않은 첫 문장을 고른다.
    """
    def latest_first(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=_representative_key, reverse=True)

    filled = [m for m in members if str(m.get("open_question") or "").strip()]
    if not filled:
        return ""
    for group in (
        [m for m in filled if _is_official_source(m)],
        [m for m in filled if m.get("source_tier") == 1],
        filled,
    ):
        for member in latest_first(group):
            return str(member["open_question"]).strip()
    return ""


def pick_detail(members: list[dict], representative: dict) -> tuple[str, str]:
    """이슈 상세에 실을 기사 요지와 그 출처 기사 제목.

    대표 기사만 보면 안 된다 — 본문 수집은 매체마다 성패가 갈려서(실측 성공률 85%,
    Reuters·FT 는 봇 차단으로 0%) 대표 기사에만 요지가 없는 경우가 흔하다.
    **가장 최신 기사부터** 찾는다. 오래된 멤버의 요지를 쓰면 제목은 새 사건인데
    본문은 옛 상태인 조합이 나온다 — 사용자가 지적한 그 모순이다.

    `members` 는 **카드 멤버만** 넣는다. 근거 기사(`evidence_members`)는 관련기사
    목록과 검증에만 쓰이고 대표 설명으로 승격되지 않는다 — 범위는 호출부가 정하고
    `build_issue_catalog` 주석에 실측이 있다.
    """
    # 출처 표기는 **대표 기사가 아닐 때만** 의미가 있다. 대표 기사면 그 제목이
    # 바로 위 h2 라서 같은 문장을 두 번 쓰는 꼴이 된다("대다수가 다는 표시는
    # 신호가 아니다"는 이 저장소의 기존 원칙).
    representative_detail = usable_detail(representative)
    if representative_detail:
        return representative_detail, ""

    def newest_first(member: dict) -> tuple:
        # _representative_key 는 중요도·점수가 앞이라 날짜가 뒤로 밀린다. 여기서
        # 필요한 정렬은 '최신'이므로 날짜를 앞에 둔다.
        return (str(member.get("article_date") or ""), _representative_key(member))

    for member in sorted(members, key=newest_first, reverse=True):
        detail = usable_detail(member)
        if detail:
            return detail, str(member.get("title_kr") or member.get("title") or "")
    return "", ""


def verification_state(articles: list[dict], checked_at: str = "") -> dict:
    """이슈 근거를 4단계 검증 상태로 요약한다.

    - official: 규제기관·사업자 공식 문서가 근거에 포함
    - corroborated: 재인용 관계를 제거한 독립 출처 2곳 이상이 연결
    - partial: 독립 출처 1곳만 확인
    - unverified: 배포 자료 재인용뿐이거나 근거가 부족

    근거가 없으면 문장을 지어내지 않고 unverified로 남긴다.

    **기사 행 하나 = 출처 하나가 아니다.** 하나의 카드는 같은 사건을 쓴 여러 매체를
    접은 story 이고, 그 매체 목록이 `story_sources` 다. 수집 단계가 중복을 지우던
    시절에는 이 목록이 비어 있어서 `corroborated`(독립 출처 2곳 이상)가 사실상
    도달 불가능한 상태였다 — 열 매체가 보도한 사건도 '단일 출처·확인 중'으로
    표시됐다. 이제 story 매체를 함께 세므로 이 상태가 실제 복수 출처 확인을 뜻한다.
    """
    official = {_source_identity(article) for article in articles if _is_official_source(article)}
    independent = {
        _source_identity(article) for article in articles if _is_independent_source(article)
    }
    all_sources = {_source_identity(article) for article in articles}

    story_outlets: set[str] = set()
    for article in articles:
        for source in _story_sources_of(article):
            identity = _story_source_identity(source)
            story_outlets.add(identity)
            all_sources.add(identity)
            role = str(source.get("evidence_role") or "").lower()
            if role == "primary":
                official.add(identity)
            elif role == "independent":
                independent.add(identity)
    independent -= official

    if official:
        status = "official"
    elif len(independent) >= 2:
        status = "corroborated"
    elif len(independent) == 1:
        status = "partial"
    else:
        status = "unverified"

    official_article = next((article for article in articles if _is_official_source(article)), None)
    independent_labels = list(dict.fromkeys(
        label for label in (
            [(article.get("publisher") or article.get("domain") or "").strip()
             for article in articles if _is_independent_source(article)]
            + [(source.get("publisher") or source.get("domain") or "").strip()
               for article in articles for source in _story_sources_of(article)
               if str(source.get("evidence_role") or "").lower() == "independent"]
        ) if label
    ))
    return {
        "status": status,
        "label": VERIFICATION_LABELS[status],
        "source_count": len(all_sources),
        "independent_source_count": len(independent),
        "official_source_count": len(official),
        # 같은 사건을 실제로 보도한 매체 수. 카드 개수가 아니라 매체 개수다.
        "story_outlet_count": len(story_outlets),
        "checked_at": checked_at,
        "checks": [
            {
                "kind": "official",
                "passed": bool(official),
                "label": "공식 원문 포함",
                "detail": ((official_article or {}).get("publisher")
                           or (official_article or {}).get("domain") or ""),
                "url": source_url(official_article or {}),
            },
            {
                "kind": "multi",
                "passed": len(independent) >= 2,
                "label": "독립 출처 2곳 이상 연결",
                "detail": " · ".join(independent_labels[:3]),
            },
            {
                "kind": "outlets",
                "passed": len(story_outlets) >= 2,
                "label": "복수 매체가 같은 사건을 보도",
                "detail": f"보도 매체 {len(story_outlets)}곳" if story_outlets else "",
            },
        ],
    }


EMPTY_HEADLINE = "오늘 새로 연결된 원자력 이슈가 없습니다"


def _fit_headline(candidates: list[object]) -> str:
    """후보 문장 중 히어로 한 줄에 들어가는 첫 문장을 고른다."""
    fallback = ""
    for candidate in candidates:
        headline = flow_takeaway(candidate, limit=HEADLINE_LIMIT).strip().rstrip(".!?")
        if not headline:
            continue
        if len(headline) <= HEADLINE_LIMIT:
            return headline
        fallback = fallback or headline
    if not fallback:
        return ""
    # flow_takeaway가 안전하게 종결하지 못한 문장은 원문을 지키느라 길이를 넘긴다.
    # 히어로 h1이 문단으로 번지지 않도록 마지막 단계에서만 말줄임한다.
    return f"{fallback[:HEADLINE_LIMIT - 1].rstrip()}…"


SYNTHESIS_LIMIT = 90  # 봇 종합 문장 상한 — daily_lead.LEAD_LIMIT와 동일 계약
# 종합 문장이 그날 이슈 제목과 공유해야 하는 최소 의미 토큰 수.
# 하루 이슈에 공통 주제가 없으면 모델이 "비워 두라"는 지시를 어기고 최대한
# 일반적인 문장으로 뭉갠다. 실측(2026-08-03 라이브):
#   "국내외에서 원자력 및 에너지 정책과 현실에 대한 다양한 논의와 상황 변화가
#    있었습니다" → 이슈 제목과 공유 토큰 0개
# 같은 날 구체적 문장이라면 6~7개가 겹친다. 0~1개면 아무 말도 안 한 것이다.
SYNTHESIS_MIN_SHARED = 2
_CLAUSE_BOUNDARIES = ("며, ", "고, ", "지만 ", "으나 ", ", ")


def _fit_synthesis(text: str) -> str:
    """봇이 만든 종합 문장을 빌드 단계에서 한 번 더 길이 검증한다.

    생성 단계(daily_lead.py)가 90자를 지키지만, 계약 위반 데이터가 와도
    히어로 h1이 문단으로 번지지 않도록 절 경계에서 자른다.
    """
    text = " ".join(str(text or "").split()).strip()
    if not text or len(text) <= SYNTHESIS_LIMIT:
        return text
    window = text[:SYNTHESIS_LIMIT]
    best = -1
    for sep in _CLAUSE_BOUNDARIES:
        pos = window.rfind(sep)
        if pos > best:
            best = pos + len(sep.rstrip())
    if best > 20:
        return window[:best].rstrip().rstrip(",")
    return window[: SYNTHESIS_LIMIT - 1].rstrip() + "…"


def synthesis_is_substantive(lead: str, issue_rows: list[dict]) -> bool:
    """종합 문장이 실제로 무언가를 말하는지.

    그날 이슈 제목과 의미 토큰을 나눠 갖지 못하면 구체적인 사실을 하나도
    담지 못한 문장이다. 그런 문장은 제목 폴백(구체적 이슈 제목)보다 못하다.
    """
    if not lead:
        return False
    titles = " ".join(str(row.get("title") or "") for row in issue_rows)
    shared = _keei_shared(_keei_match_tokens(lead), _keei_match_tokens(titles))
    return len(shared) >= SYNTHESIS_MIN_SHARED


def _evidence_chips(evidence: list, issue_rows: list[dict]) -> list[dict]:
    """종합 문장의 근거 기사 hash를 그날 이슈 카드로 연결한다 (최대 3개)."""
    hash_to_issue: dict[str, dict] = {}
    for row in issue_rows:
        for article in row.get("related_articles") or []:
            article_hash = article.get("hash")
            if article_hash and article_hash not in hash_to_issue:
                hash_to_issue[article_hash] = row
    chips: list[dict] = []
    seen_issues: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        row = hash_to_issue.get(item.get("hash") or "")
        if not row or row["issue_id"] in seen_issues:
            continue
        seen_issues.add(row["issue_id"])
        chips.append({"issue_id": row["issue_id"], "title": row["title"]})
        if len(chips) >= 3:
            break
    return chips


_REPEAT_SHARED_TOKENS = 3  # 어제 헤드라인과 이만큼 겹치면 같은 사건으로 본다


def _is_repeat_of(title: str, previous_headline: str) -> bool:
    """어제 히어로가 말한 것과 같은 사건인지."""
    previous = _keei_match_tokens(previous_headline)
    if not previous:
        return False
    return len(_keei_shared(_keei_match_tokens(title), previous)) >= _REPEAT_SHARED_TOKENS


def daily_lead(issue_rows: list[dict], previous_headline: str = "") -> dict:
    """히어로 문장과 그 문장의 성격(kind)을 함께 만든다.

    kind가 오버라인 문구를 정한다. 실제로 이어지는 이슈일 때만 change로 표시한다.

    설계 근거 (라이브 실측 2026-08-02):
      - 예전에는 `latest_change` 화살표 뒤쪽을 헤드라인으로 썼는데, 그건 **생성
        문장**이라 "…발표했습니다", "…경고했다" 같은 기사체가 그대로 h1 에
        올라왔다. 반면 이슈 **제목**은 이미 개조식이라 훨씬 헤드라인답다
        (실측 비교: change 경로 8/1·8/2 vs issue 경로 7/31).
      - 어제와 같은 이슈가 오늘도 1위면 이틀 연속 같은 문장이 떴다(헝가리 원전
        가동 중단이 8/1·8/2 연속). '무엇이 달라졌는가'라고 묻고 어제와 같은
        답을 하면 제목이 거짓말이 된다 → 전날 헤드라인과 겹치는 이슈는 건너뛴다.
    """
    if not issue_rows:
        return {"headline": EMPTY_HEADLINE, "kind": "empty"}

    fresh = [row for row in issue_rows
             if not _is_repeat_of(str(row.get("title") or ""), previous_headline)]
    if not fresh:  # 전부 어제와 겹치면 순위를 그대로 따른다(억지로 비우지 않는다)
        fresh = issue_rows

    lead = fresh[0]
    headline = _fit_headline([lead.get("title"), lead.get("summary")])
    if not headline:
        return {"headline": EMPTY_HEADLINE, "kind": "empty"}
    # 이어지는 이슈면 '무엇이 달라졌는가', 처음 잡힌 이슈면 '오늘의 핵심 이슈'
    kind = "change" if lead.get("previous_article_count") else "issue"
    return {"headline": headline, "kind": kind}


def daily_headline(issue_rows: list[dict]) -> str:
    return daily_lead(issue_rows)["headline"]


def _brief_position(members: list[dict]) -> dict:
    """그날 텔레그램에서 이 이슈가 처음 나온 자리 (지역, 번호).

    이슈 하나에 국내·해외 기사가 함께 접히는 날이 있다(region '국내·해외').
    그때는 **국내를 먼저 읽는** 프로그램 순서에 맞춰 국내 자리를 대표로 쓴다.
    번호가 하나도 없으면(옛 회차·미발송) 빈 값으로 두고, 읽는 쪽이 기존 정렬로
    물러난다.
    """
    order = {"국내": 0, "국내·해외": 0, "해외": 1}
    best: tuple[int, int, str] | None = None
    for member in members:
        rank = member.get("brief_rank")
        if not isinstance(rank, int) or rank <= 0:
            continue
        region = str(member.get("brief_region") or member.get("region") or "")
        key = (order.get(region, 1), rank, region)
        if best is None or key < best:
            best = key
    if best is None:
        return {"brief_rank": None, "brief_region": ""}
    return {"brief_rank": best[1], "brief_region": best[2]}


def order_issue_rows(issue_rows: list[dict]) -> None:
    """브리핑 이슈를 국내·해외 순위를 번갈아 가며 배열한다 (제자리 정렬).

    봇은 국내와 해외를 **별도 풀에서 각자 캡으로** 뽑는다(국내 3 / 해외 6).
    그런데 웹이 이걸 raw 점수 하나로 다시 줄 세우면서 문제가 생겼다 — 출처 등급
    보너스(tier1 +3.0)가 국제 전문지 전용이라 국내 매체는 구조적으로 0점이고,
    그 결과 국내 이슈가 통째로 하위권으로 밀렸다(실측 8/1 브리핑에서 국내 3건이
    6·8·9위). 점수를 손대 공신력 등급을 왜곡하는 대신, 봇이 이미 만든 두 갈래
    구조를 화면에서도 유지한다: 각 지역 안의 순위(1위끼리, 2위끼리)를 맞물린다.
    """
    def within_region(row: dict) -> tuple:
        # 편집 고정(editor_pin)은 **자기 지역 안에서만** 작동한다. 지역 맞물림
        # 구조를 넘어 끌어올리면 해외 이슈가 국내 자리를 먹는다.
        return (row.get("editor_pin", 0), row["importance"] == "must_read",
                row["sort_score"], row["last_seen"])

    domestic = sorted((r for r in issue_rows if r["region"] == "국내"),
                      key=within_region, reverse=True)
    overseas = sorted((r for r in issue_rows if r["region"] != "국내"),
                      key=within_region, reverse=True)
    rank = {id(row): index for group in (domestic, overseas)
            for index, row in enumerate(group)}

    # 아직 일어나지 않은 일은 같은 순위에서 뒤로 민다.
    #
    # 사용자 지적(2026-08-10): "3대 메가프로젝트 전담조직, 왜 이게 가장 먼저 볼
    # 이슈야". 그날은 must_read 가 0건이라 위 등급 기준이 아무 일도 안 했고, 남은
    # selection_score 가 '국내 정책 결정'에 크게 가중돼(korea_relevance 3,
    # policy_materiality 3) **회의 예고 기사**가 1번 자리를 가져갔다. 그 자리는
    # '가장 먼저 볼 이슈'라고 불린다 — 아직 안 열린 회의를 거기 세우면 라벨이
    # 거짓이 된다.
    #
    # 근거량(독립 출처 수·검증 등급)으로 가르는 안을 먼저 시도했다가 물렸다.
    # 24일치 백테스트에서 선두가 3~4일 바뀌는데 전부 나쁜 방향이었다 — 원안위
    # 계속운전 발표가 이란 경고에 밀렸다. verification 은 **출처 품질**이지
    # 중요도가 아니고, 국내 규제기관 뉴스는 단일 출처·보도자료라 구조적으로
    # 낮게 나온다(official 13건 중 12건이 independent_source_count 0).
    #
    # 한계: event_date_type 은 선두 24건 중 22건이 unknown 이다. 명시적으로
    # scheduled 로 찍힌 것만 밀리므로, 판정 못 한 예고 기사는 그대로 통과한다.
    # 그래도 오탐이 없는 쪽을 고른다 — 실측 214건 중 밀리는 이슈는 3건이고
    # 선두가 바뀌는 날은 지적받은 그 하루뿐이다.
    def is_scheduled(row: dict) -> int:
        rep = row.get("representative_article") or {}
        return 1 if rep.get("event_date_type") == "scheduled" else 0

    issue_rows.sort(key=lambda row: (
        rank[id(row)],
        0 if row["importance"] == "must_read" else 1,
        is_scheduled(row),
        -row["sort_score"],
    ))
    for row in issue_rows:
        row.pop("sort_score", None)
        row.pop("editor_pin", None)   # 정렬 전용 — 화면에 편집 흔적을 남기지 않는다


PUBLICATION_NEW_DAYS = 14  # 이 기간 안의 발간물에 NEW 뱃지


PUBLICATION_ORG_ALIASES = {
    "에경연": "에너지경제연구원(KEEI)",
    "에너지경제연구원": "에너지경제연구원(KEEI)",
    "OECD 원자력기구": "OECD 원자력기구(NEA)",
    "국제원자력기구": "국제원자력기구(IAEA)",
    "국제에너지기구": "국제에너지기구(IEA)",
    "미국 에너지정보청": "미국 에너지정보청(EIA)",
}

# KEEI 인사이트 목차 ↔ 이슈 매칭.
#
# 점수만으로는 판정할 수 없다는 것이 실측으로 확인됐다(2026-08-02): 로컬 n-gram
# 코사인 상위권을 벤더명만 같은 오매칭이 차지했고(Rolls-Royce 0.323 > 진짜 같은
# 사건인 EIB·체르나보다 0.239), IDF 가중 토큰 중복도 3위부터 다른 규칙·다른
# 발전소가 섞였다. 발표·계획·건설 같은 흔한 토큰이 점수를 지배한다.
# 그래서 파이썬은 후보만 좁히고 판정은 keei_match(LLM)에 맡긴다.
KEEI_CANDIDATE_MIN_SHARED = 2      # 의미 토큰 공유 최소 개수
KEEI_CANDIDATES_PER_ISSUE = 2      # 이슈당 LLM 에 물어볼 최대 후보
# 총 상한은 품질 필터가 아니라 폭주 방지선이다. 점수는 진짜 매칭을 상위로
# 올리지 못한다(실측: 진짜 쌍이 134개 중 82위) — 상한을 낮게 잡으면 그냥
# 진짜 매칭을 버리는 셈이다. 판정은 캐시되고 KEEI 는 격주간이라 첫 빌드
# 이후 증분은 새 호 몫뿐이다.
KEEI_CANDIDATE_CAP = 150
KEEI_REFS_PER_ISSUE = 2


def _keei_match_tokens(text: str) -> set[str]:
    """매칭 판정에 쓸 의미 토큰 — 일반어는 버린다."""
    return {token for token in _text_tokens(text) if token not in _GENERIC_TAGS}


def _keei_shared(left: set[str], right: set[str]) -> set[str]:
    """공유 토큰 — 한국어 조사가 붙어 갈라진 같은 낱말을 접두 일치로 흡수한다.

    실측: '영덕군과' 와 '영덕군' 이 다른 토큰이 되어 같은 사건이 후보에서
    탈락할 뻔했다. 조사 목록을 두는 대신(원자'로' 처럼 낱말 끝과 구분이 안 됨)
    한쪽이 다른 쪽의 접두인 경우를 같은 낱말로 본다. 후보 생성 단계라
    과대 매칭은 LLM 이 걸러 주므로 재현율을 택한다.
    """
    shared = set()
    for token in left:
        if token in right:
            shared.add(token)
            continue
        for other in right:
            if len(token) >= 2 and len(other) >= 2 and (
                    token.startswith(other) or other.startswith(token)):
                shared.add(min(token, other, key=len))
                break
    return shared


def keei_entries(publications: dict) -> list[dict]:
    """발간물에서 KEEI 목차 항목을 펼친다. 제목 줄만 — 본문은 저장하지 않는다."""
    entries = []
    for publication in publications.get("items") or []:
        toc = publication.get("toc")
        if not isinstance(toc, dict):
            continue
        for text in [toc.get("issue_title") or ""] + list(toc.get("briefs") or []):
            text = str(text or "").strip()
            if text:
                entries.append({"text": text, "publication": publication})
    return entries


def keei_candidates(issue_rows: list[dict], entries: list[dict]) -> list[dict]:
    """IDF 가중 토큰 중복으로 LLM 에 물어볼 후보만 좁힌다.

    이 점수는 순위를 매기는 용도일 뿐 판정이 아니다 — 최종 판정은 LLM 이 한다.
    """
    if not issue_rows or not entries:
        return []
    issue_tokens = [
        (row, _keei_match_tokens(f"{row['title']} {row.get('summary', '')}"))
        for row in issue_rows
    ]
    entry_tokens = [(entry, _keei_match_tokens(entry["text"])) for entry in entries]

    document_frequency = Counter()
    for _, tokens in issue_tokens + entry_tokens:
        document_frequency.update(tokens)
    total = len(issue_tokens) + len(entry_tokens)

    def inverse_frequency(token: str) -> float:
        return math.log((1 + total) / (1 + document_frequency[token])) + 1.0

    candidates = []
    for row, tokens in issue_tokens:
        scored = []
        for entry, other in entry_tokens:
            shared = _keei_shared(tokens, other)
            if len(shared) < KEEI_CANDIDATE_MIN_SHARED:
                continue
            weight = sum(inverse_frequency(token) for token in shared)
            scored.append((weight / max(1.0, math.sqrt(len(other))), entry))
        scored.sort(key=lambda item: -item[0])
        for weight, entry in scored[:KEEI_CANDIDATES_PER_ISSUE]:
            candidates.append({
                "score": weight,
                "pair_id": f"{row['issue_id']}--{hashlib.sha1(entry['text'].encode('utf-8')).hexdigest()[:10]}",
                "issue_id": row["issue_id"],
                "issue_title": row["title"],
                "keei_item": entry["text"],
                "publication": entry["publication"],
            })
    # 상한에 걸릴 때만 점수를 쓴다 — 없는 것보다는 나은 순서일 뿐이다.
    candidates.sort(key=lambda row: (-row["score"], row["pair_id"]))
    kept = candidates[:KEEI_CANDIDATE_CAP]
    kept.sort(key=lambda row: row["pair_id"])  # 결정적 순서 — 캐시·배치 안정
    return kept


def attach_keei_refs(issue_rows: list[dict], publications: dict) -> dict:
    """같은 사건을 다루는 이슈 카드에 KEEI 인사이트 참조를 붙인다.

    LLM 이 same_event 로 판정한 것만 붙인다. 키가 없거나 호출이 실패하면 아무
    것도 붙이지 않는다 — 틀린 연결은 누락보다 해롭다.
    """
    entries = keei_entries(publications)
    candidates = keei_candidates(issue_rows, entries)
    if not candidates:
        return {"candidates": 0, "attached": 0, "status": "no_candidates"}

    verdicts, stats = keei_match.match_pairs([
        {"pair_id": row["pair_id"], "issue_title": row["issue_title"],
         "keei_item": row["keei_item"]}
        for row in candidates
    ])

    by_issue: dict[str, list[dict]] = defaultdict(list)
    for row in candidates:
        if verdicts.get(row["pair_id"]):
            by_issue[row["issue_id"]].append(row)

    attached = 0
    for row in issue_rows:
        matches = by_issue.get(row["issue_id"])
        if not matches:
            continue
        refs, seen = [], set()
        for match in matches:
            publication = match["publication"]
            if publication["url"] in seen:
                continue
            seen.add(publication["url"])
            refs.append({
                "title": publication.get("title", ""),
                "url": publication.get("url", ""),
                "date": publication.get("date", ""),
                "org_kr": publication.get("org_kr", ""),
                "item": match["keei_item"],
            })
            if len(refs) >= KEEI_REFS_PER_ISSUE:
                break
        row["keei_refs"] = refs
        attached += 1
    stats["attached"] = attached
    return stats


def load_publications(now: datetime | None = None) -> dict:
    """pubs_fetch.py 가 커밋한 발간물 상태 파일 → 웹 표시용 뷰.

    파일이 없거나 깨져도 빈 구조를 반환한다 — 발간물 부재가 사이트를 죽이면
    안 된다 (빈 배열이라도 publications.json 은 항상 생성되는 계약).
    """
    empty = {"generated_at": "", "items": [], "sources": {}}
    try:
        raw = json.loads((BOT_DIR / "publications.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, dict):
        return empty
    now = now or datetime.now(KST)
    new_cutoff = (now - timedelta(days=PUBLICATION_NEW_DAYS)).strftime("%Y-%m-%d")
    items = []
    dropped: dict[str, int] = {}
    echoed = 0
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        reason = publication_drop_reason(item)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        display_date = str(item.get("date") or item.get("fetched_at") or "")
        org_kr = str(item.get("org_kr") or "")
        view = {
            "id": item.get("id") or "",
            "org": item.get("org") or "",
            "org_kr": PUBLICATION_ORG_ALIASES.get(org_kr, org_kr),
            "kind": item.get("kind") or "",
            "title": title,
            "url": url,
            "date": display_date,
            "is_new": bool(display_date and display_date >= new_cutoff),
            "relevance": publication_relevance(item),
        }
        for optional in ("pdf_url", "toc", "title_kr", "gist"):
            if item.get(optional):
                view[optional] = item[optional]
        # 기관 바이라인이 바로 위에 서므로 제목이 기관명으로 시작할 이유가 없다.
        if view.get("title_kr"):
            view["title_kr"] = strip_org_prefix(
                view["title_kr"], item.get("org"), org_kr)
        if gist_adds_nothing(view.get("gist", ""), view.get("title_kr", "")):
            view.pop("gist", None)
            echoed += 1
        items.append(view)
    # 조용히 자르지 않는다 — 규칙이 과하게 잡으면 이 줄에서 먼저 티가 난다.
    if dropped:
        detail = " / ".join(f"{key} {count}건" for key, count in sorted(dropped.items()))
        print(f"[build_data] 발간물 제외 {sum(dropped.values())}건 ({detail})")
    if echoed:
        print(f"[build_data] 발간물 gist 숨김 {echoed}건 (제목 재진술)")
    relevance_counts: dict[str, int] = {}
    for view in items:
        key = view["relevance"]
        relevance_counts[key] = relevance_counts.get(key, 0) + 1
    if items:
        detail = " / ".join(f"{key} {count}건"
                            for key, count in sorted(relevance_counts.items()))
        print(f"[build_data] 발간물 관련성 {len(items)}건 ({detail}) "
              f"— technical 은 기본 접힘")
    return {
        "generated_at": now.isoformat(),
        "items": items,
        "sources": raw.get("last_checked") or {},
        "relevance_counts": relevance_counts,
    }


def load_daily_leads() -> dict[str, dict]:
    """봇이 하루 1회 생성한 '오늘의 한 문장'. 없으면 빈 dict (히어로가 폴백)."""
    try:
        raw = json.loads((BOT_DIR / "daily_leads.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    leads = raw.get("leads")
    return leads if isinstance(leads, dict) else {}


def _short_hash_index(issue_rows: list[dict]) -> dict[str, dict]:
    """hash 앞 8자리 → 이슈. 봇이 프롬프트 토큰을 아끼려 8자리만 남기는데,
    이슈 카탈로그는 전체 hash 로 색인돼 있어 그대로 조회하면 하나도 안 걸린다."""
    by_short: dict[str, dict] = {}
    for row in issue_rows:
        for article in row.get("related_articles") or []:
            short = str(article.get("hash") or "")[:8]
            if short and short not in by_short:
                by_short[short] = row
    return by_short


def attach_calendar_issues(calendar: dict, issue_rows: list[dict]) -> int:
    """달력의 근거 기사가 이슈로 묶여 있으면 그 이슈를 가리키게 한다.

    대부분은 안 걸린다 — 실측 2026-08-29: 일정 기사 27건 중 이슈로 연결되는
    것은 4건이다. 일정을 말하는 기사는 대개 브리핑에 선정되지 않은 공지·예고
    기사이기 때문이다. 그래서 달력의 상세는 이슈 다이얼로그에 기대지 않고
    **스스로 근거를 갖는다**(문장·제목·출처). 이슈 링크는 있을 때만 얹는
    덤이고, 없다고 칩이 못 서는 일은 없다.
    """
    by_hash: dict[str, str] = {}
    for row in issue_rows:
        for article in ([row.get("representative_article") or {}]
                        + list(row.get("related_articles") or [])):
            article_hash = str(article.get("hash") or "")
            if article_hash:
                by_hash.setdefault(article_hash, str(row.get("issue_id") or ""))
    linked = 0
    for event in calendar.get("events") or []:
        for source in event.get("sources") or []:
            issue_id = by_hash.get(source.get("hash") or "")
            if issue_id:
                source["issue_id"] = issue_id
                # 빈 문자열이 이미 실려 있으므로 setdefault 로는 안 채워진다.
                if not event.get("issue_id"):
                    event["issue_id"] = issue_id
                linked += 1
    return linked


def _enrich_weekly_report(raw: dict, issue_rows: list[dict],
                          by_short: dict[str, dict]) -> dict:
    """저장된 주간 리포트 → 화면용.

    문장마다 evidence_hashes 를 이슈 상세 링크로 바꾼다. 전역 key_events 만으로는
    어떤 근거가 어느 문장 것인지 알 수 없어 모든 문장에 같은 칩이 붙는다.
    """
    report = dict(raw)
    # 발송 claim/confirm 상태와 Telegram API 응답은 운영 메타데이터다. 저장 파일은
    # GitHub Actions의 durable state로 함께 쓰지만 공개 웹 payload로는 절대 내보내지
    # 않는다(chat id/message id 등이 응답에 포함될 수 있다).
    report.pop("_automation", None)

    def chips(short_hashes) -> list[dict]:
        # 매핑 실패는 칩만 비우고 넘어간다 — 화면 전체가 깨지면 안 된다.
        out, seen = [], set()
        for short in short_hashes or []:
            row = by_short.get(str(short)[:8])
            if not row or row["issue_id"] in seen:
                continue
            seen.add(row["issue_id"])
            out.append({"issue_id": row["issue_id"], "title": row["title"]})
        return out[:2]

    for key in ("policy_shifts", "theme_moves"):
        rows = [dict(r) for r in (report.get(key) or []) if isinstance(r, dict)]
        for row in rows:
            row["evidence"] = chips(row.get("evidence_hashes"))
            row.pop("evidence_hashes", None)
        report[key] = rows
    report["key_events"] = [r for r in (report.get("key_events") or [])
                            if isinstance(r, dict)]
    report["watchpoints"] = [str(w) for w in (report.get("watchpoints") or []) if w]

    # 결정적 코너(핵심사건·국가별 단신·발간물·예정)는 weekly_bot 이 이미 골라
    # 저장했다. 여기서 다시 고르지 않는다 — 텔레그램과 웹이 다른 목록을 내면
    # 그것이 곧 두 표면의 불일치다. 웹이 더하는 것은 두 가지뿐이다.
    #   · 사건 → 이슈 상세 링크 (웹에만 있는 병합 결과)
    #   · 기관 표기 정규화 (발간물 탭과 같은 규칙)
    # 핵심사건에 이미 선 이슈는 아래 코너에서 뺀다. 봇은 사건(clusterer) 단위로
    # 겹침을 걷어내지만, 웹에는 그보다 넓은 이슈 병합 결과가 있어 서로 다른
    # 사건 둘이 같은 상세 페이지를 가리킬 수 있다 (실측 W34: 두산 기자재
    # 공급계약과 SK이노 사업 공조가 한 이슈). 두 줄이 같은 곳으로 가면 독자에게
    # 그것은 두 사건이 아니라 반복이다.
    taken: set[str] = set()
    for key in ("top_stories", "country_briefs", "upcoming"):
        # 예정 코너는 화면에서 꺼져 있다(weekly_sections.SHOW_WEEKLY_UPCOMING).
        # 저장본에는 남아 있지만 공개 페이로드에는 싣지 않는다 — 내보내지 않는
        # 코너를 브라우저까지 보내 두면 언젠가 누가 그걸 그린다. 키 자체는 남겨
        # 스키마를 흔들지 않는다.
        if key == "upcoming" and not weekly_sections.SHOW_WEEKLY_UPCOMING:
            report[key] = []
            continue
        rows = []
        for raw_row in report.get(key) or []:
            if not isinstance(raw_row, dict):
                continue
            row = dict(raw_row)
            issue = by_short.get(str(row.get("key") or "")[:8])
            if issue:
                if key != "top_stories" and issue["issue_id"] in taken:
                    continue
                row["issue_id"] = issue["issue_id"]
                taken.add(issue["issue_id"])
            rows.append(row)
        report[key] = rows
    pubs = []
    for raw_pub in report.get("publications") or []:
        if not isinstance(raw_pub, dict):
            continue
        row = dict(raw_pub)
        org = PUBLICATION_ORG_ALIASES.get(str(row.get("org") or "").strip(),
                                          str(row.get("org") or "").strip())
        row["org"] = org
        row["title"] = strip_org_prefix(str(row.get("title") or ""), org)
        pubs.append(row)
    report["publications"] = pubs
    # 이슈 수는 여기서 다시 센다. 봇은 제목 정규화로 어림잡을 수밖에 없지만
    # (임베딩·LLM 병합 결과가 없다) 웹에는 실제 병합 결과가 있다.
    merged = merged_issue_count(issue_rows, report.get("week_start"), report.get("week_end"))
    if merged is not None:
        report["source_issue_count"] = merged
    return report


def _stored_weekly_reports() -> dict:
    try:
        raw = json.loads((BOT_DIR / "weekly_reports.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    reports = raw.get("reports")
    return reports if isinstance(reports, dict) else {}


def load_weekly_reports(issue_rows: list[dict]) -> dict[str, dict]:
    """week_start(YYYY-MM-DD) → 그 주 리포트. **전부** 내보낸다.

    예전에는 `reports[max(reports)]` 하나만 내보냈다. 그러면 화면이 어느 날짜를
    열든 같은 주의 결론을 붙인다 — 7월 브리핑에도 8월 8~14일 결론이 뜨고, 이번 주
    브리핑에는 지난주 결론이 '오늘 분석'처럼 붙는다. 실측(2026-08-16): 저장된
    2주치(W32·W33) 중 W33 하나만 사이트로 나갔다.

    키를 week_id 가 아니라 week_start 로 잡는 이유: week_id 는 ISO 주차(월~일)인데
    리포트의 실제 구간은 토~금(금요일 실행, 직전 7일)이라 둘이 어긋난다. 화면이
    날짜로 주차를 계산해 맞춰야 하므로 구간 시작일이 유일하게 안전한 키다.
    """
    by_short = _short_hash_index(issue_rows)
    out: dict[str, dict] = {}
    for raw in _stored_weekly_reports().values():
        if not isinstance(raw, dict):
            continue
        start = raw.get("week_start")
        if not isinstance(start, str) or not start:
            continue
        out[start] = _enrich_weekly_report(raw, issue_rows, by_short)
    return out


def load_weekly_report(issue_rows: list[dict]) -> dict | None:
    """가장 최신 주간 리포트 하나. 트렌드 탭의 '주간 판세' 패널이 쓴다 —
    그 패널은 선택 날짜와 무관한 독립 코너이고 기간을 스스로 표시한다."""
    reports = _stored_weekly_reports()
    if not reports:
        return None
    return _enrich_weekly_report(reports[max(reports)], issue_rows,
                                 _short_hash_index(issue_rows))


def merged_issue_count(issue_rows: list[dict], start: object, end: object) -> int | None:
    """그 주에 움직인 고유 이슈 수. 기사 수를 쓰면 후속 보도가 많은 주가
    실제보다 풍성해 보인다."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    count = 0
    for row in issue_rows:
        last_seen = str(row.get("last_seen") or "")
        if start <= last_seen <= end:
            count += 1
    return count


def collect_open_questions(issue_rows: list[dict], limit: int = 5) -> list[dict]:
    """그 주의 '아직 확정되지 않은 것' 모음.

    그냥 모으면 같은 내용이 여러 기사에서 중복된다. 이슈 단위로 한 번씩만 세고,
    최신·중요도순 상위 몇 개만 남긴다.
    """
    seen: set[str] = set()
    out: list[dict] = []
    ordered = sorted(
        issue_rows,
        key=lambda row: (row.get("last_seen") or "",
                         row.get("importance") == "must_read"),
        reverse=True,
    )
    for row in ordered:
        text = str(row.get("open_question") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"text": text,
                    "evidence": [{"issue_id": row["issue_id"], "title": row["title"]}]})
        if len(out) >= limit:
            break
    return out


def selection_view(stats: dict | None) -> dict:
    """봇이 남긴 선정 통계를 브리핑 행에 실을 형태로.

    이슈 0건일 때 '기준 미달'·'후보 없음'·'파이프라인 실패'를 화면에서 가르려면
    이 값이 필요하다. 통계가 없는 날(기능 도입 이전)은 None 으로 둬서 프론트가
    단정하지 않게 한다 — 0 으로 채우면 '후보가 없었다'는 거짓 진술이 된다.
    """
    if not stats:
        return {"candidate_count": None, "below_floor_count": None,
                "pipeline_status": None, "pipeline_ran_at": None}

    def total(key: str) -> int:
        return sum(int((stats.get(region) or {}).get(key) or 0)
                   for region in ("domestic", "overseas"))

    return {
        "candidate_count": total("candidate_count"),
        "below_floor_count": total("below_floor_count"),
        "pipeline_status": stats.get("pipeline_status"),
        "pipeline_ran_at": stats.get("generated_at"),
    }


def empty_briefing_row(briefing_date: str, stats: dict | None) -> dict:
    """선정이 통째로 0건인 날의 브리핑 행.

    브리핑 날짜는 '발송된 기사'에서만 나오기 때문에(dates = news_items 의
    briefing_date), 하한에 전부 걸린 날은 briefings 에 행 자체가 생기지 않는다.
    그러면 화면이 below_floor_count 를 볼 수 없어 '기준 미달' 상태가 영영 안 뜬다.
    후보가 있었다는 기록이 있으면 빈 행이라도 남긴다.
    """
    return {
        "date": briefing_date,
        "article_count": 0,
        "issue_count": 0,
        **selection_view(stats),
        "domestic_count": 0,
        "overseas_count": 0,
        "primary_source_count": 0,
        "tracked_issue_count": 0,
        "verified_issue_count": 0,
        # daily_lead 는 같은 상태에서 EMPTY_HEADLINE 을 낸다. 여기만 빈 문자열이라
        # 두 경로가 같은 날을 다르게 적고 있었다 — headline 은 히어로가 아니라
        # 아카이브 목록과 RSS 가 쓰는 값이라(build_briefings 주석) 비면 그날 행이
        # 통째로 빈칸이 되고, RSS 는 or 폴백에 걸려 "이번 주 원자력, 무엇이
        # 달라졌나"라는 사실과 다른 제목을 내보낸다. 0건인 날은 0건이라고 적는다.
        "headline": EMPTY_HEADLINE,
        "headline_kind": "empty",
        "headline_evidence": [],
        "changed_issue_count": 0,
        "highlights": [],
        "highlight_issues": [],
        "issues": [],
    }


def build_briefings(news_items: list[dict], issues: list[dict], checked_at: str = "",
                    daily_leads: dict | None = None,
                    selection_stats: dict | None = None,
                    selection_overrides: dict | None = None) -> list[dict]:
    # 오래된 날부터 돈다 — 히어로가 '어제 무엇을 말했는지' 알아야 같은 사건을
    # 이틀 연속 올리지 않는다. 반환 직전에 최신순으로 뒤집는다(briefings[0] 이
    # 최신이라는 계약은 스모크·앱이 함께 의존한다).
    dates = sorted({item["briefing_date"] for item in news_items if item.get("briefing_date")})
    briefings = []
    previous_headline = ""

    for briefing_date in dates:
        current_articles = [item for item in news_items if item.get("briefing_date") == briefing_date]
        issue_rows = []
        hidden_hashes: set[str] = set()
        for issue in issues:
            current = [member for member in issue["members"] if member["briefing_date"] == briefing_date]
            if not current:
                continue
            # 편집 override ② — 판정 단위는 기사가 아니라 **이슈 클러스터**다.
            # 기사 하나만 지우면 같은 클러스터의 다른 멤버가 briefing_date 를 갖고
            # 있어 카드가 그대로 남는다. 이슈 병합은 LLM 검수까지 거친 2차 결과이므로
            # 여기(=최종 클러스터)에서 적용해야 올바른 묶음에 걸린다.
            verdict = override_verdict(current, briefing_date, selection_overrides or {})
            if verdict == "hide":
                hidden_hashes.update(str(member.get("hash") or "") for member in current)
                continue
            history = [member for member in issue["members"] if member["briefing_date"] < briefing_date]
            representative = max(current, key=_representative_key)
            regions = {member.get("region") for member in current if member.get("region")}
            reasons = []
            for member in sorted(current, key=_representative_key, reverse=True):
                reasons.extend(member.get("selection_reasons") or [])

            topic_counts = Counter(
                topic for member in history + current for topic in (member.get("topics") or [])
            )
            tag_counts = Counter(
                tag for member in history + current for tag in (member.get("canonical_tags") or [])
                if tag not in _GENERIC_TAGS
            )

            timeline = sorted(history + current,
                              key=lambda member: (member["article_date"], member["briefing_date"], member["hash"]),
                              reverse=True)
            tracked_briefings = len({member["briefing_date"] for member in timeline})
            implication, why_important = split_interpretation(representative)
            change_line = change_line_for_card(
                current, history,
                card_visible_text(representative["title_kr"], implication, why_important),
            )
            issue_detail, issue_detail_source = pick_detail(timeline, representative)
            report_topic, report_why, report_angles = pick_report_metadata(current)
            issue_rows.append({
                "issue_id": issue["issue_id"],
                "identity_status": issue.get("identity_status", "ok"),
                "identity_diagnostics": issue.get("identity_diagnostics") or [],
                "legacy_issue_id": issue.get("legacy_issue_id", ""),
                "status": "ongoing" if history else "new",
                "first_seen": issue["first_seen"],
                "last_seen": briefing_date,
                "title": representative["title_kr"],
                "summary": representative.get("summary", ""),
                "detail": issue_detail,
                "detail_source": issue_detail_source,
                "implication": implication,
                "why_important": why_important,
                # 그날 보고서 검토 추천을 받은 기사가 이 이슈에 있으면 그 주제.
                # 추천은 그날의 판단이라 이번 브리핑분(current)에서만 본다.
                "report_pick": report_topic,
                "report_pick_why": report_why,
                "report_pick_angles": report_angles,
                # 대표 기사가 아니라 이슈 전체에서 고른다 — 미확정 내용은 공식
                # 기사에만 있고 대표 기사에는 없는 경우가 흔하다.
                "open_question": pick_open_question(timeline),
                "latest_change": change_line,
                "change_display": card_change_display(
                    change_line, representative["title_kr"], implication, why_important
                ),
                "verification": verification_state(timeline, checked_at),
                "region": "국내·해외" if len(regions) > 1 else next(iter(regions), ""),
                # 이 이슈를 그날 텔레그램에서 처음 만난 자리. 이슈 하나에 그날
                # 기사가 여럿 접혀 있으면 **가장 앞 번호**가 그 자리다 — 듣는
                # 사람은 목록을 위에서부터 보므로 뒤 번호를 기준으로 하면
                # 오디오가 화면보다 늦게 그 주제를 꺼낸다.
                **_brief_position(current),
                "importance": representative.get("importance", ""),
                "selection_reasons": list(dict.fromkeys(reasons))[:2],
                **_story_contract(representative),
                "topics": [topic for topic, _ in topic_counts.most_common(3)],
                "tags": [tag for tag, _ in tag_counts.most_common(6)],
                "current_article_count": len(current),
                "previous_article_count": len(history),
                "tracked_briefings": tracked_briefings,
                "article_count": len(timeline),
                "representative_article": _article_view(representative),
                "related_articles": [_article_view(member) for member in timeline],
                "sort_score": float(representative.get("selection_score") or 0),
                # 사람이 고정한 순위. 화면에는 표시하지 않는다 — 편집 사유는
                # 대외 공개용 문장이 아니다.
                "editor_pin": {"promote": 1, "demote": -1}.get(verdict, 0),
            })

        # 숨긴 이슈의 기사는 그날 집계에서도 빠져야 한다 — 카드는 사라졌는데
        # '오늘 수집 기사 N건'만 그대로면 화면이 스스로를 부정한다.
        if hidden_hashes:
            current_articles = [item for item in current_articles
                                if str(item.get("hash") or "") not in hidden_hashes]

        order_issue_rows(issue_rows)

        # headline 은 아카이브 목록(bt-headline)과 날짜 이동에도 쓰이므로 항상
        # 채운다. 히어로가 그 문장을 띄울지는 headline_kind 가 정한다.
        lead = daily_lead(issue_rows, previous_headline)
        # headline 은 계속 채운다 — 아카이브 목록과 RSS 가 이 값을 쓴다. 히어로만
        # 이 문장을 더 이상 화면에 올리지 않는다(app.js).
        headline_evidence: list[dict] = []
        previous_headline = lead["headline"]
        briefings.append({
            "date": briefing_date,
            "article_count": len(current_articles),
            "issue_count": len(issue_rows),
            **selection_view((selection_stats or {}).get(briefing_date)),
            "domestic_count": sum(1 for item in current_articles if item.get("region") == "국내"),
            "overseas_count": sum(1 for item in current_articles if item.get("region") == "해외"),
            "primary_source_count": sum(1 for item in current_articles if _is_primary_source(item)),
            "tracked_issue_count": sum(1 for row in issue_rows if row.get("previous_article_count", 0) > 0),
            "verified_issue_count": sum(
                1 for row in issue_rows
                if row["verification"]["status"] in {"official", "corroborated"}
            ),
            "headline": lead["headline"],
            "headline_kind": lead["kind"],
            "headline_evidence": headline_evidence,
            "changed_issue_count": sum(
                1 for row in issue_rows if "→" in str(row.get("latest_change") or "")
            ),
            "highlights": [row["title"] for row in issue_rows[:3]],
            "highlight_issues": [
                {"issue_id": row["issue_id"], "title": row["title"]}
                for row in issue_rows[:3]
            ],
            "issues": issue_rows,
        })
    # 하한에 전부 걸려 발송이 0건인 날은 위 루프가 못 만든다(날짜 자체가 기사에서
    # 나오므로). 통계에만 남은 날을 빈 행으로 채워 화면이 사유를 말할 수 있게 한다.
    # 기사가 하나도 없던 날짜 범위 밖까지 거슬러 올라가지는 않는다.
    if selection_stats and dates:
        floor_date = min(dates)
        for day, stats in selection_stats.items():
            if day in dates or day < floor_date:
                continue
            briefings.append(empty_briefing_row(day, stats))
        briefings.sort(key=lambda row: row["date"])
    briefings.reverse()  # 최신순 — briefings[0] 이 최신이라는 계약
    return briefings


# 엔티티 매칭은 entity_match.py 로 옮겼다 — discovery(수집 계층)도 같은 판정을
# 쓰기 때문이다. 이름은 그대로 재노출해 기존 호출부·테스트가 계속 동작한다.
ENTITY_REGISTRY_FILE = BOT_DIR / "entity_registry.json"
from entity_match import (  # noqa: E402
    ENTITY_MATCH_POLICIES,
    ENTITY_MIN_HANGUL,
    ENTITY_MIN_LATIN,
    ENTITY_PARTICLE_MAX,
    ENTITY_TYPES,
    _ENTITY_LATIN_RUN_RE,
    _entity_alias_entries,
    _entity_match_token,
    _entity_norm_latin,
    entity_ids_for_members,
)
from entity_match import load_entity_registry as _load_entity_registry  # noqa: E402


def load_entity_registry(path: Path = None) -> list[dict]:
    """BOT_DIR 를 존중하는 얇은 감싸개 — 경로 계산만 build_data 몫이다."""
    return _load_entity_registry(path or ENTITY_REGISTRY_FILE)


def build_entities_view(issue_catalog: list[dict], registry: list[dict], generated_at: str) -> dict:
    """entities.json — 항상 생성한다(레지스트리가 없으면 빈 목록). 0건 엔티티도
    싣는다: 엔티티 페이지가 '조용함'을 말할 수 있어야 한다(허브 노출은 프론트가
    issue_count 로 거른다). aliases 는 클라이언트 검색용으로 공개하고,
    match_policy 는 내부 매칭 규칙이라 싣지 않는다."""
    per_entity: dict[str, dict] = {}
    for row in issue_catalog:
        for entity_id in row.get("entity_ids") or []:
            bucket = per_entity.setdefault(entity_id, {"issue_ids": [], "article_count": 0, "last_seen": ""})
            bucket["issue_ids"].append(row["issue_id"])
            bucket["article_count"] += int(row.get("article_count") or 0)
            bucket["last_seen"] = max(bucket["last_seen"], row.get("last_seen") or "")
    entities = []
    for entity in registry:
        bucket = per_entity.get(entity["id"], {"issue_ids": [], "article_count": 0, "last_seen": ""})
        entities.append({
            "id": entity["id"],
            "name_kr": entity["name_kr"],
            "name_en": entity["name_en"],
            "type": entity["type"],
            "countries": entity["countries"],
            "aliases": entity["aliases"],
            "issue_count": len(bucket["issue_ids"]),
            "article_count": bucket["article_count"],
            "latest_issue_date": bucket["last_seen"],
            "issue_ids": bucket["issue_ids"],
        })
    # 이슈 수 내림차순 → 최근 포착일 내림차순 → id 오름차순 (안정 정렬 합성)
    entities.sort(key=lambda e: e["id"])
    entities.sort(key=lambda e: (e["issue_count"], e["latest_issue_date"]), reverse=True)
    return {"generated_at": generated_at, "entities": entities}


def report_entity_stats(registry: list[dict], issue_catalog: list[dict]) -> None:
    """빌드 스탯 + 오탐 경고 휴리스틱 — 게이트가 아니라 계기판(atlas 와 같은 원칙)."""
    if not registry:
        print("[build_data] 엔티티: 레지스트리 0건 — 매칭 생략")
        return
    total_issues = len(issue_catalog)
    linked_issues = [row for row in issue_catalog if row.get("entity_ids")]
    counts = Counter()
    for row in issue_catalog:
        for entity_id in row.get("entity_ids") or []:
            counts[entity_id] += 1
    active = sum(1 for c in counts.values() if c > 0)
    avg = (sum(len(row.get("entity_ids") or []) for row in issue_catalog) / total_issues) if total_issues else 0
    top = ", ".join(f"{eid} {cnt}" for eid, cnt in counts.most_common(10))
    print(f"[build_data] 엔티티: 등록 {len(registry)} → 활성 {active} / 0건 {len(registry) - active} · "
          f"연결 이슈 {len(linked_issues)}/{total_issues} ({(len(linked_issues) / total_issues * 100) if total_issues else 0:.0f}%) · "
          f"이슈당 평균 {avg:.2f} · 상위: {top}")
    if avg > 4:
        print("[build_data] ⚠ 엔티티 이슈당 평균 4 초과 — 범용어 오탐 의심, 별칭·정책 점검")
    if total_issues and len(linked_issues) / total_issues < 0.30:
        print("[build_data] ⚠ 엔티티 연결 이슈 비율 30% 미만 — 별칭 부족 의심")
    for entity_id, cnt in counts.most_common(3):
        if total_issues and cnt / total_issues > 0.40:
            print(f"[build_data] ⚠ 엔티티 {entity_id} 가 이슈의 40% 초과({cnt}/{total_issues}) — 범용어 오탐 의심")


def build_issue_catalog(issues: list[dict], latest_briefing_date: str, checked_at: str = "",
                        entity_registry: list[dict] | None = None,
                        entity_evidence_out: list[dict] | None = None) -> list[dict]:
    latest_day = _parse_day(latest_briefing_date)
    alias_entries = _entity_alias_entries(entity_registry) if entity_registry else []
    rows = []
    for issue in issues:
        card_timeline = sorted(
            issue["members"],
            key=lambda member: (member["article_date"], member.get("briefing_date") or "", member["hash"]),
            reverse=True,
        )
        evidence_timeline = sorted(
            issue.get("evidence_members") or [],
            key=lambda member: (member.get("article_date") or "", member["hash"]),
            reverse=True,
        )
        all_timeline = sorted(
            card_timeline + evidence_timeline,
            key=lambda member: (member.get("article_date") or "", member.get("briefing_date") or "", member["hash"]),
            reverse=True,
        )
        briefing_dates = sorted({member["briefing_date"] for member in card_timeline})
        last_seen = max(briefing_dates)
        current = [member for member in card_timeline if member["briefing_date"] == last_seen]
        history = [member for member in card_timeline if member["briefing_date"] < last_seen]
        representative = max(current, key=_representative_key)
        regions = {member.get("region") for member in card_timeline if member.get("region")}
        topic_counts = Counter(topic for member in card_timeline for topic in (member.get("topics") or []))
        tag_counts = Counter(
            tag for member in card_timeline for tag in (member.get("canonical_tags") or [])
            if tag not in _GENERIC_TAGS
        )
        reasons = []
        for member in sorted(card_timeline, key=_representative_key, reverse=True):
            reasons.extend(member.get("selection_reasons") or [])
        last_day = _parse_day(last_seen)
        days_since_update = (
            (latest_day - last_day).days if latest_day and last_day else None
        )
        implication, why_important = split_interpretation(representative)
        archive_change_line = change_line_for_card(
            current, history + evidence_timeline,
            card_visible_text(representative["title_kr"], implication, why_important),
        )
        # 엔티티 매칭은 여기(원 멤버)에서만 가능하다 — _article_view 가
        # canonical_tags 를 싣지 않으므로 직렬화 이후엔 재료가 없다.
        entity_ids: list[str] = []
        if alias_entries:
            entity_ids, entity_evidence = entity_ids_for_members(card_timeline, alias_entries)
            if entity_evidence_out is not None:
                for record in entity_evidence:
                    entity_evidence_out.append({"issue_id": issue["issue_id"], **record})
        # 요지는 **카드 멤버 안에서만** 고른다. `evidence_members` 는 브리핑에
        # 선정되지 않은 채 뒤에 매칭으로 붙은 보도이고, 화면에서도 '추가 근거'라는
        # 접힌 칸에만 산다 — 그 본문이 이슈 대표 설명("관련 기사 내용")으로 올라오면
        # 근거가 결론 자리를 차지한다.
        #
        # 2026-08-22 라이브: 『제12차 전력수급기본계획, 원전 비중 확대 시험대』의
        # '관련 기사 내용'이 근거 기사 『산업용 전기요금 지역별 차등제, 남부권
        # 통합안 실효성 논란』의 본문이었다. 그 근거는 같은 이슈의 다른 **카드**
        # (『김성환 장관, 산업용 지역요금제 초안 다음 주 공개』)에 정상적으로 붙은
        # 것이라 매칭은 틀리지 않았다 — 틀린 것은 승격 범위였다.
        #
        # 카드에도 요지가 없으면 블록을 통째로 비운다. 근거에서 끌어오면 제목과
        # 본문이 다른 사건을 말하는 조합이 되고, 그것은 요지가 없는 것보다 나쁘다.
        # (`verification`·`article_count` 는 그대로 all_timeline 을 센다 — 검증은
        # 근거를 함께 세는 것이 맞다.)
        archive_detail, archive_detail_source = pick_detail(card_timeline, representative)
        report_topic, report_why, report_angles = pick_report_metadata(card_timeline)
        rows.append({
            "issue_id": issue["issue_id"],
            "identity_status": issue.get("identity_status", "ok"),
            "identity_diagnostics": issue.get("identity_diagnostics") or [],
            "legacy_issue_id": issue.get("legacy_issue_id", ""),
            "status": "ongoing" if len(briefing_dates) > 1 else "new",
            "lifecycle": "active" if days_since_update is not None and days_since_update <= 7 else "quiet",
            "days_since_update": days_since_update,
            "first_seen": min(briefing_dates),
            "last_seen": last_seen,
            "title": representative["title_kr"],
            "summary": representative.get("summary", ""),
            "detail": archive_detail,
            "detail_source": archive_detail_source,
            "implication": implication,
            "why_important": why_important,
            # 아카이브 행은 그 이슈가 **언젠가** 보고서감이었는지를 남긴다 —
            # 브리핑 행과 달리 '오늘'이라는 기준일이 없다.
            "report_pick": report_topic,
            "report_pick_why": report_why,
            "report_pick_angles": report_angles,
            "open_question": pick_open_question(card_timeline),
            "latest_change": archive_change_line,
            "change_display": card_change_display(
                archive_change_line, representative["title_kr"], implication, why_important
            ),
            "verification": verification_state(all_timeline, checked_at),
            "region": "국내·해외" if len(regions) > 1 else next(iter(regions), ""),
            "regions": sorted(regions),
            # 이 이슈가 **가장 최근 회차**의 텔레그램에서 몇 번이었나. 오디오가
            # 읽는 사전(issues.json)이 이 행이라, 여기 없으면 브리핑 행에만
            # 넣어 봐야 닿지 않는다. `region` 이 여러 날의 합집합이라 '국내·해외'가
            # 되는 이슈도 그날 실린 목록은 하나뿐이므로 brief_region 이 그것을 말한다.
            **_brief_position(current),
            "importance": representative.get("importance", ""),
            "selection_reasons": list(dict.fromkeys(reasons))[:2],
            **_story_contract(representative),
            "topics": [topic for topic, _ in topic_counts.most_common(3)],
            "tags": [tag for tag, _ in tag_counts.most_common(8)],
            "entity_ids": entity_ids,
            "current_article_count": len(current),
            "previous_article_count": len(history),
            "tracked_briefings": len(briefing_dates),
            "briefing_count": len(briefing_dates),
            "card_article_count": len(card_timeline),
            "evidence_article_count": len(evidence_timeline),
            "article_count": len(all_timeline),
            "representative_article": _article_view(representative, "card"),
            "related_articles": (
                [_article_view(member, "card") for member in card_timeline]
                + [_article_view(member, "evidence") for member in evidence_timeline]
            ),
            "sort_score": float(representative.get("selection_score") or 0),
        })
    rows.sort(
        key=lambda row: (row["last_seen"], row["importance"] == "must_read", row["sort_score"], row["card_article_count"]),
        reverse=True,
    )
    for row in rows:
        row.pop("sort_score", None)
    return rows


def _issue_meta_description(issue: dict) -> str:
    description = " ".join(
        str(issue.get("summary") or issue.get("latest_change") or "원자력 정책·산업 이슈의 변화와 근거를 추적합니다.").split()
    )
    return description if len(description) <= 170 else f"{description[:167].rstrip()}…"


# 이슈 지도(Atlas)가 그리려는 5단계 경로. 각 노드가 어느 필드에 걸려 있는지.
# 시안의 경로이고, 착수 판단의 유일한 근거다 — 산문으로 적어두면 매 세션이 다시 잰다.
ATLAS_NODES = (
    ("latest_change", lambda row: bool(row.get("latest_change"))),
    ("open_question", lambda row: bool(row.get("open_question"))),
    # 라벨은 시사점·왜 중요 둘로 갈라졌지만(2026-08-04) 이 노드가 묻는 건
    # 'AI 해석 문장이 하나라도 있는가' 하나다. 한쪽만 세면 분리 작업이
    # 데이터 후퇴로 잘못 읽힌다 — 실제로는 같은 문장이 제 이름을 찾았을 뿐이다.
    ("implication", lambda row: bool(row.get("implication") or row.get("why_important"))),
    ("related_articles", lambda row: (row.get("article_count") or 0) >= 2),
    ("official_source", lambda row: ((row.get("verification") or {})
                                     .get("official_source_count") or 0) > 0),
)

# 착수 문턱. `open_question` 이 0 인 한 '남은 질문' 노드를 만들 수 없고,
# `related_articles` 가 20% 아래면 '관련 보도' 노드가 대부분 숨는다 — 두 값만 본다
# (docs/PHASE_PLAN.md §S4). 나머지 셋은 이 둘이 풀리면 같이 오르거나(병합기 공통 뿌리)
# 구조적 상한이 있다(공식 출처는 출처 구성의 성질이다).
ATLAS_MIN_OPEN_QUESTION = 1        # 건수 — 0 이면 노드 자체가 성립 안 한다
ATLAS_MIN_RELATED_RATE = 0.20


def atlas_readiness(issue_catalog: list[dict]) -> dict:
    """이슈 지도를 지금 그릴 수 있는지, 못 그리면 어느 노드가 비었는지.

    **게이트가 아니라 계기판이다.** 오늘(2026-08-03) 추적률을 배포 게이트로 썼다가
    뉴스가 한산한 날 CSS 오타 수정까지 막힌 일이 있었다 — 데이터 지표는 빌드를
    세우는 데 쓰지 않는다. 이 값은 meta.json 에 실려 "언제 착수 가능한가"를
    사람이 재지 않고 볼 수 있게만 한다.
    """
    total = len(issue_catalog)
    counts = {name: sum(1 for row in issue_catalog if test(row))
              for name, test in ATLAS_NODES}
    rates = {name: round(count / total, 4) if total else 0.0
             for name, count in counts.items()}
    filled_per_issue = [sum(1 for _, test in ATLAS_NODES if test(row))
                        for row in issue_catalog]
    articles = [
        article for row in issue_catalog for article in (row.get("related_articles") or [])
    ]
    canonical = [
        article for article in articles
        if "news.google." not in str(article.get("url") or "").lower()
    ]
    verification_mix = Counter(
        (row.get("verification") or {}).get("status", "unverified")
        for row in issue_catalog
    )
    blocking = []
    if counts["open_question"] < ATLAS_MIN_OPEN_QUESTION:
        blocking.append("open_question")
    if rates["related_articles"] < ATLAS_MIN_RELATED_RATE:
        blocking.append("related_articles")
    return {
        "definition_version": "card-evidence-v2",
        "metric_basis": {
            "state_sort_briefing_count": "card_members",
            "related_articles_verification": "card_plus_evidence_members",
        },
        "issue_total": total,
        "node_counts": counts,
        "node_rates": rates,
        "full_path_issues": sum(1 for n in filled_per_issue if n == len(ATLAS_NODES)),
        "three_plus_issues": sum(1 for n in filled_per_issue if n >= 3),
        "multi_card_article_rate": round(
            sum(1 for row in issue_catalog if (row.get("card_article_count") or 0) >= 2) / total, 4
        ) if total else 0.0,
        "multi_evidence_article_rate": round(
            sum(1 for row in issue_catalog if (row.get("article_count") or 0) >= 2) / total, 4
        ) if total else 0.0,
        "verification_mix": {
            key: round(verification_mix.get(key, 0) / total, 4) if total else 0.0
            for key in ("official", "corroborated", "partial", "unverified")
        },
        "canonical_url_rate": round(len(canonical) / len(articles), 4) if articles else 0.0,
        "change_rate": round(
            sum(1 for row in issue_catalog if row.get("latest_change")) / total, 4
        ) if total else 0.0,
        "cluster_input_count": sum(
            (row.get("card_article_count") or 0) + (row.get("evidence_article_count") or 0)
            for row in issue_catalog
        ),
        "blocking_nodes": blocking,
        "ready": not blocking,
    }


def validate_issue_catalog_ids(issue_catalog: list[dict]) -> None:
    """서로 다른 클러스터가 같은 issue_id 를 쓰면 페이지를 만들기 전에 멈춘다.

    `issue_id` 는 대체로 대표 기사의 `story_id`(Daily Brief 의 연속일 판정이
    붙여 준 값)를 그대로 쓴다(`cluster_selected_articles`). 그 story_id 가
    잘못 상속되면(실측 2026-08-31: 국가·설비가 전혀 다른 두 사건이 업계 공통
    태그 앵커만으로 같은 story_id 를 물려받음) `cluster_selected_articles` 는
    국가·설비 충돌 때문에 둘을 **다른 클러스터**로 유지하면서도, 두 클러스터가
    같은 `issue_id` 를 들고 나온다. `build_issue_pages` 가 그 id 로 디렉터리를
    하나씩 만들다 두 번째에서 죽거나(과거 `FileExistsError`), `exist_ok=True`
    로 덮어쓰면 서로 다른 이슈가 같은 URL 을 공유한다 — 둘 다 사고다.

    근본 원인은 `issue_continuity.annotate()` 의 story_id 상속 조건에서
    막는다(identity_confirmed·evidence_confirmed 없이는 상속하지 않는다).
    이 검증은 그 방어가 뚫렸을 때(다른 story_id 발급 경로·수동 데이터 편집 등)
    마지막으로 잡아 배포 전에 사람이 볼 수 있는 오류로 바꾸는 자리다.
    """
    seen: dict[str, dict] = {}
    conflicts: list[str] = []
    for issue in issue_catalog:
        issue_id = str(issue.get("issue_id") or "")
        if not issue_id:
            continue
        prior = seen.get(issue_id)
        if prior is None:
            seen[issue_id] = issue
            continue
        left_hash = str((prior.get("representative_article") or {}).get("hash") or "")
        right_hash = str((issue.get("representative_article") or {}).get("hash") or "")
        conflicts.append(
            f"{issue_id}: {prior.get('title', '')!r}[{left_hash}] "
            f"vs {issue.get('title', '')!r}[{right_hash}]"
        )
    if conflicts:
        preview = " | ".join(conflicts[:10])
        raise ValueError(
            f"duplicate issue_id across distinct clusters ({len(conflicts)}): {preview}"
        )


def publish_build_mode(diagnostics: Mapping) -> None:
    """Hand the identity verdict to the workflow that started this build.

    ``build_mode`` already lives in ``meta.json``/``status.json``, but nothing
    reads those: a degraded build and a clean one look identical to GitHub, so
    ``degraded`` silently renders as ``ok`` in every summary and alert.  Writing
    it to ``$GITHUB_OUTPUT`` is the one place the workflow can pick it up while
    still distinguishing it from a *crashed* build, which never reaches here.
    """
    out = os.environ.get("GITHUB_OUTPUT")
    status = str(diagnostics.get("status") or "ok")
    count = int(diagnostics.get("quarantined_cluster_count") or 0)
    print(f"[build_data:identity] build_mode={status} quarantined={count}")
    if not out:
        return
    try:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"build_mode={status}\n")
            handle.write(f"identity_quarantined={count}\n")
    except OSError as exc:  # 보고 경로가 빌드를 죽이면 안 된다
        print(f"::warning::build_mode 를 workflow 에 전달하지 못했습니다: {exc}")


def resolve_local_issue_id_conflicts(issues: list[dict], *, max_local: int = 5) -> dict:
    """Quarantine a few colliding clusters and fail closed on systemic corruption.

    Every cluster sharing an ambiguous ID receives a deterministic article-hash fallback;
    choosing an arbitrary "winner" would preserve contamination in one branch.  The old ID
    is diagnostic-only because it cannot safely redirect to multiple destinations.
    """
    by_id: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "").strip()
        if issue_id:
            by_id[issue_id].append(issue)
    groups = {issue_id: rows for issue_id, rows in by_id.items() if len(rows) > 1}
    affected = sum(len(rows) for rows in groups.values())
    if affected > max_local:
        preview = ", ".join(f"{issue_id}:{len(rows)}" for issue_id, rows in list(groups.items())[:8])
        raise ValueError(
            f"systemic duplicate issue_id corruption ({affected} clusters; "
            f"local threshold {max_local}): {preview}"
        )

    events: list[dict] = []
    for legacy_id, rows in groups.items():
        for issue in rows:
            representative = issue.get("representative") or issue.get("representative_article") or {}
            article_hash = str(representative.get("hash") or "").strip()
            if not article_hash:
                article_hash = hashlib.sha256(
                    (legacy_id + str(issue.get("title") or "")).encode("utf-8")
                ).hexdigest()[:16]
            safe_id = f"issue-{article_hash}"
            issue["issue_id"] = safe_id
            issue["identity_status"] = "quarantined"
            issue["identity_diagnostics"] = [
                "identity_conflict", "legacy_rejected", "forced_split",
                "fallback_article_card",
            ]
            issue["legacy_issue_id"] = legacy_id
            issue["issue_id_alias_safe"] = False
            events.append({
                "legacy_issue_id": legacy_id,
                "issue_id": safe_id,
                "representative_hash": article_hash,
                "status": "fallback_article_card",
            })
    return {
        "status": "degraded" if events else "ok",
        "conflict_group_count": len(groups),
        "quarantined_cluster_count": len(events),
        "duplicate_issue_id_count": 0,
        "local_threshold": max_local,
        "events": events,
    }


def build_issue_pages(issue_catalog: list[dict]) -> int:
    """이슈별 OG 메타데이터를 가진 정적 진입 페이지를 생성한다."""
    public_dir = (SITE_DIR / "public").resolve()
    issue_dir = (public_dir / "issue").resolve()
    if issue_dir.parent != public_dir or issue_dir.name != "issue":
        raise RuntimeError(f"unsafe issue page directory: {issue_dir}")
    if issue_dir.exists():
        shutil.rmtree(issue_dir)
    issue_dir.mkdir(parents=True)

    template = (public_dir / "index.html").read_text(encoding="utf-8")
    generated = 0
    for issue in issue_catalog:
        issue_id = str(issue.get("issue_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", issue_id):
            continue
        title = str(issue.get("title") or "Nuclens 이슈")
        description = _issue_meta_description(issue)
        issue_url = f"{SITE_URL}/issue/{quote(issue_id, safe='-_')}"
        page = template
        replacements = {
            '<meta name="description" content="Nuclens는 원자력 정책·산업 뉴스를 이슈 단위로 연결하고 중요한 변화를 근거와 함께 추적합니다.">':
                f'<meta name="description" content="{html_escape(description, quote=True)}">',
            '<meta property="og:type" content="website">': '<meta property="og:type" content="article">',
            '<meta property="og:title" content="Nuclens · 원자력 정책·산업 이슈 트래커">':
                f'<meta property="og:title" content="{html_escape(title, quote=True)} | Nuclens">',
            '<meta property="og:description" content="원자력 이슈를 연결하고, 변화를 추적합니다.">':
                f'<meta property="og:description" content="{html_escape(description, quote=True)}">',
            '<meta property="og:url" content="https://nuclens-v2.pages.dev/">':
                f'<meta property="og:url" content="{html_escape(issue_url, quote=True)}">',
            '<link rel="canonical" href="https://nuclens-v2.pages.dev/">':
                f'<link rel="canonical" href="{html_escape(issue_url, quote=True)}">',
            '<title>Nuclens · 원자력 정책·산업 이슈 트래커</title>':
                f'<title>{html_escape(title)} | Nuclens</title>',
        }
        for old, new in replacements.items():
            if old not in page:
                raise RuntimeError(f"issue page metadata template is missing: {old}")
            page = page.replace(old, new, 1)
        structured_data = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": description,
            "datePublished": issue.get("first_seen") or "",
            "dateModified": issue.get("last_seen") or "",
            "mainEntityOfPage": issue_url,
            "publisher": {"@type": "Organization", "name": "Nuclens", "url": SITE_URL},
        }
        json_ld = json.dumps(structured_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        page = page.replace("</head>", f'  <script type="application/ld+json">{json_ld}</script>\n</head>', 1)
        page_dir = issue_dir / issue_id
        page_dir.mkdir()
        (page_dir / "index.html").write_text(page, encoding="utf-8")
        generated += 1
    return generated


def build_brief_pages(briefings: list[dict]) -> int:
    """날짜별 오늘 화면을 OG·canonical·JSON-LD가 있는 정적 진입점으로 만든다."""
    public_dir = (SITE_DIR / "public").resolve()
    brief_dir = (public_dir / "brief").resolve()
    if brief_dir.parent != public_dir or brief_dir.name != "brief":
        raise RuntimeError(f"unsafe brief page directory: {brief_dir}")
    if brief_dir.exists():
        shutil.rmtree(brief_dir)
    brief_dir.mkdir(parents=True)

    template = (public_dir / "index.html").read_text(encoding="utf-8")
    generated = 0
    for briefing in briefings:
        briefing_date = str(briefing.get("date") or "")
        try:
            date.fromisoformat(briefing_date)
        except ValueError:
            continue
        issues = briefing.get("issues") or []
        headline = str(briefing.get("headline") or "이번 주 원자력, 무엇이 달라졌나")
        title = f"{briefing_date} 원자력 브리프"
        description = str((issues[0] if issues else {}).get("summary") or headline)[:180]
        brief_url = f"{SITE_URL}/brief/{briefing_date}"
        page = template
        replacements = {
            '<meta name="description" content="Nuclens는 원자력 정책·산업 뉴스를 이슈 단위로 연결하고 중요한 변화를 근거와 함께 추적합니다.">':
                f'<meta name="description" content="{html_escape(description, quote=True)}">',
            '<meta property="og:type" content="website">': '<meta property="og:type" content="article">',
            '<meta property="og:title" content="Nuclens · 원자력 정책·산업 이슈 트래커">':
                f'<meta property="og:title" content="{html_escape(title, quote=True)} | Nuclens">',
            '<meta property="og:description" content="원자력 이슈를 연결하고, 변화를 추적합니다.">':
                f'<meta property="og:description" content="{html_escape(description, quote=True)}">',
            '<meta property="og:url" content="https://nuclens-v2.pages.dev/">':
                f'<meta property="og:url" content="{html_escape(brief_url, quote=True)}">',
            '<link rel="canonical" href="https://nuclens-v2.pages.dev/">':
                f'<link rel="canonical" href="{html_escape(brief_url, quote=True)}">',
            '<title>Nuclens · 원자력 정책·산업 이슈 트래커</title>':
                f'<title>{html_escape(title)} | Nuclens</title>',
        }
        for old, new in replacements.items():
            if old not in page:
                raise RuntimeError(f"brief page metadata template is missing: {old}")
            page = page.replace(old, new, 1)
        structured_data = {
            "@context": "https://schema.org",
            "@type": "Report",
            "name": title,
            "description": description,
            "datePublished": briefing_date,
            "mainEntityOfPage": brief_url,
            "publisher": {"@type": "Organization", "name": "Nuclens", "url": SITE_URL},
        }
        json_ld = json.dumps(structured_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        page = page.replace("</head>", f'  <script type="application/ld+json">{json_ld}</script>\n</head>', 1)
        page_dir = brief_dir / briefing_date
        page_dir.mkdir()
        (page_dir / "index.html").write_text(page, encoding="utf-8")
        generated += 1
    return generated


def build_period_trends(all_items: list[dict], end_date: str) -> dict[str, dict]:
    """7/30/90/180/365일 장기 흐름을 *선정된 briefing story* 단위로 집계한다.

    원본 기사 언급량을 세면 동일 사건의 재전재가 트렌드를 부풀린다. Daily Brief가
    story dedup을 마친 대표 기사만 사용하면 웹의 숫자도 "오늘 무엇을 중요한 사건으로
    봤는가"와 같은 단위가 된다. 상세 뉴스 JSON은 60일만 유지하면서 이 집계만 1년치를
    내보내므로 Pages 전송량도 작다.

    요청기간보다 실제 archive 축적기간이 짧으면 effective_start/complete_period로 이를
    명시한다. 사용자가 '1년'을 눌렀다고 한 달치 데이터를 1년치처럼 보이면 안 된다.
    """
    try:
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError):
        return {}

    selected_with_dates: list[tuple[date, dict]] = []
    for row in all_items:
        if not row.get("briefing_date"):
            continue
        try:
            selected_with_dates.append((date.fromisoformat(str(row.get("briefing_date"))), row))
        except ValueError:
            continue
    selected_with_dates.sort(key=lambda item: item[0])
    earliest = selected_with_dates[0][0] if selected_with_dates else end

    def bucket_key(day: date, days: int) -> str:
        if days <= 7:
            return day.isoformat()
        if days <= 90:
            year, week, _ = day.isocalendar()
            return f"{year}-W{week:02d}"
        return day.strftime("%Y-%m")

    def summarize_rows(rows: list[tuple[date, dict]]) -> tuple[Counter, Counter, Counter, Counter]:
        tags, topics, countries, publishers = Counter(), Counter(), Counter(), Counter()
        for _, row in rows:
            tags.update(tag for tag in (row.get("canonical_tags") or []) if tag)
            topics.update(topic for topic in (row.get("topics") or []) if topic)
            for country in set(row.get("countries") or []) - {"UNSPECIFIED", "OTHER"}:
                countries[country] += 1
            publisher = row.get("publisher") or row.get("domain")
            if publisher:
                publishers[str(publisher)] += 1
        return tags, topics, countries, publishers

    periods: dict[str, dict] = {}
    for days in TREND_PERIOD_DAYS:
        requested_start = end - timedelta(days=days - 1)
        effective_start = max(requested_start, earliest)
        rows = [(d, row) for d, row in selected_with_dates if effective_start <= d <= end]

        # 키워드 비교의 상대는 기간 토글을 따라간다 — 30일이면 직전 30일, 분기면
        # 직전 분기다. 양쪽 모두 기사 건수가 아니라 story 수를 쓴다. 이전 구간이
        # archive에 온전히 들어 있을 때만 비교하고(previous_period_complete),
        # 아니면 화면이 비교 열을 접는다(app.js renderKeywordTable).
        previous_end = requested_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        previous_complete = bool(selected_with_dates) and earliest <= previous_start
        previous_rows = [
            (d, row) for d, row in selected_with_dates
            if previous_start <= d <= previous_end
        ] if previous_complete else []

        tags, topics, countries, publishers = summarize_rows(rows)
        prev_tags, _, _, _ = summarize_rows(previous_rows)
        buckets: dict[str, list[tuple[date, dict]]] = defaultdict(list)
        multi_source = 0
        tier1_story = 0
        official_story = 0
        story_contract_rows = 0
        for d, row in rows:
            if row.get("story_contract_available"):
                story_contract_rows += 1
            if int(row.get("story_outlet_count") or 1) >= 2:
                multi_source += 1
            if int(row.get("story_tier1_count") or 0) >= 1:
                tier1_story += 1
            if _is_primary_source(row):
                official_story += 1
            buckets[bucket_key(d, days)].append((d, row))

        timeline = []
        for key in sorted(buckets):
            members = buckets[key]
            member_days = [d for d, _ in members]
            topic_counts = Counter(
                topic for _, row in members for topic in (row.get("topics") or []) if topic
            )
            ranked = sorted(
                (row for _, row in members),
                key=lambda row: (
                    float(row.get("selection_score") or 0),
                    1 if row.get("importance") == "must_read" else 0,
                    row.get("briefing_date") or "",
                ),
                reverse=True,
            )
            timeline.append({
                "key": key,
                "start": min(member_days).isoformat(),
                "end": max(member_days).isoformat(),
                "story_count": len(members),
                "multi_source_story_count": sum(
                    1 for _, row in members if int(row.get("story_outlet_count") or 1) >= 2
                ),
                "must_read_count": sum(1 for _, row in members if row.get("importance") == "must_read"),
                "top_topics": [topic for topic, _ in topic_counts.most_common(3)],
                "highlights": [
                    {"title": row.get("title_kr") or row.get("title") or "",
                     "date": row.get("briefing_date") or "",
                     "score": row.get("selection_score")}
                    for row in ranked[:2]
                ],
            })

        def _tag_rows(limit):
            rows_out = []
            for tag in sorted(set(tags) | set(prev_tags),
                              key=lambda key: tags.get(key, 0), reverse=True)[:limit]:
                now_count = int(tags.get(tag, 0))
                prev_count = int(prev_tags.get(tag, 0))
                rows_out.append({
                    "tag": tag,
                    "count": now_count,
                    "previous_count": prev_count if previous_complete else None,
                    "delta": (now_count - prev_count) if previous_complete else None,
                    "new": bool(previous_complete and now_count > 0 and prev_count == 0),
                })
            return rows_out

        tag_comparison = _tag_rows(12)
        # 워드 클라우드는 표보다 넓게 본다 — 12개는 순위이지 분포가 아니고, 그
        # 12개로 구름을 그리면 낱말 사이가 비어 그림이 아무 말도 하지 않는다.
        # 표의 계약(tag_comparison)은 건드리지 않고 별도 키로 낸다.
        tag_cloud = _tag_rows(TAG_CLOUD_LIMIT)

        complete_period = bool(selected_with_dates) and earliest <= requested_start
        periods[str(days)] = {
            "days": days,
            "requested_start": requested_start.isoformat(),
            "start": effective_start.isoformat(),
            "end": end.isoformat(),
            "complete_period": complete_period,
            "available_days": max(0, (end - effective_start).days + 1) if rows else 0,
            "archive_first_briefing_date": earliest.isoformat() if selected_with_dates else None,
            "story_count": len(rows),
            "briefing_day_count": len({row.get("briefing_date") for _, row in rows}),
            "multi_source_story_count": multi_source,
            "tier1_story_count": tier1_story,
            "official_story_count": official_story,
            "story_contract_count": story_contract_rows,
            "story_contract_coverage": round(story_contract_rows / len(rows), 3) if rows else 0,
            "average_outlets": round(
                sum(max(1, int(row.get("story_outlet_count") or 1)) for _, row in rows) / len(rows), 2
            ) if rows else 0,
            "top_tags": [{"tag": k, "count": v} for k, v in tags.most_common(12)],
            "previous_top_tags": ([{"tag": k, "count": v} for k, v in prev_tags.most_common(12)]
                                  if previous_complete else []),
            "tag_comparison": tag_comparison,
            "tag_cloud": tag_cloud,
            "previous_period_complete": previous_complete,
            "top_topics": [{"topic": k, "count": v} for k, v in topics.most_common(12)],
            "countries": [{"country": k, "count": v} for k, v in countries.most_common(12)],
            "publishers": [{"publisher": k, "count": v} for k, v in publishers.most_common(10)],
            "timeline": timeline,
            "unit": "briefing_story",
        }
    return periods


def _read_json(path: Path, fallback):
    """설정 파일을 읽는다. 없거나 깨졌으면 fallback — 운영 콘솔 하나 때문에
    빌드 전체가 죽으면 안 된다. 대신 화면이 '못 읽었다'를 말할 수 있게
    호출부에서 빈 값과 오류를 구분해 싣는다."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


# ── 운영 콘솔(/admin) 데이터 ────────────────────────────────────────────────
#
# 이 서비스에서 더 위험한 실패는 누락이 아니라 **오병합**이다. 놓친 기사는 다음
# 회차에 다시 들어오지만, 서로 다른 사건이 한 카드로 붙으면 그 카드가 근거 목록과
# 검증 배지까지 달고 사실처럼 굳는다 — 그리고 아무도 그걸 되짚을 화면이 없었다.
#
# 병합은 두 계층에서 일어난다. 둘을 한 파일에 담는 이유는 관리자가 "이 카드가 왜
# 이렇게 생겼나"를 물을 때 어느 계층에서 붙었는지를 모르기 때문이다.
#
#   story  같은 날 여러 매체의 같은 사건 (daily_brief 의 LLM 판단 — 근거 문장이 있다)
#   issue  날짜를 넘어 잇는 클러스터 (build_data 의 규칙 매칭 — 점수·차단 사유가 있다)
def load_story_audits(limit: int = 14) -> list[dict]:
    """delivery_log 의 story_audit 레코드(단계 충돌 분리·대표 교체)를 최신순으로 읽는다.

    병합은 카드에 흔적을 남기지만 **분리는 아무 흔적도 남기지 않는다.** 두 기사가
    끝내 안 붙었다는 사실은 결과물 어디에도 없어서, 이 줄을 안 읽으면 운영 콘솔이
    "왜 분리됐나"에 영원히 답할 수 없다.
    """
    path = BOT_DIR / "delivery_log.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("record_type") == "story_audit":
            rows.append(row)
    rows.sort(key=lambda row: (str(row.get("date") or ""),
                               str(row.get("generated_at") or "")), reverse=True)
    return rows[:limit]


def _split_units(item: dict, raw_sources: list[dict],
                 known: dict[str, dict] | None = None) -> list[dict]:
    """이 카드에서 사람이 떼어낼 수 있는 기사들 (hash ↔ 제목 짝).

    제목 목록만으로는 분리를 지정할 수 없다 — 같은 제목이 여러 매체에 있고, 판정은
    hash 로 남아야 다음 회차에 재현된다. 그래서 짝이 있는 것만 올린다. 재료는 셋이고
    앞의 것이 이긴다.

      ① ``story_members``      hash·제목·매체가 같이 적힌 최신 계약
      ② ``raw_sources``        수집 단계에서 접힌 기사 — 여기에도 hash 가 있다
      ③ ``story_article_hashes`` + 아카이브 조회
         선정 단계(LLM)에서 접힌 기사는 그 전에 이미 아카이브에 **따로** 실려 있다
         (수집이 개별 레코드로 넣고 story 병합은 그 뒤에 일어난다). 그래서 hash 로
         제목을 되찾을 수 있다 — ①②가 붙기 전의 옛 회차가 이 경로로 살아난다.
    """
    units: list[dict] = []
    seen: set[str] = {str(item.get("hash") or "")}

    def add(member_hash: str, title: str, publisher: str, stage: str) -> None:
        if not member_hash or member_hash in seen:
            return
        seen.add(member_hash)
        units.append({
            "hash": member_hash,
            "title": title[:180],
            "publisher": publisher[:100],
            "fold_stage": stage[:40],
        })

    for member in list(item.get("story_members") or []) + list(raw_sources):
        if isinstance(member, dict):
            add(str(member.get("hash") or ""), str(member.get("title") or ""),
                str(member.get("publisher") or member.get("domain") or ""),
                str(member.get("fold_stage") or ""))

    for member_hash in (item.get("story_article_hashes") or []):
        archived = (known or {}).get(str(member_hash))
        if not archived:
            # 제목을 못 찾은 hash 는 올리지 않는다. 16진수만 보여 주고 "떼시겠습니까"를
            # 묻는 것은 검토가 아니라 도박이다.
            continue
        add(str(member_hash),
            str(archived.get("title_kr") or archived.get("title") or ""),
            str(archived.get("publisher") or archived.get("domain") or ""),
            str(item.get("story_dedup_stage") or ""))
    return units[:16]


def build_admin_judgments(news_items: list[dict], generated_at: datetime) -> dict:
    """콘솔이 남긴 병합 판정과, 그 판정이 실제로 얼마나 넓은지.

    학습 규칙에서 제일 위험한 실패는 **과적용**이다. "고리 2호기 ↔ 한빛 3호기"로
    배웠는데 축을 '원전'처럼 넓게 적으면 그 뒤로 모든 원전 기사가 서로 안 붙는다 —
    그리고 그 사고는 조용하다(비슷한 카드가 두 칸을 먹을 뿐, 어디에도 이유가 없다).
    그래서 규칙마다 **최근 30일 기사 중 각 축에만 걸린 건수**를 함께 낸다.
    양쪽이 두세 건이면 좁은 규칙이고, 수십 건씩이면 지워야 할 규칙이다.
    """
    try:
        import admin_overrides  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "entries": [], "rules": [], "synced_at": ""}

    cutoff = (generated_at.date() - timedelta(days=30)).isoformat()
    recent = [item for item in news_items if str(item.get("article_date") or "") >= cutoff]
    texts = [admin_overrides.article_text(item) for item in recent]

    rules = []
    for rule in admin_overrides.learned_rules():
        left_only = right_only = 0
        for text in texts:
            has_left = any(term in text for term in rule["left_terms"])
            has_right = any(term in text for term in rule["right_terms"])
            if has_left and not has_right:
                left_only += 1
            elif has_right and not has_left:
                right_only += 1
        rules.append({
            **rule,
            "left_only": left_only,
            "right_only": right_only,
            # 이 규칙이 갈라 놓을 수 있는 조합의 상한. 실제 발동은 두 기사가 같은
            # 병합 후보로 만났을 때뿐이라 이보다 훨씬 적지만, 넓이는 이 수가 말한다.
            "reach": left_only * right_only,
            "sample_days": 30,
        })

    summary = admin_overrides.summary()
    merge_kinds = ("story_split", "issue_split", "issue_group_split",
                   "issue_join", "learned_rule")
    return {
        "available": True,
        "error": "",
        "synced_at": summary["synced_at"],
        "updated_at": summary["updated_at"],
        "entries": [
            entry for entry in admin_overrides.load()["entries"]
            if entry.get("kind") in merge_kinds
        ],
        "rules": rules,
        "sample_articles": len(recent),
    }


# ---- 진단 회차 ---------------------------------------------------------------------
#
# 콘솔은 이제 "전부"가 아니라 **한 회차**를 본다. 그러려면 판단마다 "이 판단은 어느
# 회차에서 났는가"가 있어야 하는데, 저장된 것은 그 회차의 스냅숏이 아니다 —
# 병합은 빌드마다 전량 재계산된다(cluster_selected_articles 는 기사를 briefing_date
# 오름차순으로 훑으며 매번 처음부터 다시 묶는다).
#
# 그래서 회차는 **역산한다.** 다행히 역산이 정확한 자리가 있다.
#
#   같은 날 병합    briefing_date 그 자체(발송 전 수집분은 보도일로 물러난다)
#   붙이지 않은 판단 delivery_log 의 story_audit.date = 발송 회차
#   날짜 넘는 병합   합류한 기사의 briefing_date. matches[].hash 가 그 기사이고
#                    members 에 briefing_date 가 있다(실측: 카드 match 전량이 members 안)
#   경계선          카드 경로는 right_date 가 곧 합류를 시도한 기사의 briefing_date다
#                    (실측 전수 301건 중 left_date > right_date 인 행 0건)
#
# 역산이 **불가능한** 자리가 하나 있고, 거기서 거짓말하지 않는 것이 이 함수의 요지다:
# 미발송 근거 기사(member_role="evidence")는 briefing 회차가 아예 없다. 그 판단은
# 과거 어느 날이 아니라 **이번 빌드**가 내린 것이고(다음 빌드에도 다시 내린다),
# 그래서 보도일이 아니라 빌드 회차에 싣는다. 보도일에 실으면 운영자는 "8월 12일
# 진단이 이걸 놓쳤다"로 읽지만 그날의 진단은 이 쌍을 본 적조차 없다.


def _near_merge_threshold(diagnostics: dict) -> bool:
    """이 쌍이 화면에 설 만큼 병합 문턱에 가까운가.

    콘솔의 '붙지 않은 경계선'은 **문턱 바로 아래**를 뜻한다. 그런데 audit 의
    review_candidates 는 문턱이 아니라 **기록 문턱**(0.70) 위를 전부 담는다 —
    2026-08-22 라이브 실측 29,305쌍. 그걸 그대로 세면 화면이 "조금만 내리면
    붙습니다"라고 적어 놓고 0.71 짜리 쌍까지 세는 셈이라 숫자가 뜻을 잃는다
    (AS_IS § 0.70과 0.84는 다른 일을 하는 두 개의 선).

    그래서 LLM 이 판정하는 구간(`issue_review.REVIEW_BAND_LOW` ~ `REVIEW_BAND_HIGH`)
    으로 좁힌다. 문턱은 **거기 한 곳에만** 있다 — 여기 숫자를 다시 박으면 매칭부와
    갈라진다.

    `issue_review.in_review_band` 를 그대로 쓰지 않는 이유는 하나다. 그쪽은
    'LLM 에게 물을 것인가'를 정하는 자리라 **차단된 쌍을 뺀다.** 이 화면에서는
    차단이야말로 봐야 할 것이다 — "차단이 과했나"를 여기서 눈으로 고르고, 표에
    차단 열이 있는 것도 그래서다.
    """
    similarity = diagnostics.get("embedding_similarity")
    if similarity is None:
        return False
    try:
        similarity = float(similarity)
    except (TypeError, ValueError):
        return False
    return issue_review.REVIEW_BAND_LOW <= similarity < issue_review.REVIEW_BAND_HIGH


def _top_per_round(rows: list[dict], per_round: int, total: int) -> list[dict]:
    """회차별 상위 몇 건씩, 최신 회차부터 채운다.

    전역 상위 N건으로 자르면 최신 회차가 창을 독점하고 과거 회차는 건수 자체가
    거짓이 된다. 행은 이미 각자의 정렬 순서(점수 내림차순 등)로 들어온다 —
    여기서는 자르기만 한다.

    가르는 단위는 회차와 `_bucket` 이다. 한 회차 안에서도 서로 다른 경로가
    같은 창을 두고 다투면 수가 많은 쪽이 통째로 가져가기 때문이다(경계선의
    미발송 근거 ↔ 발송 기사). `_bucket` 이 없는 계층은 회차로만 갈린다.
    """
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (row["_round"], row.get("_bucket", ""))
        buckets.setdefault(key, []).append(row)
    picked: list[dict] = []
    for key in sorted(buckets, reverse=True):
        if len(picked) >= total:
            break
        picked.extend(buckets[key][:max(0, min(per_round, total - len(picked)))])
    return picked


def build_admin_merges(
    news_items: list[dict],
    issue_catalog: list[dict],
    issue_audit: dict,
    generated_at: datetime,
    story_audits: list[dict] | None = None,
) -> dict:
    """병합 판단을 사람이 되짚을 수 있는 형태로 모은다.

    산출물은 **회차 단위로 읽을 수 있게** 정리한다. 항목마다 `diagnosis_rounds`
    (그 판단이 난 회차)를 싣고, `rounds` 색인이 회차 목록과 회차별 건수를 낸다.
    회차를 어떻게 역산하는지는 이 파일 위의 '진단 회차' 머리말에 있다.
    """
    # 이번 빌드가 내린 판단이 실릴 회차. 미발송 근거 기사처럼 briefing 회차가
    # 없는 판단이 여기로 온다.
    build_round = generated_at.date().isoformat()
    issue_of_hash: dict[str, dict] = {}
    for issue in issue_catalog:
        for article in issue.get("related_articles") or []:
            issue_of_hash.setdefault(str(article.get("hash") or ""), issue)
    # 접힌 기사의 제목을 hash 로 되찾기 위한 색인. 선정 단계에서 접힌 기사는
    # 수집 때 이미 개별 레코드로 아카이브에 들어가 있다.
    item_by_hash = {str(item.get("hash") or ""): item for item in news_items}

    # ① story 계층. 대표 기사 한 줄이 접힌 형제 전부를 들고 있다.
    #
    # `collected` 는 **수집 단계**에서 접힌 story 다. 예전에는 이 계층이 아예 없었다 —
    # 그때 접힌 기사는 삭제됐고, 그래서 화면에 남은 카드의 매체 수는 실제보다 작았고
    # 그 차이를 되짚을 방법도 없었다. 이제 같은 화면에서 함께 본다.
    story_rows = []
    relation_counts: Counter = Counter()
    folded_articles = 0
    collect_folded = 0
    for item in news_items:
        relation = str(item.get("story_relation") or "single")
        relation_counts[relation] += 1
        raw_sources = [s for s in (item.get("story_raw_sources") or []) if isinstance(s, dict)]
        raw_count = int(item.get("story_raw_source_count") or len(raw_sources))
        collect_folded += raw_count
        if relation not in ("merge", "duplicate", "collected"):
            continue
        count = int(item.get("story_article_count") or 1)
        folded_articles += max(0, count - 1)
        owner = issue_of_hash.get(str(item.get("hash") or ""))
        story_rows.append({
            "hash": item.get("hash", ""),
            "title": item.get("title_kr") or item.get("title") or "",
            "briefing_date": item.get("briefing_date") or "",
            "article_date": item.get("article_date") or "",
            "publisher": item.get("publisher") or item.get("domain") or "",
            "relation": relation,
            "reason": item.get("story_reason") or "",
            "stage": item.get("story_dedup_stage") or "",
            "article_count": count,
            "outlet_count": int(item.get("story_outlet_count") or 1),
            "tier1_count": int(item.get("story_tier1_count") or 0),
            "independent_outlet_count": int(item.get("story_independent_outlet_count") or 0),
            "fingerprint": item.get("story_fingerprint") or {},
            "related_titles": item.get("story_related_titles") or [],
            # 수동 분리가 집는 단위. story_members 가 붙기 전의 회차에는 없으므로
            # 수집 단계 근거(raw_sources)로 물러난다 — 그쪽도 hash 를 들고 있고,
            # 실제로 접힌 기사의 대부분이 거기 있다. 둘 다 없으면 화면이
            # "이 회차는 분리 단위를 남기지 않았습니다"로 물러난다(LLM 병합 구간).
            "members": _split_units(item, raw_sources, item_by_hash),
            "sources": item.get("story_sources") or [],
            "context": item.get("story_context") or [],
            # 수집 단계 근거 — 큐레이션 전이라 제목·매체·URL 만 있다.
            "raw_sources": raw_sources[:12],
            "raw_source_count": raw_count,
            # 화면 대표를 story 완성 뒤에 고른 결과.
            "display_reason": item.get("story_display_reason") or "",
            "display_candidates": int(item.get("story_display_candidates") or 1),
            "display_swapped_from": item.get("story_display_swapped_from") or "",
            "display_swapped_from_title": item.get("story_display_swapped_from_title") or "",
            "issue_id": (owner or {}).get("issue_id", ""),
            # 이 판단이 난 회차. 발송 전 수집분은 briefing_date 가 아직 비어 있어
            # 보도일로 물러난다 — 아래 story_by_date 와 같은 규칙이다.
            "diagnosis_rounds": [item.get("briefing_date") or item.get("article_date") or ""],
        })
    # 접은 기사가 많은 순 = 되짚을 값이 큰 순. 같으면 최신부터.
    story_rows.sort(key=lambda row: (row["article_count"], row["briefing_date"]), reverse=True)
    # 발송 전 수집분은 briefing_date 가 아직 비어 있다 — 그때는 보도일로 센다.
    # 빈 문자열 하나로 몰아 두면 날짜별 집계가 통째로 '(없음)' 한 줄이 된다.
    story_by_date: Counter = Counter()
    for row in story_rows:
        story_by_date[row["briefing_date"] or row["article_date"]] += 1

    # ② issue 계층. 클러스터는 '가장 약한 연결'이 먼저 오게 세운다 — 점수가 빠듯한
    # 병합이 곧 의심스러운 병합이고, 관리자가 위에서부터 훑으면 된다.
    titles = {issue["issue_id"]: issue.get("title", "") for issue in issue_catalog}
    clusters = []
    for cluster in issue_audit.get("clusters") or []:
        # 합류한 기사의 회차. matches[].hash 가 그 기사이고, 카드 멤버는 전부
        # briefing_date 를 들고 있다. 근거 기사(member_role="evidence")는 여기에
        # 없다 — 발송된 적이 없어 회차 자체가 없기 때문이다.
        member_round = {
            str(member.get("hash") or ""): str(member.get("briefing_date") or "")
            for member in (cluster.get("members") or [])
        }
        matches = [
            {
                "hash": match.get("hash", ""),
                "reference_hash": match.get("reference_hash", ""),
                "method": match.get("method") or "none",
                "score": match.get("score"),
                "title_ratio": match.get("title_ratio"),
                "token_ratio": match.get("token_ratio"),
                "tag_shared": match.get("tag_shared"),
                "topic_shared": match.get("topic_shared"),
                "embedding_similarity": match.get("embedding_similarity"),
                "local_embedding_similarity": match.get("local_embedding_similarity"),
                "story_fingerprint_similarity": match.get("story_fingerprint_similarity"),
                "story_fingerprint_shared": match.get("story_fingerprint_shared") or [],
                "shared_facility_entities": match.get("shared_facility_entities") or [],
                "blocked_by": match.get("blocked_by") or [],
                "member_role": match.get("member_role") or "card",
                # 이 연결이 만들어진 회차. 근거 기사 경로는 빈 문자열이다.
                "round": ("" if (match.get("member_role") or "card") == "evidence"
                          else member_round.get(str(match.get("hash") or ""), "")),
            }
            for match in (cluster.get("matches") or [])
        ]
        scores = [float(m["score"]) for m in matches if isinstance(m.get("score"), (int, float))]
        rounds = sorted({m["round"] for m in matches if m["round"]})
        clusters.append({
            "issue_id": cluster.get("issue_id", ""),
            "title": titles.get(cluster.get("issue_id", ""), ""),
            "first_seen": cluster.get("first_seen", ""),
            "last_seen": cluster.get("last_seen", ""),
            "briefing_dates": cluster.get("briefing_dates") or [],
            "members": cluster.get("members") or [],
            "member_count": len(cluster.get("members") or []),
            "matches": matches,
            "weakest_score": min(scores) if scores else None,
            "methods": sorted({m["method"] for m in matches if m["method"] != "none"}),
            # 이슈를 **자르지 않는다**(§ 이슈 연속성). 이 목록은 "어느 회차 화면에
            # 이 카드를 세울까"에만 쓰이고, 카드를 열면 전체 멤버가 그대로 있다.
            # 회차를 하나도 못 찾으면(옛 audit) 마지막 등장일로 물러난다 — 조용히
            # 사라지는 것보다 한 회차에 서 있는 편이 낫다.
            "diagnosis_rounds": rounds or [cluster.get("last_seen", "")],
            # 미발송 근거로 붙은 연결 수. 회차에 귀속되지 않지만 카드 안에서는 보인다.
            "evidence_match_count": sum(1 for m in matches if m["member_role"] == "evidence"),
        })
    clusters.sort(key=lambda row: (row["weakest_score"] is None, row["weakest_score"] or 0))

    # ③ 붙지 않은 경계선. 자동 병합 바로 아래 구간이라 문턱을 조금만 내리면 붙는다 —
    # "안 붙어서 다행인가, 붙었어야 했나"를 여기서 눈으로 고른다.
    #
    # 회차 귀속이 여기서 가장 미묘하다. left_date·right_date 는 **판정일이 아니라
    # 두 기사의 날짜**고, 두 생성 경로가 서로 다른 날짜를 넣는다:
    #   카드 경로   right_date = 합류를 시도한 발송 기사의 briefing_date → 회차 그 자체
    #   근거 경로   right_date = 미발송 기사의 article_date → 회차가 아니다
    # 실측(2026-08-21 전수): 카드 301건은 left_date <= right_date 가 100% 성립하고,
    # 근거 2,053건에서만 역전이 난다. 그래서 경로를 갈라 귀속한다.
    borderline_rows = []
    borderline_totals: Counter = Counter()
    scored_totals: Counter = Counter()
    for row in (issue_audit.get("review_candidates") or []):
        role = str(row.get("member_role") or "card")
        # 근거 경로에는 briefing 회차가 없다 — 이번 빌드가 내린 판단이므로
        # 빌드 회차에 싣는다. 보도일에 실으면 그날 진단이 이 쌍을 봤다는 거짓이 된다.
        day = build_round if role == "evidence" else str(row.get("right_date") or "")
        # 채점된 쌍 전수. 화면에 세우지는 않지만 **숨기지도 않는다** — 배경 규모를
        # 지우면 "경계선 11쌍"이 이 회차에 벌어진 일의 전부인 것처럼 읽힌다.
        scored_totals[day] += 1
        if not _near_merge_threshold(row.get("diagnostics") or {}):
            continue
        borderline_totals[day] += 1
        borderline_rows.append({
            "candidate_id": row.get("candidate_id", ""),
            # 콘솔의 '붙이기'가 승인 쌍을 쓰려면 hash 가 필요하다 — 제목은
            # 사람이 읽는 이름이지 판정이 재현되는 열쇠가 아니다.
            "left_hash": row.get("left_hash", ""),
            "right_hash": row.get("right_hash", ""),
            "left_title": row.get("left_title", ""),
            "right_title": row.get("right_title", ""),
            "left_date": row.get("left_date", ""),
            "right_date": row.get("right_date", ""),
            "score": row.get("candidate_score"),
            "method": row.get("candidate_method", ""),
            "review_state": row.get("review_state", ""),
            "diagnostics": row.get("diagnostics") or {},
            # 화면이 "미발송 근거"라고 적을 수 있어야 한다. 그 배지가 없으면
            # 오늘 회차에 몰려 있는 이유를 아무도 설명할 수 없다.
            "member_role": role,
            "diagnosis_rounds": [day],
            "_round": day,
            "_bucket": role,
        })
    borderline_rows.sort(key=lambda row: float(row["score"] or 0), reverse=True)
    borderline = sorted(
        ({key: value for key, value in row.items() if not key.startswith("_")}
         for row in _top_per_round(borderline_rows, CONSOLE_BORDERLINE_PER_ROUND,
                                   CONSOLE_BORDERLINE_TOTAL)),
        key=lambda row: float(row["score"] or 0),
        reverse=True,
    )

    method_counts: Counter = Counter()
    for cluster in clusters:
        for match in cluster["matches"]:
            method_counts[match["method"]] += 1

    # ④ 붙지 않은 story — 사건 단계가 달라 일부러 갈라 둔 쌍과, story 완성 뒤에
    #    화면 대표를 바꾼 판단. 둘 다 결과물에 흔적이 없어 로그에서만 온다.
    #    여기는 회차가 그대로 적혀 있다 — delivery_log 의 story_audit.date 가 발송 회차다.
    veto_rows: list[dict] = []
    promo_rows: list[dict] = []
    veto_totals: Counter = Counter()
    promo_totals: Counter = Counter()
    for audit in (story_audits or []):
        day = str(audit.get("date") or "")
        for veto in (audit.get("stage_vetoes") or []):
            if isinstance(veto, dict):
                veto_totals[day] += 1
                veto_rows.append({**veto, "date": day, "diagnosis_rounds": [day], "_round": day})
        for promo in (audit.get("display_promotions") or []):
            if isinstance(promo, dict):
                promo_totals[day] += 1
                promo_rows.append({**promo, "date": day, "diagnosis_rounds": [day], "_round": day})

    def _shipped(rows: list[dict], per_round: int, total: int) -> list[dict]:
        return [{k: v for k, v in row.items() if not k.startswith("_")}
                for row in _top_per_round(rows, per_round, total)]

    stage_vetoes = _shipped(veto_rows, CONSOLE_VETO_PER_ROUND, CONSOLE_VETO_TOTAL)
    display_promotions = _shipped(promo_rows, CONSOLE_PROMOTION_PER_ROUND,
                                  CONSOLE_PROMOTION_TOTAL)

    # ⑤ 회차 색인. 화면이 "무슨 날이 있고 그날 무엇이 몇 건인가"를 여기서만 읽는다.
    #
    # 건수는 **전수**다(실린 행 수가 아니라). 회차별로 자른 목록만 세면 "10건"이
    # 상한이라는 사실이 화면에서 사라지고, 운영자는 나머지 20건을 못 본 줄도 모른다.
    shown_counts: Counter = Counter()
    for row in borderline:
        shown_counts[row["diagnosis_rounds"][0]] += 1
    story_rounds: Counter = Counter(story_by_date)
    issue_rounds: Counter = Counter()
    for cluster in clusters:
        for day in cluster["diagnosis_rounds"]:
            if day:
                issue_rounds[day] += 1
    stage_totals = veto_totals + promo_totals
    days = {day for day in (
        set(story_rounds) | set(issue_rounds) | set(stage_totals)
        | set(borderline_totals) | set(scored_totals)
    ) if day}
    # 빌드 회차는 아무 일이 없어도 목록에 남긴다 — 없으면 "오늘"로 들어갈 자리가
    # 사라지고, 화면은 어제를 오늘처럼 연다.
    days.add(build_round)
    round_rows = [
        {
            "date": day,
            "story": story_rounds.get(day, 0),
            "stage": stage_totals.get(day, 0),
            "issue": issue_rounds.get(day, 0),
            "borderline": borderline_totals.get(day, 0),
            "borderline_shown": shown_counts.get(day, 0),
            # 그 회차에 채점된 쌍 전수(기록 문턱 위 전부). 경계선은 이 가운데
            # 병합 문턱 바로 아래 구간만이다 — 실측 2026-08-21 전체의 2.6%.
            "scored": scored_totals.get(day, 0),
        }
        for day in sorted(days, reverse=True)
    ]
    # 기본으로 열 회차. 빌드가 그날 브리핑보다 먼저 돌면 빌드 회차에는 미발송 근거만
    # 있고 진짜 진단은 하루 전에 있다 — 그때 빈 화면을 기본으로 열면 안 된다.
    latest_round = next(
        (row["date"] for row in round_rows if row["story"] or row["stage"] or row["issue"]),
        round_rows[0]["date"] if round_rows else build_round,
    )

    return {
        "generated_at": generated_at.isoformat(),
        # 화면이 여는 문. 회차를 고르면 아래 네 계층이 **함께** 그 회차로 간다.
        "rounds": {
            "latest": latest_round,
            # 미발송 근거처럼 briefing 회차가 없는 판단이 실린 회차.
            "build_round": build_round,
            "dates": round_rows,
            "borderline_per_round": CONSOLE_BORDERLINE_PER_ROUND,
        },
        # 사람이 내린 판정과 그 판정의 넓이. 병합 진단과 같은 파일에 두는 이유는
        # "무엇이 붙었나"와 "내가 무엇을 갈라 뒀나"를 한 화면에서 대조해야 하기 때문이다.
        "judgments": build_admin_judgments(news_items, generated_at),
        "story": {
            "contract_version": STORY_CONTRACT_VERSION,
            "totals": {
                "merge": relation_counts.get("merge", 0),
                "duplicate": relation_counts.get("duplicate", 0),
                "collected": relation_counts.get("collected", 0),
                "single": relation_counts.get("single", 0),
                "folded_articles": folded_articles,
                # 수집 단계에서 접힌 기사 수. 예전 파이프라인에서는 이 숫자만큼이
                # story 가 만들어지기 전에 삭제됐다.
                "collect_folded_articles": collect_folded,
                # 전수를 센다 — 회차별로 자른 목록 길이를 세면 화면의 숫자가
                # "실린 것"이지 "일어난 것"이 아니게 된다.
                "stage_vetoes": len(veto_rows),
                "display_promotions": len(promo_rows),
            },
            "by_date": [
                {"date": day, "count": count}
                for day, count in sorted(story_by_date.items(), reverse=True)
            ],
            "merges": story_rows,
            "stage_vetoes": stage_vetoes,
            "display_promotions": display_promotions,
        },
        "issue": {
            "matching_version": issue_audit.get("matching_version", ""),
            "window_days": issue_audit.get("issue_window_days"),
            "rules": [dict(rule) for rule in MERGE_RULES],
            "thresholds": {
                "embedding": ISSUE_EMBEDDING_THRESHOLD,
                "embedding_candidate": ISSUE_EMBEDDING_CANDIDATE_THRESHOLD,
                "local_embedding_candidate": LOCAL_EMBEDDING_CANDIDATE_THRESHOLD,
            },
            "totals": {
                "clusters": len(clusters),
                "review_candidates": len(issue_audit.get("review_candidates") or []),
                "manual_approved": len(issue_audit.get("overrides", {}).get("approved") or []),
                "manual_rejected": len(issue_audit.get("overrides", {}).get("rejected") or []),
                "llm_approved": len(issue_audit.get("llm_approved") or []),
                "llm_rejected": len(issue_audit.get("llm_rejected") or []),
            },
            "method_counts": dict(method_counts.most_common()),
            "clusters": clusters,
            "borderline": borderline,
        },
    }


def build_admin_config(generated_at: datetime) -> dict:
    """무엇을 어디서 긁어오는가 — 수집 설정을 화면이 읽을 형태로 모은다.

    값은 전부 실제 설정 파일과 모듈에서 읽는다. 화면에 숫자를 손으로 적으면
    설정이 바뀌어도 화면은 옛날을 말하고, 그러면 이 화면을 볼 이유가 없다.

    **덧칠을 적용한 뒤의 값을 낸다.** 콘솔에서 키워드를 지웠는데 화면이 여전히
    기본 파일을 그리면, 관리자는 자기가 누른 것이 먹혔는지 확인할 방법이 없다.
    기본값과의 차이는 `overrides.entries` 로 따로 실어 보내 화면이 표시한다.
    """
    try:
        import admin_overrides as _ao  # noqa: PLC0415

        overlay = _ao.summary()
        overlay_error = ""
    except Exception as exc:  # noqa: BLE001
        _ao = None
        overlay = {"total": 0, "counts": {}, "synced_at": "", "updated_at": "", "source": ""}
        overlay_error = f"{type(exc).__name__}: {exc}"[:200]

    keyword_groups = []
    base_keywords = _read_json(BOT_DIR / "keywords.json", {}) or {}
    raw_keywords = _ao.keywords_config(base_keywords) if _ao else base_keywords
    for name, group in (raw_keywords or {}).items():
        if not isinstance(group, dict):
            continue
        base_group = base_keywords.get(name) if isinstance(base_keywords.get(name), dict) else {}
        keyword_groups.append({
            "name": name,
            "keywords": [str(k) for k in (group.get("keywords") or [])],
            "anchors": [str(k) for k in (group.get("anchors") or [])],
            # 검색 쿼리에 그대로 붙는 문자열이라 쪼개지 않고 원문 그대로 보인다.
            "negative_terms": str(group.get("negative_terms") or ""),
            # 화면은 칩 단위로 지우므로 쪼갠 형태도 함께 준다.
            "negative_list": _ao.negative_terms_list(group.get("negative_terms")) if _ao
                             else str(group.get("negative_terms") or "").split(),
            # 기본 파일에 있던 것과 콘솔이 더한 것을 화면이 구분해 표시한다.
            "base_keywords": [str(k) for k in (base_group.get("keywords") or [])],
            "base_anchors": [str(k) for k in (base_group.get("anchors") or [])],
        })

    base_sources = _read_json(BOT_DIR / "sources.json", {}) or {}
    sources_config = _ao.sources_config(base_sources) if _ao else base_sources
    tiers = []
    for tier in (1, 2, 3):
        for row in sources_config.get(f"tier{tier}") or []:
            if not isinstance(row, dict):
                continue
            tiers.append({
                # 정렬 호환용 키(tier1/2/3 배열)와 실제 등급(rank_tier)이 다를 수
                # 있다 — 콘솔에서 등급을 옮기면 rank_tier 가 먼저 바뀐다.
                "tier": int(row.get("rank_tier") or tier),
                "domain": row.get("domain", ""),
                "name": row.get("name", ""),
                "source_type": row.get("source_type", ""),
                "evidence_role": row.get("evidence_role", ""),
                "aliases": [str(a) for a in (row.get("aliases") or [])][:12],
            })

    # news_bot 은 수집 실행 모듈이라 임포트 실패가 빌드를 죽이면 안 된다.
    # 못 읽으면 빈 목록이 아니라 '못 읽었다'를 화면에 적는다 — 조용한 0 은
    # '수집원이 없다'로 읽힌다.
    feeds: list[dict] = []
    official: list[dict] = []
    anti_keywords: list[str] = []
    feed_error = ""
    try:
        import news_bot as _nb  # noqa: PLC0415
        for row in _nb.RSS_SOURCES:
            url = str(row.get("url") or "")
            feeds.append({
                "name": row.get("name", ""),
                "domain": row.get("domain_label", ""),
                "url": url,
                # 직접 피드와 Google News 우회는 신뢰도가 다르다 — 우회는 색인
                # 지연·관련도순 정렬을 타므로 구분해서 보여야 한다.
                "via": "google_news" if "news.google.com" in url else "direct",
                "require_keywords": bool(row.get("require_keywords")),
            })
        for row in _nb.OFFICIAL_DIRECT_SOURCES:
            official.append({
                "name": row.get("name", ""),
                "publisher": row.get("publisher", ""),
                "domain": row.get("domain_label", ""),
                "kind": row.get("kind", ""),
                "url": row.get("url", ""),
            })
        anti_keywords = [str(k) for k in _nb.ANTI_KEYWORDS]
    # SystemExit 도 잡는다. news_bot 은 자격증명이 없으면 sys.exit 하던 모듈이고
    # (2026-08-16 에 그 호출을 첫 사용 시점으로 옮겼다), SystemExit 은 Exception 이
    # 아니라 except Exception 을 그냥 통과해 빌드를 죽였다. 콘솔 한 칸 때문에
    # 배포가 멈추면 안 된다 — 못 읽었으면 그 사실을 화면에 적고 지나간다.
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — 원인을 화면에 그대로 보낸다
        feed_error = f"{type(exc).__name__}: {exc}"[:200]

    publication_sources: list[dict] = []
    pubs_error = ""
    try:
        import pubs_fetch as _pf  # noqa: PLC0415
        publication_sources = [{"id": str(row.get("id") or "")} for row in _pf.SOURCES]
        # 같은 기관이 '국제원자력기구'와 '국제원자력기구(IAEA)' 두 이름으로 실려
        # 온다. 그대로 세우면 7개 기관이 아니라 기관이 두 배로 보인다 — 괄호 앞을
        # 열쇠로 묶고 약칭이 붙은 긴 쪽을 남긴다.
        by_base: dict[str, str] = {}
        for row in (_read_json(BOT_DIR / "publications.json", {}) or {}).get("items", []):
            if not isinstance(row, dict):
                continue
            label = str(row.get("org_kr") or row.get("org") or "").strip()
            if not label:
                continue
            base = label.split("(")[0].strip()
            if len(label) > len(by_base.get(base, "")):
                by_base[base] = label
        publication_orgs = sorted(by_base.values())
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — 위와 같은 이유
        pubs_error = f"{type(exc).__name__}: {exc}"[:200]
        publication_orgs = []

    discovery = _read_json(BOT_DIR / "discovery_state.json", {})

    # 학습된 검색어(신규 이슈 탐색). 숫자만 싣던 자리에 목록을 싣는다 — 관리자가
    # 지울지 둘지 판단하려면 '몇 개인가'가 아니라 '무슨 말이고 왜 생겼나'가
    # 필요하다. 모듈을 못 읽어도 화면 한 칸 때문에 빌드가 죽지 않는다.
    learned_terms: list[dict] = []
    learned_retired: list[dict] = []
    learned_stats: dict = {}
    learned_error = ""
    try:
        import adaptive_discovery as _ad  # noqa: PLC0415

        adaptive_state = _ad.load_state()
        learned_terms = _ad.console_view(adaptive_state, generated_at)
        learned_retired = _ad.retired_view(adaptive_state)
        learned_stats = _ad.summary(adaptive_state, generated_at)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — 위 feeds 와 같은 이유
        learned_error = f"{type(exc).__name__}: {exc}"[:200]

    config_kinds = (
        "keyword_add", "keyword_remove", "anchor_add", "anchor_remove",
        "negative_add", "negative_remove", "anti_add", "anti_remove",
        "feed_add", "feed_disable", "official_disable", "tier_upsert", "tier_remove",
        "learned_term_add", "learned_term_remove", "learned_term_keep",
    )
    return {
        "generated_at": generated_at.isoformat(),
        # 콘솔 편집이 실제 파이프라인에 도달했는지를 화면이 판정하는 근거.
        # 콘솔은 KV 의 최신본과 이 목록을 대조해 "아직 반영 안 됨"을 표시한다.
        "overrides": {
            "synced_at": overlay["synced_at"],
            "updated_at": overlay["updated_at"],
            "source": overlay.get("source", ""),
            "total": overlay["total"],
            "counts": overlay["counts"],
            "error": overlay_error,
            "entries": [
                entry for entry in (_ao.load()["entries"] if _ao else [])
                if entry.get("kind") in config_kinds
            ],
        },
        "keywords": {
            "groups": keyword_groups,
            "totals": {
                "groups": len(keyword_groups),
                "keywords": sum(len(g["keywords"]) for g in keyword_groups),
                "anchors": sum(len(g["anchors"]) for g in keyword_groups),
            },
        },
        "anti_keywords": anti_keywords,
        "feeds": {
            "rss": feeds,
            "official": official,
            "error": feed_error,
        },
        "publications": {
            "sources": publication_sources,
            "orgs": publication_orgs,
            "error": pubs_error,
        },
        "search": {
            "engines": ["naver"],
            # discovery 가 성과를 보고 스스로 늘리고 줄이는 쿼리 풀. 엔티티 ×
            # 사건어 조합이라 목록으로 보여 줄 것이 없다 — 숫자만 밝힌다.
            "learned_query_count": len(discovery.get("queries") or {}),
            # 신규 이슈 탐색이 만든 임시 검색어. 이쪽은 **말 하나하나가 판단
            # 대상**이라 목록·근거·남은 시간을 전부 싣는다.
            "learned_terms": learned_terms,
            "learned_retired": learned_retired,
            "learned_stats": learned_stats,
            "learned_error": learned_error,
        },
        "source_tiers": {
            "tier1_bonus": sources_config.get("tier1_bonus"),
            "tier2_bonus": sources_config.get("tier2_bonus"),
            "rows": tiers,
        },
    }


def build_rss(briefings: list[dict], generated_at: datetime) -> bytes:
    """최신 이슈 카드를 보고서형 RSS 2.0으로 직렬화한다."""
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "nuclens 원자력 정책 브리핑"
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = "이슈 단위로 추적하는 원자력 정책 브리핑"
    ET.SubElement(channel, "language").text = "ko"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(generated_at)
    ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
        {"href": f"{SITE_URL}/rss.xml", "rel": "self", "type": "application/rss+xml"},
    )

    for briefing in briefings[:14]:
        briefing_date = briefing.get("date") or ""
        try:
            published = datetime.combine(date.fromisoformat(briefing_date), datetime.min.time(), KST)
        except ValueError:
            published = generated_at
        for issue in briefing.get("issues", [])[:20]:
            issue_id = str(issue.get("issue_id") or "")
            link = f"{SITE_URL}/issue/{quote(issue_id, safe='-_')}"
            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "title").text = str(issue.get("title") or "")
            ET.SubElement(item, "link").text = link
            ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"{issue_id}:{briefing_date}"
            ET.SubElement(item, "pubDate").text = format_datetime(published)
            description = []
            if issue.get("summary"):
                description.append(f"핵심: {issue['summary']}")
            if issue.get("latest_change"):
                description.append(f"새로 확인: {issue['latest_change']}")
            if issue.get("why_important"):
                description.append(f"왜 중요(AI 해석): {issue['why_important']}")
            if issue.get("implication"):
                description.append(f"시사점(AI 해석): {issue['implication']}")
            ET.SubElement(item, "description").text = "\n".join(description)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build() -> None:
    build_started = time.monotonic()

    def progress(phase: str, **counts: object) -> None:
        detail = " ".join(f"{key}={value}" for key, value in counts.items())
        suffix = f" {detail}" if detail else ""
        print(
            f"[build_data:progress] phase={phase} "
            f"elapsed={time.monotonic() - build_started:.1f}s{suffix}",
            flush=True,
        )

    progress("load_archive:start")
    records = load_archive()
    records, archive_quality = apply_archive_integrity_gate(records)
    if archive_quality["quarantined"] or archive_quality["sanitized"]:
        print(f"::warning::archive 무결성 게이트 — 기사 격리 "
              f"{archive_quality['quarantined']}건 / 사건일 정리 "
              f"{archive_quality['sanitized']}건")
        for sample in archive_quality["quarantine_samples"][:5]:
            print(f"  · 격리 {sample['hash']}: {sample['title'][:45]} → "
                  f"{sample['title_kr'][:45]} ({','.join(sample['codes'])})")
    validate_archive_records(records)
    deliveries = load_deliveries()
    brief_ranks = brief_ranks_by_hash()
    now = datetime.now(KST)
    generation_id = GENERATION_ID or now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cutoff_news = (now - timedelta(days=NEWS_WINDOW_DAYS)).strftime("%Y-%m-%d")

    visible = []
    for record in records:
        importance = record.get("importance", "")
        if importance == "noise" or (importance == "market" and not SHOW_MARKET):
            continue
        article_date = date_of(record)
        if not article_date:
            continue
        delivery = deliveries.get(record.get("hash", ""))
        topics, topic_source = infer_topics(record)
        countries, country_source = infer_countries(record)
        region, region_source = infer_region(record, countries, country_source)
        canonical_tags = list(dict.fromkeys(
            _canonical_tag(tag) for tag in (record.get("tags") or []) if _canonical_tag(tag)
        ))
        visible.append({
            "hash": record.get("hash", ""),
            "story_id": str((delivery or {}).get("story_id")
                            or story_cluster.fallback_story_id(record)),
            "story_id_source": str((delivery or {}).get("story_id_source") or "legacy_hash"),
            "story_id_trust": str((delivery or {}).get("story_id_trust") or "legacy"),
            "story_identity_version": int(
                (delivery or {}).get("story_identity_version") or 0),
            "date": article_date,
            "article_date": article_date,
            "briefing_date": delivery.get("date") if delivery else None,
            "region": region,
            "region_source": region_source,
            "importance": importance,
            "section": record.get("section", ""),
            "category": record.get("category", ""),
            "title_kr": record.get("title_kr") or record.get("title", ""),
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            # 원문 대신 읽는 기사 요지(3~5문장). 2026-08-07 이전 아카이브에는 없다 —
            # 화면은 빈 값을 정상으로 다뤄야 한다. 제 기사 제목과 안 맞는 요지는
            # 다른 기사의 본문이므로 여기서 걷힌다(usable_detail).
            "detail": usable_detail(record),
            "implication": record.get("implication", ""),
            "why_important": record.get("why_important", ""),
            "open_question": record.get("open_question", ""),
            "tags": record.get("tags") or [],
            "canonical_tags": canonical_tags,
            "topics": topics,
            "countries": countries,
            "topic_source": topic_source,
            "country_source": country_source,
            "features": record.get("features") or {},
            "article_type": record.get("article_type", ""),
            "url": source_url(record),
            "domain": record.get("domain", ""),
            "publisher": record.get("publisher", ""),
            "source_tier": record.get("source_tier"),
            "source_type": record.get("source_type", "unknown"),
            "evidence_role": record.get("evidence_role", "unknown"),
            "event_date": record.get("event_date"),
            "event_date_type": record.get("event_date_type", "unknown"),
            "event_date_precision": record.get("event_date_precision", "unknown"),
            "event_date_source": record.get("event_date_source", "unknown"),
            # 이 기사가 큐레이션을 실제로 받았는가. 429(RPM)로 큐레이션이 실패하면
            # fallback 이 한 배치를 통째로 nice_to_know 로 찍어 넣는데, 그 레코드는
            # features 가 null 이고 topics·tags·implication 이 전부 빈다.
            # 아래 features 필드는 투영 과정에서 {} 로 채워져 구별이 사라지므로
            # 원본 레코드를 보고 여기서 못박는다.
            "curated": record.get("features") is not None,
            "selection_score": delivery.get("score") if delivery else None,
            "selection_reasons": selection_reasons(delivery, record),
            # 텔레그램에 실제로 찍힌 카드 번호(지역별 1부터). 화면은 쓰지 않지만
            # 오디오 브리핑이 설명 순서의 기준으로 쓴다 — 웹의 issue 정렬은
            # 점수를 다시 줄 세우고 운영 콘솔의 승격·숨김까지 반영하므로
            # 발송 순서와 다르다. 옛 발송분에는 이 필드가 없으므로
            # brief_rank_fallback() 이 delivery_log 의 기록 순서로 메운다.
            "brief_rank": brief_ranks.get(str(record.get("hash") or "")),
            "brief_region": (delivery or {}).get("brief_region") or region,
            # daily_brief의 story-level dedup 결과. 웹 issue clustering과 검증 표시가
            # 같은 사건 정의를 공유하도록 delivery_log 계약을 그대로 이어받는다.
            "story_contract_available": bool(delivery and "story_article_count" in delivery),
            "story_article_count": (delivery or {}).get("story_article_count", 1),
            "story_outlet_count": (delivery or {}).get("story_outlet_count", 1),
            "story_tier1_count": (delivery or {}).get("story_tier1_count", 0),
            "story_independent_outlet_count": (delivery or {}).get("story_independent_outlet_count", 0),
            "story_relation": (delivery or {}).get("story_relation", "single"),
            "story_reason": (delivery or {}).get("story_reason", ""),
            "story_dedup_stage": (delivery or {}).get("story_dedup_stage", ""),
            "story_fingerprint": (delivery or {}).get("story_fingerprint", {}),
            "story_article_hashes": (delivery or {}).get("story_article_hashes", []),
            "story_related_titles": (delivery or {}).get("story_related_titles", []),
            "story_members": (delivery or {}).get("story_members", []),
            "story_sources": (delivery or {}).get("story_sources", []),
            "story_context": (delivery or {}).get("story_context", []),
            # 수집 단계에서 접힌 근거와, story 완성 뒤에 화면 대표를 고른 판단.
            # 운영 콘솔이 "왜 이 기사가 이 카드의 얼굴인가"에 답하는 재료다.
            "story_raw_sources": (delivery or {}).get("story_raw_sources", []),
            "story_raw_source_count": (delivery or {}).get("story_raw_source_count", 0),
            "story_display_reason": (delivery or {}).get("story_display_reason", ""),
            "story_display_candidates": (delivery or {}).get("story_display_candidates", 1),
            "story_display_swapped_from": (delivery or {}).get("story_display_swapped_from", ""),
            "story_display_swapped_from_title": (delivery or {}).get(
                "story_display_swapped_from_title", ""),
            # 보고서 검토 추천은 발송 시점의 판단이라 아카이브 레코드가 아니라
            # delivery_log 에 실려 온다 (daily_brief.plan_briefs).
            "report_pick": delivery.get("report_pick", "") if delivery else "",
            "report_pick_why": delivery.get("report_pick_why", "") if delivery else "",
            "report_pick_angles": delivery.get("report_pick_angles", []) if delivery else [],
            # 기존 프론트와의 호환용. 새 화면은 briefing_date를 사용한다.
            "promoted": delivery.get("date") if delivery else None,
        })
    # 편집 override ① — 클러스터링 전에 promote 대상을 그날 후보로 올린다.
    # 정렬 단계에서 하면 늦다: 미발송 기사는 briefing_date 가 없어 배열에 아예 없다.
    selection_overrides = load_selection_overrides()
    promoted = apply_promotions(visible, selection_overrides)
    if promoted:
        print(f"[overrides] 편집 승격 {promoted}건")

    visible.sort(key=lambda item: (item["article_date"], item.get("briefing_date") or ""), reverse=True)
    news_items = [item for item in visible if item["article_date"] >= cutoff_news]
    progress("prepare_news:done", records=len(records), news_items=len(news_items))

    embeddings = load_embeddings_cache()
    local_embeddings = build_local_embeddings(news_items)
    match_overrides = load_match_overrides()
    entity_registry = load_entity_registry()
    facility_entities = facility_entities_by_hash(
        news_items, facility_alias_entries(entity_registry)
    )
    review_candidates: list[dict] = []
    # 후보 계수기. 후보가 어느 경로에서 몇 개 나는지 세기만 한다 — 판정에는
    # 관여하지 않는다(issue_candidate_stats 머리말). 2차 패스에서 새로 만든다.
    card_telemetry = issue_candidate_stats.SearchTelemetry("card")
    evidence_telemetry = issue_candidate_stats.SearchTelemetry("evidence")
    issues = cluster_selected_articles(
        news_items,
        embeddings,
        local_embeddings,
        match_overrides,
        review_candidates,
        facility_entities,
        telemetry=card_telemetry,
    )
    progress("cluster_cards:done", issues=len(issues), candidates=len(review_candidates))
    p0_card_snapshot = card_cluster_snapshot(issues)
    evidence_attached = attach_evidence_articles(
        news_items,
        issues,
        embeddings,
        local_embeddings,
        match_overrides,
        review_candidates,
        facility_entities,
        telemetry=evidence_telemetry,
    )
    progress(
        "attach_evidence:done",
        issues=len(issues),
        attached=evidence_attached,
        candidates=len(review_candidates),
    )
    p1_regression = assert_card_clusters_unchanged(p0_card_snapshot, issues)

    # 1차 묶음에서 나온 회색지대 쌍을 LLM 에 한 번 물어보고, 같은 사건으로
    # 판정된 것만 오버라이드로 넣어 다시 묶는다. 클러스터링은 순수 계산이라
    # 두 번 돌려도 비용이 없다. 판정이 0건이면 2차 실행 자체를 건너뛴다.
    progress("llm_review:start", candidates=len(review_candidates))
    llm_verdicts, llm_stats = issue_review.review_pairs(review_candidates)
    progress(
        "llm_review:done",
        asked=llm_stats.get("asked", 0),
        failed=llm_stats.get("failed", 0),
    )
    llm_approved = {pair_id for pair_id, same in llm_verdicts.items() if same}
    # 기각도 2차 묶음에 반영한다. 승인만 넘기면 "다른 사건"이라는 판정이 버려져,
    # 유사도만으로 붙는 경로가 그대로 살아 과병합이 난다(위 거부권 주석 참고).
    # ``same`` 이 None 인 실패 건은 어느 쪽으로도 쓰지 않는다.
    llm_rejected = {pair_id for pair_id, same in llm_verdicts.items() if same is False}
    if llm_approved or llm_rejected:
        match_overrides = {
            **match_overrides,
            "llm_approved": llm_approved,
            "llm_rejected": llm_rejected,
        }
        review_candidates = []
        # 계수기도 같이 비운다. 안 그러면 1차와 2차가 겹쳐 세어져 방문 수가
        # 두 배로 보이고, 예선 순위 히스토그램에는 이미 없어진 쌍이 섞인다.
        card_telemetry = issue_candidate_stats.SearchTelemetry("card")
        evidence_telemetry = issue_candidate_stats.SearchTelemetry("evidence")
        issues = cluster_selected_articles(
            news_items,
            embeddings,
            local_embeddings,
            match_overrides,
            review_candidates,
            facility_entities,
            telemetry=card_telemetry,
        )
        p0_card_snapshot = card_cluster_snapshot(issues)
        evidence_attached = attach_evidence_articles(
            news_items,
            issues,
            embeddings,
            local_embeddings,
            match_overrides,
            review_candidates,
            facility_entities,
            telemetry=evidence_telemetry,
        )
        p1_regression = assert_card_clusters_unchanged(p0_card_snapshot, issues)
    print(f"[build_data] 이슈 병합 LLM 검수: 후보 {llm_stats['candidates']}쌍 "
          f"(캐시 {llm_stats['from_cache']} / 신규 {llm_stats['asked']} / "
          f"재질의 {llm_stats.get('reasked', 0)} / "
          f"호출 {llm_stats['calls']}회) → 병합 {llm_stats['approved']} "
          f"분리 {llm_stats['rejected']} 실패 {llm_stats['failed']} [{llm_stats['status']}]")
    print(f"[build_data] 미발송 근거 기사 부착: {evidence_attached}건 "
          f"(카드 클러스터 {len(issues)}개 고정)")
    # 쿼터로 죽으면 '병합 안 함'으로 조용히 흡수돼 후속 보도가 신규 이슈로 갈라진다.
    # 실측 2026-08-05: quota 20건 → 팍스 원전 후속(코사인 0.8716, 밴드 안)이 분리됐다.
    # 실패 통계는 issue_audit.json 에 남지만 아무도 안 보므로 로그에 크게 찍는다.
    quota_failures = (llm_stats.get("failure_reasons") or {}).get("quota", 0)
    if quota_failures:
        print(f"  !! 이슈 병합 검수 {quota_failures}쌍이 쿼터(429)로 미판정 — "
              f"모델 {llm_stats.get('model', '?')}. 후속 보도가 신규 이슈로 갈라진다. "
              f"GEMINI_REVIEW_MODEL 로 버킷을 옮길 것.")

    identity_diagnostics = resolve_local_issue_id_conflicts(issues)
    if identity_diagnostics["status"] == "degraded":
        print(
            f"::warning::identity degraded build — quarantined "
            f"{identity_diagnostics['quarantined_cluster_count']} clusters"
        )
    publish_build_mode(identity_diagnostics)

    review_candidates.sort(
        key=lambda row: (row.get("candidate_score") or 0, row.get("right_date") or ""),
        reverse=True,
    )
    checked_at = now.isoformat()
    selection_stats = load_selection_stats()
    briefings = build_briefings(news_items, issues, checked_at, load_daily_leads(),
                                selection_stats, selection_overrides)
    report_unmatched_overrides(selection_overrides)
    # entity_registry 는 클러스터링 앞에서 이미 읽었다(설비 엔티티 우선순위용).
    entity_match_evidence: list[dict] = []
    issue_catalog = build_issue_catalog(
        issues,
        briefings[0]["date"] if briefings else "",
        checked_at,
        entity_registry=entity_registry,
        entity_evidence_out=entity_match_evidence,
    )
    validate_issue_catalog_ids(issue_catalog)
    # 카드 두 번째 줄을 이슈 타임라인으로 채운다. 기사 하나만 보는 큐레이션
    # 프롬프트로는 원리상 못 만드는 문장이다 — 로이터 헤드라인에는 가뭄이 없지만
    # 그 기사가 속한 클러스터에는 다뉴브강 수위 저하부터 다 들어 있다.
    # 생성은 카탈로그 행에서만 한다(전체 타임라인). 브리핑 행은 같은 이슈의
    # 날짜별 부분집합이라 거기서 또 물으면 같은 이슈를 날짜 수만큼 중복 질의한다.
    insights, insight_stats = issue_insight.generate(issue_catalog)
    applied = issue_insight.apply(issue_catalog, insights)
    for briefing in briefings:
        applied += issue_insight.apply(briefing.get("issues") or [], insights)
    # 해석이 다 자리를 잡은 뒤에 카드 3칸의 역할 분리를 확정한다. 이 순서를 지켜야
    # 나중에 덮어쓴 해석이 변화 문장과 같은 말이 되는 것을 잡는다.
    finalize_card_fields(issue_catalog)
    for briefing in briefings:
        finalize_card_fields(briefing.get("issues") or [])
    print(f"[build_data] 이슈 해석: 후보 {insight_stats['candidates']}건 "
          f"(캐시 {insight_stats['from_cache']} / 신규 {insight_stats['asked']} / "
          f"호출 {insight_stats['calls']}회) → 적용 {applied}건 "
          f"[{insight_stats['status']}]")
    entities_view = build_entities_view(issue_catalog, entity_registry, now.isoformat())
    report_entity_stats(entity_registry, issue_catalog)
    publications = load_publications(now)
    keei_stats = attach_keei_refs(issue_catalog, publications)
    # 재질의는 '이슈 제목이 바뀌어 옛 거부 판정이 무효가 된' 몫이다. 0 이 아니면
    # 그만큼 다시 물었다는 뜻이고, 미룸이 붙으면 상한에 걸려 다음 빌드로 넘겼다는 뜻.
    keei_deferred = keei_stats.get("reask_deferred", 0)
    keei_reask = f"재질의 {keei_stats.get('reasked', 0)}"
    if keei_deferred:
        keei_reask += f"+미룸 {keei_deferred}"
    print(f"[build_data] KEEI 매칭: 후보 {keei_stats.get('candidates', 0)}쌍 "
          f"(캐시 {keei_stats.get('from_cache', 0)} / 질의 {keei_stats.get('asked', 0)} / "
          f"{keei_reask} / "
          f"호출 {keei_stats.get('calls', 0)}회) → 연결 {keei_stats.get('attached', 0)}건 "
          f"[{keei_stats.get('status', '')}]")
    keei_by_issue = {
        row["issue_id"]: row["keei_refs"]
        for row in issue_catalog if row.get("keei_refs")
    }
    for briefing in briefings:
        for row in briefing["issues"]:
            refs = keei_by_issue.get(row["issue_id"])
            if refs:
                row["keei_refs"] = refs

    # 트렌드는 기존 집계를 유지하되 커버리지가 낮으면 프론트에서 숨길 수 있게 메타를 제공한다.
    trend_pool = news_items
    day7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    day14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    day30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    tags_7, tags_prev7, tags_30, tags_all_before7 = Counter(), Counter(), Counter(), Counter()
    for record in trend_pool:
        record_date = record.get("article_date", "")
        if not record_date:
            continue
        tags = list(record.get("canonical_tags") or [])
        tags = [tag for tag in tags if tag]
        if record_date >= day7:
            tags_7.update(tags)
        elif record_date >= day14:
            tags_prev7.update(tags)
        if record_date >= day30:
            tags_30.update(tags)
        if record_date < day7:
            tags_all_before7.update(tags)

    rising = []
    for tag, count in tags_7.items():
        previous = tags_prev7.get(tag, 0)
        if count >= 3 and count > previous:
            rising.append({"tag": tag, "now": count, "prev": previous})
    rising.sort(key=lambda item: (item["now"] - item["prev"], item["now"]), reverse=True)

    new_tags = [
        {"tag": tag, "count": count}
        for tag, count in tags_7.most_common()
        if tag not in tags_all_before7 and count >= 2
    ]

    weeks, topic_series = build_topic_weeks(
        issue_catalog, [briefing["date"] for briefing in briefings])
    print(f"[build_data] 주제 추이: 온전한 주 {len(weeks)}개 "
          f"({', '.join(weeks) or '없음'}) · 주별 합계 "
          f"{[sum(series[i] for series in topic_series.values()) for i in range(len(weeks))]}")
    country_issue_30 = count_country_issues(issues, day30)
    # 장기 흐름은 최근 60일 news_items가 아니라 전체 archive에서 선정된 story만
    # 사용한다. 상세 원문을 1년치 브라우저에 보내지 않고도 분기·반기·연 추세를 본다.
    period_trends = build_period_trends(visible, now.date().isoformat())

    # 앞으로 30일 달력. 재료는 60일 창의 노출 기사(news_items)이고, 날짜와 이름을
    # **같은 절에서** 뽑아 다시 확인한다(event_calendar 머리말). 3시간마다 도는
    # 이 빌드가 창을 한 칸씩 밀므로 지난 일정은 저절로 빠진다 — 상태 파일이 없다.
    #
    # 달력이 터져도 사이트는 나가야 한다. 이 구역은 화면 한 칸이지 파이프라인이
    # 아니고, 재료가 없으면 화면이 스스로 내려가도록 이미 만들어 뒀다 —
    # 여기서 예외를 올리면 그 하루치 브리핑·이슈·흐름이 통째로 배포되지 않는다.
    # (후보 진단 집계가 같은 이유로 같은 모양을 하고 있다.)
    # 두 번째 재료: event_sources.py 가 하루 한 번 걷어 커밋한 공식 일정.
    # 파일이 없어도 달력은 기사 경로만으로 그대로 선다 — 새 수집원이 죽어도
    # 기존 화면이 무너지지 않게 하는 계약이다(publications.json 과 같은 모양).
    official_store = _read_json(BOT_DIR / "event_schedule.json", {}) or {}
    official_rows = [row for row in (official_store.get("events") or [])
                     if isinstance(row, dict)]
    try:
        calendar = event_calendar.build(news_items, now.date(),
                                        official=official_rows)
        attach_calendar_issues(calendar, issue_catalog)
    except Exception as exc:
        print(f"::warning::앞으로 30일 달력 생성 실패 — {exc} (빌드는 계속한다)")
        calendar = {"start": "", "end": "", "days": event_calendar.HORIZON_DAYS,
                    "events": [], "month_notes": [], "dropped": {"build_error": 1}}
    dropped = calendar.get("dropped") or {}
    official_shown = sum(1 for row in calendar.get("events") or []
                         if row.get("origin") == "official")
    merged = sum(1 for row in calendar.get("events") or []
                 if row.get("origin") == "official" and row.get("source_count", 1) > 1)
    print(f"[build_data] 앞으로 30일 달력: 일정 {len(calendar['events'])}건 "
          f"(공식 {official_shown}건 · 보도와 통합 {merged}건) · "
          f"이 달 중 {len(calendar['month_notes'])}건"
          + (f" · 근거 부족으로 버림 {dropped}" if dropped else ""))
    if official_rows and not official_shown:
        # 저장본에 일정이 있는데 화면에 한 건도 안 서면 창 밖이거나 판정에서
        # 전부 걸린 것이다. 둘 다 정상일 수 있지만 조용히 지나가면 안 된다.
        print(f"[build_data] 공식 일정 저장본 {len(official_rows)}건 중 창 안 0건 "
              f"— 수집 시각 {official_store.get('generated_at') or '?'}")
    # 수집이 멈춘 것과 '요즘 일정이 없는 것'은 화면에서 똑같이 보인다. 저장본이
    # 며칠째 그대로면 crawl 의 수집 단계가 안 도는 것이므로 그때는 말해야 한다.
    collected_at = str(official_store.get("generated_at") or "")[:10]
    if collected_at and collected_at < (now.date() - timedelta(days=3)).isoformat():
        print(f"::warning::공식 일정 저장본이 {collected_at} 이후로 갱신되지 않았다 "
              f"— event_sources.py 수집 단계를 확인할 것")

    trend = {
        # 금요일 주간 판세 리포트. 없으면 None → 프론트가 기존 정량 트렌드만 그린다
        # (목요일에 빈 탭이 되지 않게 하는 폴백). 트렌드 탭의 독립 패널 전용 —
        # 선택 날짜에 붙는 화면은 아래 weekly_reports 에서 그 주 것을 고른다.
        "weekly_report": load_weekly_report(issue_catalog),
        # week_start → 그 주 리포트. 날짜를 옮기면 그 날짜가 속한 주차 것을
        # 보여주기 위해 전부 싣는다. 없는 주는 키가 없다 — 다른 주 내용을
        # 대신 끼우지 않는다.
        "weekly_reports": load_weekly_reports(issue_catalog),
        # 이번 주 움직인 이슈 — 키워드 단위 흐름 해석을 대체한다(중복 제거)
        "weekly_movers": build_weekly_movers(
            issue_catalog, briefings[0]["date"] if briefings else ""),
        "open_questions": collect_open_questions(issue_catalog),
        "periods": period_trends,
        "period_unit": "briefing_story",
        "top_tags_7d": [{"tag": tag, "count": count} for tag, count in tags_7.most_common(10)],
        "top_tags_30d": [{"tag": tag, "count": count} for tag, count in tags_30.most_common(10)],
        "rising": rising[:10],
        "new_tags": new_tags[:10],
        "countries_30d": [
            {"country": country, "count": count}
            for country, count in country_issue_30.most_common(10)
        ],
        "countries_30d_unit": "issue",
        "countries_30d_counting": "distinct_issue_per_country",
        "weeks": weeks,
        "topic_series": topic_series,
        # 단위를 데이터가 말한다 — 화면 문구가 집계와 갈라지면 이슈 총수보다 큰
        # '건수'가 다시 뜬다.
        "topic_series_unit": "issue",
        # 앞으로 30일 달력(흐름 탭). 빈 칸이 많은 것은 정상이고, 한 건도 없으면
        # 화면이 구역째 내린다 — 빈 격자 31칸은 '일정이 없다'가 아니라 고장으로
        # 읽힌다. 못 찾은 것과 근거가 없어 버린 것은 위 빌드 로그가 구분한다.
        "event_calendar": calendar,
    }

    # 분류율은 **큐레이션을 받은 기사**에 대해서만 잰다.
    #
    # 이 지표의 이름이 말하는 것은 택소노미가 작동하는가이지 큐레이션이 돌았는가가
    # 아니다. 429(RPM)로 한 배치가 통째로 미큐레이션 상태로 들어오면 분모만 커져
    # **분류기 버그처럼 보인다.** 실측 2026-08-06: 표시 393건 중 무분류 41건이라
    # 0.8957 로 배포 게이트(>=0.9)가 막혔는데, 41건을 뜯어보니 37건은 큐레이션을
    # 아예 못 받은 fallback 껍데기였고 진짜 분류 실패는 4건뿐이었다.
    #
    # 미큐레이션은 지우는 게 아니라 **따로 센다**(uncurated_count). 분모에서 빼되
    # 눈에 보이게 두지 않으면 큐레이션 장애가 조용히 사라진다.
    curated_items = [item for item in news_items if item.get("curated")]
    uncurated_count = len(news_items) - len(curated_items)
    topic_coverage = (
        sum(1 for item in curated_items if item["topics"]) / len(curated_items)
    ) if curated_items else 0
    country_coverage = (
        sum(
            1 for item in news_items
            if set(item["countries"]) - {"UNSPECIFIED"}
        ) / len(news_items)
    ) if news_items else 0
    country_unspecified_count = sum(
        1 for item in news_items if "UNSPECIFIED" in set(item["countries"])
    )
    heuristic_topic_count = sum(1 for item in news_items if item["topic_source"] == "heuristic-v1")
    heuristic_country_count = sum(
        1 for item in news_items if not item["country_source"].startswith("native")
    )
    region_source_counts = Counter(item.get("region_source", "unknown") for item in news_items)
    # 국가 태그로 판정된 기사만 센다. `infer_region` 은 **명시적 scope 를 국가보다
    # 먼저** 본다(scope → countries → section → domain). 그래서 큐레이션이
    # scope 를 명시한 기사는 이 규칙의 적용 대상이 아니다.
    #
    # 실측 사고(2026-08-05, 배포 차단): "엔터지, 홀텍 SMR-300 배치 검토 위해
    # 현대건설과 협력" 은 countries=[KR, US] 인데 region=해외였다. 엔터지·홀텍의
    # **미국** 배치 검토에 현대건설이 참여하는 기사라 큐레이션이 scope=overseas 로
    # 명시했고, 그 판단이 국가 태그보다 정확하다("한국이 등장한다"와 "국내 뉴스다"는
    # 다르다 — news_bot 프롬프트의 scope 규칙과 같은 판단).
    # 지표가 scope 경로까지 위반으로 세면서 **분류가 아니라 지표가 틀린 채로**
    # deploy-web 이 빨갛게 죽었다.
    region_country_mismatch_count = sum(
        1
        for item in news_items
        if item.get("region_source") == "countries"
        and (set(item.get("countries") or []) - {"OTHER"})
        and (
            ("KR" in set(item.get("countries") or []) and item.get("region") != "국내")
            or ("KR" not in set(item.get("countries") or []) and item.get("region") != "해외")
        )
    )
    selected_items = [item for item in news_items if item.get("briefing_date")]
    remote_embedded_selected_count = sum(
        1 for item in selected_items if item["hash"] in embeddings
    )
    embedded_selected_count = sum(
        1 for item in selected_items if item["hash"] in local_embeddings
    )
    match_methods = Counter(
        diag.get("method", "none")
        for issue in issues
        for diag in issue.get("match_diagnostics", [])
    )
    cross_date_issue_count = sum(
        1 for issue in issues
        if len({member["briefing_date"] for member in issue["members"]}) > 1
    )
    latest_briefing = briefings[0] if briefings else {"issues": []}
    latest_tracked_issue_count = sum(
        1 for issue in latest_briefing.get("issues", [])
        if issue.get("previous_article_count", 0) > 0
    )
    latest_issue_count = len(latest_briefing.get("issues", []))
    # 게이트가 보는 값은 아래 누적치다. 위 latest_* 는 관측용으로 남긴다.
    tracking_window = briefings[:TRACKING_WINDOW_BRIEFINGS]
    tracking_window_issue_count = sum(
        len(briefing.get("issues", [])) for briefing in tracking_window
    )
    tracking_window_tracked_issue_count = sum(
        1
        for briefing in tracking_window
        for issue in briefing.get("issues", [])
        if issue.get("previous_article_count", 0) > 0
    )
    meta = {
        "generation_id": generation_id,
        "generated_at": now.isoformat(),
        "archive_total": len(records),
        "archive_quality": archive_quality,
        "visible_total": len(news_items),
        "briefing_total": len(briefings),
        "issue_catalog_total": len(issue_catalog),
        "identity": identity_diagnostics,
        "build_mode": identity_diagnostics["status"],
        "p1_regression": p1_regression,
        "atlas_readiness": atlas_readiness(issue_catalog),
        "latest_briefing_date": briefings[0]["date"] if briefings else "",
        "date_min": min((item["article_date"] for item in visible), default=""),
        "date_max": max((item["article_date"] for item in visible), default=""),
        "importance_counts": dict(Counter(record.get("importance", "") for record in records)),
        "source_type_counts": dict(Counter(record.get("source_type", "unknown") for record in records)),
        "evidence_role_counts": dict(Counter(record.get("evidence_role", "unknown") for record in records)),
        "publisher_coverage": round(
            sum(1 for record in records if record.get("publisher")) / len(records), 4
        ) if records else 0,
        "topic_coverage": round(topic_coverage, 4),
        "country_coverage": round(country_coverage, 4),
        # 큐레이션을 못 받은 기사 수(분류율 분모에서 빠진 몫). 0 이 아니면 그 회차에
        # 큐레이션이 실패했다는 뜻이다 — topic_coverage 에 섞이면 '분류기 버그'로
        # 오독되므로 따로 센다.
        "uncurated_count": uncurated_count,
        "taxonomy_version": "topic-v1-country-scope-v2",
        "heuristic_topic_count": heuristic_topic_count,
        "heuristic_country_count": heuristic_country_count,
        "country_unspecified_count": country_unspecified_count,
        "region_classification_version": "country-first-v1",
        "region_source_counts": dict(region_source_counts),
        "region_country_mismatch_count": region_country_mismatch_count,
        "trend_ready": topic_coverage >= 0.8 and country_coverage >= 0.8 and len(weeks) >= 2,
        "issue_matching_version": "hybrid-review-v4",
        "issue_window_days": ISSUE_WINDOW_DAYS,
        "news_window_days": NEWS_WINDOW_DAYS,
        "long_trend_window_days": LONG_TREND_WINDOW_DAYS,
        "trend_period_days": list(TREND_PERIOD_DAYS),
        "story_contract_version": STORY_CONTRACT_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_cache_entries": len(embeddings),
        "remote_embedding_selected_count": remote_embedded_selected_count,
        "local_embedding_selected_count": embedded_selected_count,
        "embedding_selected_count": embedded_selected_count,
        "embedding_selected_coverage": round(
            embedded_selected_count / len(selected_items), 4
        ) if selected_items else 0,
        "issue_match_methods": dict(match_methods),
        "cross_date_issue_count": cross_date_issue_count,
        "latest_briefing_issue_count": latest_issue_count,
        "latest_briefing_tracked_issue_count": latest_tracked_issue_count,
        "latest_briefing_tracking_rate": round(
            latest_tracked_issue_count / latest_issue_count, 4
        ) if latest_issue_count else 0,
        "tracking_window_briefings": len(tracking_window),
        "tracking_window_issue_count": tracking_window_issue_count,
        "tracking_window_tracked_issue_count": tracking_window_tracked_issue_count,
        "tracking_window_rate": round(
            tracking_window_tracked_issue_count / tracking_window_issue_count, 4
        ) if tracking_window_issue_count else 0,
        "issue_review_candidate_count": len(review_candidates),
        "issue_match_approved_count": len(match_overrides["approved"]),
        "issue_match_rejected_count": len(match_overrides["rejected"]),
    }

    insights_path = BOT_DIR / "trend_insights.json"
    try:
        insights = json.loads(insights_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        insights = {"generated_at": "", "items": []}
    insights = prepare_insights(insights, news_items)

    # 후보 진단. **자르기 전 전수**로 센다 — 배포본은 상위 5,000건만 싣고 그
    # 절단선이 코사인 0.8153 이라(2026-08-21 실측), 배포본만 보면 후보의 84.6%가
    # 안 보인다. 여기서 세면 그 구간이 채워진다.
    #
    # 정답지는 `match_diagnostics` 다 — 실제로 채택된 병합 전량이라, 어떤
    # 사전차단·상한이 **무엇을 잃는지**를 이 목록으로 잰다. 표본이 아니라 전수다.
    merge_records = [diag for issue in issues
                     for diag in issue.get("match_diagnostics", [])]
    try:
        candidate_diagnostics = issue_candidate_stats.summarize(
            review_candidates, merge_records,
            [card_telemetry, evidence_telemetry],
            selected_count=len(selected_items),
        )
    except Exception as exc:  # 계측이 빌드를 죽이면 안 된다
        print(f"::warning::후보 진단 집계 실패 — {exc} (빌드는 계속한다)")
        candidate_diagnostics = {"definition_version": "candidate-telemetry-v1",
                                 "error": str(exc), "guards": []}
    report_candidate_diagnostics(candidate_diagnostics)

    issue_audit = {
        "generated_at": now.isoformat(),
        "matching_version": "hybrid-review-v4",
        "issue_window_days": ISSUE_WINDOW_DAYS,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_threshold": ISSUE_EMBEDDING_THRESHOLD,
        "embedding_candidate_threshold": ISSUE_EMBEDDING_CANDIDATE_THRESHOLD,
        "local_embedding_candidate_threshold": LOCAL_EMBEDDING_CANDIDATE_THRESHOLD,
        "embedding_cache_entries": len(embeddings),
        "embedding_selected_count": embedded_selected_count,
        "remote_embedding_selected_count": remote_embedded_selected_count,
        "llm_review": llm_stats,
        "llm_approved": sorted(llm_approved),
        # 기각도 남긴다 — 거부권이 실제로 걸렸는지 audit 만 보고 확인할 수 있어야 한다.
        "llm_rejected": sorted(llm_rejected),
        # 후보가 어디서 몇 개 났는지. **크기가 O(1)** 이라 배포본에도 그대로 간다 —
        # 잘린 목록만 보는 사람도 전수 기준 분포는 볼 수 있어야 한다.
        "candidate_diagnostics": candidate_diagnostics,
        "identity": identity_diagnostics,
        "review_candidates": review_candidates,
        "overrides": {
            "approved": sorted(match_overrides["approved"]),
            "rejected": sorted(match_overrides["rejected"]),
        },
        # 엔티티 매칭 근거 — 오탐 디버깅용 최소 레코드(원문 복제 금지).
        # {issue_id, entity_id, matched_alias, source_field} 만 싣는다.
        "entity_matches": entity_match_evidence,
        "clusters": [
            {
                "issue_id": issue["issue_id"],
                "first_seen": issue["first_seen"],
                "last_seen": issue["last_seen"],
                "briefing_dates": sorted({member["briefing_date"] for member in issue["members"]}),
                "members": [
                    {
                        "hash": member["hash"],
                        "briefing_date": member["briefing_date"],
                        "article_date": member["article_date"],
                        "title": member["title_kr"],
                        "countries": member.get("countries") or [],
                        "facilities": sorted(set().union(*_facility_signature(member))),
                    }
                    for member in issue["members"]
                ],
                "matches": issue.get("match_diagnostics", []),
            }
            for issue in issues if len(issue["members"]) > 1
        ],
    }

    # Cloudflare Pages의 flat 배포도 manifest/status를 항상 제공한다. 프론트가
    # 존재하지 않는 선택 파일을 매번 요청해 404를 남기지 않도록 하는 계약이다.
    manifest = {
        "generation_id": generation_id,
        "generated_at": now.isoformat(),
        "base_path": "",
    }
    status = {**system_status(records, selection_stats, now),
              "generation_id": generation_id,
              "build_mode": identity_diagnostics["status"],
              "identity": identity_diagnostics}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_OUT_DIR.mkdir(parents=True, exist_ok=True)
    shipped_audit = shipped_issue_audit(issue_audit)
    outputs = (
        ("news.json", news_items),
        ("briefings.json", briefings),
        ("issues.json", issue_catalog),
        ("trend.json", trend),
        ("meta.json", meta),
        ("insights.json", insights),
        ("publications.json", publications),
        ("entities.json", entities_view),
        # 원본이 아니라 사본을 싣는다 — 아래 admin_outputs 는 전수를 봐야 한다.
        ("issue_audit.json", shipped_audit),
        ("manifest.json", manifest),
        ("status.json", status),
    )
    admin_outputs = (
        ("merges.json", build_admin_merges(news_items, issue_catalog, issue_audit, now,
                                           load_story_audits())),
        ("config.json", build_admin_config(now)),
    )
    for name, payload in outputs:
        (OUT_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    full_audit_path = write_full_issue_audit(issue_audit)
    publish_artifact_ready(shipped_audit, full_audit_path, candidate_diagnostics)
    for name, payload in admin_outputs:
        (ADMIN_OUT_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    issue_page_count = build_issue_pages(issue_catalog)
    brief_page_count = build_brief_pages(briefings)
    (SITE_DIR / "public" / "rss.xml").write_bytes(build_rss(briefings, now))

    selected_count = sum(briefing["article_count"] for briefing in briefings)
    issue_count = sum(briefing["issue_count"] for briefing in briefings)
    print(
        f"[build] 아카이브 {len(records)}건 → 표시 {len(news_items)}건 → "
        f"브리핑 기사 {selected_count}건 / 이슈 카드 {issue_count}개 / "
        f"상세 페이지 {issue_page_count}개 / 날짜 브리프 {brief_page_count}개 → {OUT_DIR}"
    )
    # 이 프로세스가 쓴 Gemini 호출을 센다. crawl.yml 은 news_bot 과 build_data 를
    # **한 잡 안에서 이어서** 돌리므로 둘이 같은 분에 겹칠 수 있다 — 429(분당 20회)의
    # 범인을 가리려면 양쪽 다 찍혀야 한다.
    try:
        import gemini_client as _gc  # noqa: PLC0415
        print(_gc.format_call_stats())
    except Exception as exc:  # 계측이 빌드를 죽이면 안 된다
        print(f"[gemini] 호출 통계 실패: {exc}")
    # 조용히 지우면 큐레이션 프롬프트가 망가진 것을 아무도 모른다. 기준선(옛 프롬프트):
    # implication 이 있는 64건 중 40건(62%). 새 프롬프트로 재큐레이션되면 내려가야 한다.
    if _HOLLOW_IMPLICATIONS:
        print(f"[build_data] 빈껍데기 해석 {len(_HOLLOW_IMPLICATIONS)}건 미표시 "
              f"(카드는 요약으로 물러남) — 예: {_HOLLOW_IMPLICATIONS[0][:52]}")
    # 요지(본문 유래)와 제목·요약(모델 유래)이 어긋난 기사. **사람이 볼 자리다** —
    # 겹침만으로는 어느 쪽이 틀렸는지 못 가르고, 실사고에서 틀린 쪽은 제목이었다.
    # must_read 로 올라간 것이 섞여 있으면 selection_overrides 로 내릴 것.
    if _DETAIL_MISMATCHES:
        seen = {row["hash"]: row for row in _DETAIL_MISMATCHES}
        flagged = [row for row in seen.values() if row["importance"] == "must_read"]
        print(f"[build_data] 요지↔제목 불일치 {len(seen)}건"
              f"{f' (must_read {len(flagged)}건 — 확인 필요)' if flagged else ''}")
        for row in list(seen.values())[:5]:
            # 번역 제목이 따로 있을 때만 원문을 덧붙인다 — 국내 기사는 같은 문자열이다.
            origin = row["title"][:34]
            same = not origin or origin == row["title_kr"][:34]
            print(f"    {row['hash']} [{row['importance']}] 겹침 {row['overlap']:.0%}\n"
                  f"      제목={row['title_kr'][:44]}"
                  f"{'' if same else f' / 원문={origin}'}\n"
                  f"      요지={row['detail'][:44]}")
    atlas = meta["atlas_readiness"]
    print(
        "[build_data:atlas] "
        + " / ".join(f"{name} {atlas['node_counts'][name]}"
                     f"({atlas['node_rates'][name] * 100:.0f}%)"
                     for name, _ in ATLAS_NODES)
        + f" | 5칸 {atlas['full_path_issues']} · 3칸+ {atlas['three_plus_issues']} → "
        + ("착수 가능" if atlas["ready"]
           else "대기: " + ", ".join(atlas["blocking_nodes"]))
    )


if __name__ == "__main__":
    build()
