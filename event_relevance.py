"""공식 일정원에서 걷어 온 일정을 두 번 거른다 — **관심 분야인가**, **실을 만한가**.

왜 두 번인가
------------
공식 기관이 올렸다는 사실은 그 일정이 사실이라는 근거이지, 이 달력에 실릴
이유가 아니다. 실측(2026-08-29)으로 확인한 두 종류의 잡음은 서로 다른 자리에서
난다.

  ① **주제가 다르다.** 원자력 기관의 일정표에도 원자력이 아닌 일이 실린다.
     한국원자력산업협회 Monthly Calendar 576건에는 '대한핵의학회 추계 학술대회'
     '대한방사선종양학회 정기학술대회'가 함께 있다. 방사성 동위원소를 쓴다는
     점만 같고, 원자력 정책·산업과는 남남이다.
     국회 행사알림은 더하다 — 2026-09-03 하루치 10건 중 이 달력이 볼 것은
     '국가전력망 민간참여…전력산업 공공성 강화를 위한 정책 연속세미나' 하나뿐이고
     나머지는 인플루엔자·영화영상·반도체·해양MRO·자동차 온실가스·Agentic AI·
     초등교육·아트갤러리·버스킹이다.

  ② **주제는 맞는데 일정이 아니다.** 한국원자력환경공단 공지사항 상위 3건은
     '사업수행능력평가(PQ) 세부평가기준(안) 공개'·'공급업체 등록안내 공고'·
     '제1회 5대강 사진 공모전 홍보'다. 셋 다 원자력 기관의 공지이지만 달력 칸에
     설 일이 아니다. 한국원자력산업협회 공지사항의 '신입사원 입문 과정 교육생
     모집'도 같은 자리다.

①은 **관심 분야 판정**(relevance)이, ②는 **정책·산업 중요도 판정**
(significance)이 막는다. 둘을 한 점수로 합치지 않는 이유는 버린 까닭을 남기기
위해서다 — 어느 날 달력이 비면 '못 찾았다'와 '주제가 아니다'와 '일정이 아니다'를
가를 수 있어야 수집원을 고칠지 게이트를 고칠지 안다.

무엇을 하지 않는가
------------------
* LLM 에게 묻지 않는다. 판정 근거는 전부 원문의 부분 문자열이고, 어떤 낱말이
  걸렸는지 그대로 돌려준다(`grounds`).
* 기관을 믿고 통과시키지 않는다. '원자력' 기관의 일정표라는 사실은 그 일정
  하나하나의 주제를 보증하지 않는다(위 ①).
* 애매하면 버린다. 칸을 채우려고 통과시키지 않는다 — 이 달력의 빈 칸은 정상이다.
"""

from __future__ import annotations

# 관심 분야. 값은 news_bot.VALID_TOPICS 와 같은 어휘를 쓴다 — 달력 칩이 사이트의
# 다른 화면과 같은 주제 이름을 달아야 독자가 둘을 잇는다.
INTEREST_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("smr", ("smr", "소형모듈원자로", "소형모듈원전", "혁신형 smr", "i-smr",
             "small modular")),
    ("waste", ("방폐물", "방사성폐기물", "방사성 폐기물", "사용후핵연료",
               "사용후 핵연료", "고준위", "중저준위", "방폐장", "처분장",
               "건식저장", "심층처분", "radioactive waste", "spent fuel",
               "used fuel", "repository")),
    ("fuel_cycle", ("핵연료", "농축", "재처리", "파이로", "우라늄", "haleu",
                    "연료주기", "nuclear fuel", "fuel cycle", "topfuel",
                    "uranium", "enrichment")),
    ("power_market", ("전력수급", "전기본", "전원믹스", "전력시장", "전력계통",
                      "송전", "전기요금", "전력정책", "전력산업",
                      "에너지믹스", "power market")),
    ("regulation", ("원자력안전", "원안위", "안전규제", "규제기관", "운영허가",
                    "건설허가", "주기적안전성", "내진", "피폭", "방재",
                    "nuclear safety", "nuclear regulat")),
    ("newbuild", ("신규원전", "신규 원전", "신한울", "새울", "천지원전",
                  "대진원전", "원전 건설", "new nuclear", "newbuild",
                  "apr1400")),
    ("restart_lto", ("계속운전", "수명연장", "재가동", "장기가동",
                     "long-term operation")),
    ("security_trade", ("원전수출", "원전 수출", "핵비확산", "비확산", "핵통제",
                        "안전조치", "두코바니", "체코 원전", "폴란드 원전",
                        "non-proliferation", "safeguards")),
    ("fusion", ("핵융합", "fusion", "iter")),
    ("finance", ("원전 생태계", "원전생태계", "원전기업", "원자력산업",
                 "원전산업", "원전해체", "해체산업", "decommission")),
    ("datacenter_ai", ("데이터센터", "data center", "datacenter")),
    ("fukushima", ("후쿠시마", "오염수", "처리수", "fukushima")),
)

