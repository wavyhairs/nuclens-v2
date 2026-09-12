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
                      "에너지믹스", "power market",
                      # 2026-09-10 추가. 전력거래소 공지사항을 수집원에 들이면서
                      # 실측한 구멍이다 — 'ESS중앙계약시장 사업자 의견수렴 간담회',
                      # '전력거버넌스 포럼', '전기국가 및 수요·재생e 전망 정책토론회'
                      # 가 전부 no_interest_match 로 떨어졌다. 전력시장·계통 제도를
                      # 다루는 자리인데 위 어휘가 그 말을 하나도 담고 있지 않았다.
                      #
                      # '전력' 한 낱말은 넣지 않는다. 넣으면 '전력기자재 사절단'
                      # '전력구입비 절감 사례 소개' 까지 관심 분야가 되고, 그때부터
                      # 이 게이트를 막는 것은 중요도 판정 하나뿐이다.
                      "전력거래", "전력수요", "전력망", "전력공급", "전력거버넌스",
                      "계통운영", "계통해석", "에너지시장", "에너지정책",
                      "에너지수급", "전력수급기본계획",
                      # 제도를 실제로 움직이는 기관 이름. 기사 제목은 제도 이름
                      # 대신 기관 이름만 쓰는 일이 흔하다.
                      "한국전력공사", "한국전력", "한전", "전력거래소", "kpx")),
    # 재생·분산·무탄소. 원자력 그 자체는 아니지만 전원믹스를 두고 같은 자리에서
    # 다투는 주제라 이 달력이 볼 값이 있다 (사용자 요청 2026-09-10).
    #
    # 'ess' 를 낱말로 넣지 않는다 — 판정은 소문자로 눕힌 문자열의 부분일치라
    # 'business'·'process'·'assessment' 가 전부 걸린다. 실제 표기를 그대로 적는다.
    ("renewable_grid", ("재생에너지", "재생e", "신재생", "분산에너지", "분산자원",
                        "가상발전소", "vpp", "에너지저장", "ess중앙계약",
                        "ess 중앙계약", "재생e 입찰", "재생에너지 입찰",
                        "renewable energy", "distributed energy")),
    ("carbon_free", ("무탄소", "무탄소전원", "cfe", "청정에너지",
                     "carbon free", "carbon-free")),
    ("regulation", ("원자력안전", "원안위", "안전규제", "규제기관", "운영허가",
                    "건설허가", "주기적안전성", "내진", "피폭", "방재",
                    # 방사선 차폐·방호. 위 OFF_TOPIC_TERMS 를 **먼저** 지나므로
                    # 방사선종양·방사선의학은 여기 닿기 전에 걸린다. 남는 것은
                    # 원자로 차폐 공학이다(실측: '제15회 방사선 차폐 국제회의
                    # (ICRS15)' 가 no_interest_match 로 떨어졌다 — 제주에서
                    # 열리는 원자력 분야 국제 학술대회다).
                    "방사선차폐", "방사선 차폐", "방사선방호", "방사선 방호",
                    "선량한도", "방사선작업",
                    "nuclear safety", "nuclear regulat", "radiation shielding")),
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
                 "atomic", "iaea", "wna symposium", "kaeri", "kins", "khnp",
                 # 운영 중인 호기 이름. 기사 제목은 '원전' 이라는 낱말 없이
                 # 발전소 이름만 쓰는 일이 흔하다 — 실측: '한빛 2호기, 9월 11일
                 # 설계수명 만료로 가동 정지' 가 no_interest_match 로 떨어졌다.
                 # 이 달력이 세워야 할 가장 분명한 일정 중 하나다.
                 #
                 # '고리' 는 단독으로 넣지 않는다 — '연결고리'·'고리대금' 이
                 # 걸린다. 발전소를 가리킬 때 실제로 쓰는 표기만 적는다.
                 "한빛", "한울", "월성", "신한울", "새울", "고리원전",
                 "고리 원전", "고리1호기", "고리 1호기", "영광원전", "울진원전",
                 # 사업자 이름. 표에 'khnp' 는 있었는데 **한글 표기가 없었다** —
                 # 국내 기사는 거의 언제나 '한수원' 이라고 쓴다(실측: '한수원,
                 # 토론회 개최' 가 no_interest_match 로 떨어졌다).
                 "한수원", "한국수력원자력", "원자력연구원", "원자력환경공단")

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