# 위 어느 갈래에도 안 걸리지만 원자력 그 자체를 말하는 낱말. 주제 이름을 하나로
# 못 박기 어려운 일반 표기라 별도로 둔다(예: '원자력계 조찬강연회').
GENERIC_TERMS = ("원자력", "원전", "핵발전", "원자로", "nuclear", "reactor",
                 "atomic", "iaea", "wna symposium", "kaeri", "kins", "khnp")

# 낱말은 원자력을 닮았지만 이 달력의 주제가 아닌 것. **관심어보다 먼저** 본다 —
# '핵의학'에는 '핵'이, '방사선종양학회'에는 '방사선'이 들어 있어 순서를 뒤집으면
# 전부 통과한다(실측: 협회 일정표의 대한핵의학회·대한방사선종양학회 학술대회).
OFF_TOPIC_TERMS = ("핵의학", "핵자기공명", "방사선종양", "방사성의약품",
                   "방사선의학", "원자력병원", "핵산", "핵심광물",
                   "nuclear medicine", "radiopharm", "radiation oncology")

# 일정으로서 의미가 있는 자리. 이 표지가 없으면 '무엇을 언제 한다'가 아니라
# 그냥 공지다 — 사진 공모전 홍보·공급업체 등록안내가 여기서 걸린다.
EVENT_FORMS = ("토론회", "공청회", "간담회", "설명회", "세미나", "심포지엄",
               "심포지움", "포럼", "학술대회", "학술발표회", "연차대회",
               "총회", "컨퍼런스", "콘퍼런스", "워크숍", "워크샵", "국제회의",
               "강연회", "발표회", "전시회", "박람회", "공개토론", "협의회",
               "조찬", "대토론", "conference", "symposium", "seminar",
               "forum", "workshop", "summit", "expo", "congress")

# 행사는 아니지만 날짜가 정책 절차를 움직이는 자리. 의견을 받는 창이 닫히는 날은
# 달력이 말할 값이 있다.
PROCESS_FORMS = ("입법예고", "행정예고", "의견수렴", "의견제출", "공람", "열람",
                 "공고 마감", "접수 마감", "제안요청", "부지공모", "공론화")

# 주제가 맞고 형식도 맞지만 정책·산업 중요도가 없는 것. **형식 판정보다 먼저**
# 본다 — '신입사원 입문 과정 교육생 모집'에는 '과정'이, 사진 공모전 홍보에는
# '공모전'이 있어 순서를 뒤집으면 형식 표지에 먼저 걸린다.
#
# 주의: 맨 '공모'는 여기 넣지 않는다. 이 도메인에서 공모는 방폐장 부지공모라
# keywords.json 이 명시적으로 경고하고 있다 — 자르려면 '공모전'으로 적는다.
NOISE_TERMS = ("채용", "신입사원", "경력사원", "교육생 모집", "수강생 모집",
               "수료", "자격시험", "공모전", "사진전", "동호회", "체육대회",
               "야유회", "인사발령", "임원 인사", "부고", "기념품",
               "입찰", "낙찰", "견적", "용역 공고", "공급업체", "등록안내",
               "사업수행능력", "사옥 이전", "청사 이전", "공사 안내",
               "홈페이지 점검", "서버 점검", "휴무", "홍보")


def _hay(*parts: object) -> str:
    """판정에 쓰는 한 줄. 대소문자를 눕혀 영문 표기의 흔들림을 없앤다."""
    return " ".join(str(part or "") for part in parts).lower()


def _hit(hay: str, terms) -> str:
    """걸린 낱말 하나. 없으면 빈 문자열 — 어떤 말이 걸렸는지 남기려고 값을 돌려준다."""
    for term in terms:
        if term in hay:
            return term
    return ""


def topics(*parts: object) -> list[str]:
    """이 일정이 걸치는 관심 분야. 없으면 빈 목록이다."""
    hay = _hay(*parts)
    found = [topic for topic, terms in INTEREST_TERMS if _hit(hay, terms)]
    # 순서는 표에 적힌 순서를 따른다 — 매 실행 같은 순서로 나와야 저장본 diff 가
    # 주제 순서 때문에 흔들리지 않는다.
    return found[:3]


def relevance(*parts: object) -> dict:
    """① Nuclens 관심 분야인가.

    돌려주는 값은 판정과 **그 근거가 된 낱말**이다. 통과든 탈락이든 왜 그랬는지
    원문의 말로 남는다.
    """
    hay = _hay(*parts)
    off = _hit(hay, OFF_TOPIC_TERMS)
    if off:
        return {"ok": False, "reason": "off_topic", "ground": off, "topics": []}
    matched = topics(*parts)
    if matched:
        term = _hit(hay, dict(INTEREST_TERMS)[matched[0]])
        return {"ok": True, "reason": "", "ground": term, "topics": matched}
    generic = _hit(hay, GENERIC_TERMS)
    if generic:
        # '원자력계 조찬강연회'처럼 분야를 못 박을 말이 없어도 원자력 그 자체를
        # 말하면 통과시킨다. 주제 이름은 비워 둔다 — 없는 분류를 지어내지 않는다.
        return {"ok": True, "reason": "", "ground": generic, "topics": []}
    return {"ok": False, "reason": "no_interest_match", "ground": "", "topics": []}


def significance(*parts: object) -> dict:
    """② 정책·산업 중요도가 있는가 — 달력 한 칸을 줄 만한 일인가."""
    hay = _hay(*parts)
    noise = _hit(hay, NOISE_TERMS)
    if noise:
        return {"ok": False, "reason": "low_significance", "ground": noise,
                "form": ""}
    form = _hit(hay, EVENT_FORMS)
    if form:
        return {"ok": True, "reason": "", "ground": form, "form": "event"}
    process = _hit(hay, PROCESS_FORMS)
    if process:
        return {"ok": True, "reason": "", "ground": process, "form": "process"}
    return {"ok": False, "reason": "not_an_event", "ground": "", "form": ""}


def judge(title: object, host: object = "", place: object = "",
          extra: object = "") -> dict:
    """두 판정을 함께. 하나라도 걸리면 그 사유로 버린다.

    `host` 는 **그 행사의 주최**여야 하고, 그것을 실어 나른 기관이어서는 안 된다.
    둘을 섞었다가 실측에서 바로 틀렸다(2026-08-29): 협회 일정표의 모든 행에
    수집 기관 이름 '한국원자력산업협회'를 주최로 넣었더니 그 안의 '원자력산업'이
    관심어로 걸려 **도쿄의 태양광·전지 전시회 'Smart Energy Week 2026' 까지
    통과**했고, 53건 전부가 finance 주제를 달았다. 게시판의 주인은 그 게시판에
    실린 일 하나하나의 주제를 보증하지 않는다.

    그래서 주최는 행사마다 다른 값이 올 때만 넘긴다 — 국회 행사알림의 `orgNm`
    ('김주영 의원실, 혁신더하기연구소, 전기신문')이 그런 값이다. 협회·학회
    게시판처럼 주최 칸이 곧 게시판 주인인 곳은 빈 문자열을 넘기고, 판정은 행사
    이름과 장소만으로 한다.
    """
    topic_verdict = relevance(title, host, place, extra)
    if not topic_verdict["ok"]:
        return {"ok": False, "reason": topic_verdict["reason"],
                "topics": [], "grounds": {"relevance": topic_verdict["ground"]}}
    # 중요도는 **제목과 주최만** 본다. 장소를 넣으면 '국회도서관 대강당'이나
    # '제8간담회의실' 같은 방 이름이 형식 표지로 오인될 자리가 생긴다
    # (실측: 국회 행사알림의 placeNm 은 대부분 '의원회관 제N간담회의실').
    weight = significance(title, host)
    if not weight["ok"]:
        return {"ok": False, "reason": weight["reason"],
                "topics": topic_verdict["topics"],
                "grounds": {"relevance": topic_verdict["ground"],
                            "significance": weight["ground"]}}
    return {"ok": True, "reason": "", "topics": topic_verdict["topics"],
            "form": weight["form"],
            "grounds": {"relevance": topic_verdict["ground"],
                        "significance": weight["ground"]}}