# 기사 경로에서만 절차로 치는 것. 맨 '공모'·'모집' 이다.
#
# 공식 경로(`judge`)에 넣으면 안 된다 — 기관 게시판의 '공모'·'모집' 은 대부분
# 조달·채용이고, KPX 공지 회귀 테스트가 바로 그 자리를 지키고 있다. 반면 기사가
# '공모' 를 말할 때는 방폐장 부지공모·신규 원전 명칭 공모처럼 마감일이 곧 정책
# 절차인 경우가 많다(실측: '영덕군, 신규 원전 명칭 공모…10월 최종 결과 발표'
# 가 not_an_event 로 떨어졌다). NOISE_TERMS 를 먼저 지나므로 채용·공채·
# 교육생 모집·공모전은 여기 닿기 전에 걸린다.
REPORTED_PROCESS_FORMS = PROCESS_FORMS + ("공모", "모집")

# 행사도 절차도 아니지만 **그 날 정책·산업이 실제로 움직이는** 자리. 기사에서
# 유도한 일정에만 쓴다.
#
# 왜 기사 경로에만 필요한가 (2026-09-10 실측): 기관 게시판은 '무엇을 언제 여는가'
# 를 공지하므로 위 두 표(EVENT_FORMS·PROCESS_FORMS)로 충분하다. 기사는 다르다 —
# 달력에 서야 할 가장 중요한 일정이 행사가 아니라 **마일스톤**인 경우가 많다.
# 그 표 둘만 쓰면 아래가 전부 not_an_event 로 떨어졌다:
#     '한빛 2호기, 9월 11일 설계수명 만료로 가동 정지'
#     'SMR 상용화 일정 가속화, 9월 특별법 시행'
#     '919 기후정의행진'
# 이것들은 토론회보다 값이 크다. 그래서 표를 하나 더 둔다.
#
# 이 표를 공식 경로에 넣지 않는 이유는 게시판의 성질 때문이다 — '시행'·'만료'는
# 조달 공고 제목에도 흔해서, 공식 경로에 풀면 `judge` 가 막고 있던 것이 다시 샌다.
MILESTONE_FORMS = ("설계수명", "수명만료", "운영허가 만료", "계속운전",
                   "가동 정지", "가동정지", "가동 중단", "재가동",
                   "시행", "발효", "공포", "시행일", "만료", "종료",
                   "본회의", "국정감사", "국무회의", "의결", "가결", "표결",
                   "착공", "준공", "기공", "상업운전", "임계", "장전",
                   "집회", "행진", "시위", "파업", "선고", "공판",
                   "정상회담", "회담", "방한", "순방", "총회")

# 주제가 맞고 형식도 맞지만 정책·산업 중요도가 없는 것. **형식 판정보다 먼저**
# 본다 — '신입사원 입문 과정 교육생 모집'에는 '과정'이, 사진 공모전 홍보에는
# '공모전'이 있어 순서를 뒤집으면 형식 표지에 먼저 걸린다.
#
# 주의: 맨 '공모'는 여기 넣지 않는다. 이 도메인에서 공모는 방폐장 부지공모라
# keywords.json 이 명시적으로 경고하고 있다 — 자르려면 '공모전'으로 적는다.
NOISE_TERMS = ("채용", "공채", "신입 공채", "신입사원", "경력사원", "교육생 모집", "수강생 모집",
               "수료", "자격시험", "공모전", "사진전", "동호회", "체육대회",
               "야유회", "인사발령", "임원 인사", "부고", "기념품",
               "입찰", "낙찰", "견적", "용역 공고", "공급업체", "등록안내",
               "사업수행능력", "사옥 이전", "청사 이전", "공사 안내",
               "홈페이지 점검", "서버 점검", "휴무", "홍보")


# 잡음어와 **글자가 겹칠 뿐** 이 도메인에서는 제도 이름인 말. 잡음 판정보다
# 먼저 본다.
#
# 실측(2026-09-10, KPX 공지): 'VPP사업자 및 재생e 입찰제 참여 발전사업자 대상
# 제도 개선 간담회 시행' 이 NOISE_TERMS 의 '입찰' 에 걸려 버려졌다. 여기서 말하는
# 입찰은 조달 입찰이 아니라 **제주 재생에너지 입찰제**, 곧 전력시장 제도 그
# 자체다. 반대로 같은 게시판의 ''26년도 ESS 중앙계약시장 입찰공고 개설 관련…'
# 은 진짜 조달 공고라 계속 걸려야 한다 — 그래서 '입찰' 을 통째로 풀지 않고
# '입찰제'·'입찰시장' 이라는 **더 긴 표기가 실제로 있을 때만** 면제한다.
NOISE_EXEMPT = ("입찰제", "입찰시장")


def _hay(*parts: object) -> str:
    """판정에 쓰는 한 줄. 대소문자를 눕혀 영문 표기의 흔들림을 없앤다."""
    return " ".join(str(part or "") for part in parts).lower()


def _hit(hay: str, terms) -> str:
    """걸린 낱말 하나. 없으면 빈 문자열 — 어떤 말이 걸렸는지 남기려고 값을 돌려준다."""
    for term in terms:
        if term in hay:
            return term
    return ""


def _noise_hit(hay: str) -> str:
    """걸린 잡음어. 더 긴 제도 이름의 일부일 뿐이면 잡음이 아니다."""
    for term in NOISE_TERMS:
        if term not in hay:
            continue
        if any(term in phrase and phrase in hay for phrase in NOISE_EXEMPT):
            continue
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
    noise = _noise_hit(hay)
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


def judge_reported(title: object, label: object = "", clause: object = "") -> dict:
    """기사에서 유도한 일정을 화면에 세울 것인가.

    왜 `judge` 를 그대로 쓰지 않는가
    --------------------------------
    `judge` 는 **공지 제목**을 읽으라고 만든 판정이다. 기사 경로에 그대로 걸면
    양쪽으로 틀린다(2026-09-10 실측, 달력 기사 경로 48건 전수):

      · 통과 1건 / 탈락 47건. 탈락 중에는 '한빛 2호기 설계수명 만료',
        'SMR 특별법 시행' 처럼 이 달력이 가장 세워야 할 것이 섞여 있었다 —
        `EVENT_FORMS` 는 토론회·세미나의 표이지 마일스톤의 표가 아니다.
      · 반대로 지금 기사 경로에는 판정이 **아예 없어서** 'Qnity 신임 CFO 선임',
        'CME GPU 선물 출시', '전국 자전거 동호인 친환경 레이스', '무비데이'가
        그대로 칸에 섰다.

    그래서 관심 분야는 `judge` 와 **같은 표**로 재고(두 화면이 같은 주제 어휘를
    써야 독자가 둘을 잇는다), 형식만 `MILESTONE_FORMS` 를 더해 넓힌다.

    근거 문장(`clause`)은 **관심 분야 판정에만** 넣는다. 중요도까지 문장으로
    재면 어느 문장에나 있는 '개최'·'시행'이 걸려 판정이 무력해진다 — 공식
    경로가 장소(`place`)를 중요도에서 빼는 것과 같은 이유다.
    """
    topic_verdict = relevance(title, label, clause)
    if not topic_verdict["ok"]:
        return {"ok": False, "reason": topic_verdict["reason"],
                "topics": [], "grounds": {"relevance": topic_verdict["ground"]}}
    hay = _hay(title, label)
    noise = _noise_hit(hay)
    if noise:
        return {"ok": False, "reason": "low_significance",
                "topics": topic_verdict["topics"],
                "grounds": {"relevance": topic_verdict["ground"],
                            "significance": noise}}
    for form, terms in (("event", EVENT_FORMS),
                        ("process", REPORTED_PROCESS_FORMS),
                        ("milestone", MILESTONE_FORMS)):
        hit = _hit(hay, terms)
        if hit:
            return {"ok": True, "reason": "", "topics": topic_verdict["topics"],
                    "form": form,
                    "grounds": {"relevance": topic_verdict["ground"],
                                "significance": hit}}
    return {"ok": False, "reason": "not_an_event",
            "topics": topic_verdict["topics"],
            "grounds": {"relevance": topic_verdict["ground"],
                        "significance": ""}}
