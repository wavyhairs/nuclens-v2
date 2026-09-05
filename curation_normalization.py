"""Pure normalization for controlled curation values."""

VALID_SCOPES = {"kr", "overseas"}

# 통제 태그 (웹 트렌드 집계용 — 프롬프트 D 섹션과 반드시 일치)
VALID_TOPICS = {
    "smr", "newbuild", "restart_lto", "fuel_cycle", "waste", "finance",
    "regulation", "power_market", "datacenter_ai", "fusion",
    "security_trade", "fukushima",
}
# 국가는 임의의 화이트리스트가 아니라 ISO 3166-1 alpha-2 전체를 허용한다.
# EU/EUROPE/GLOBAL/UNSPECIFIED는 국가 코드와 섞이지 않도록 의미가 고정된 범위 코드다.
ISO_ALPHA2_COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ
VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())
COUNTRY_SCOPE_CODES = {"EU", "EUROPE", "GLOBAL", "UNSPECIFIED"}
VALID_COUNTRIES = ISO_ALPHA2_COUNTRIES | COUNTRY_SCOPE_CODES
COUNTRY_ALIASES = {
    "UK": "GB",             # 관용 코드 → ISO 코드
    "EU_ETC": "UNSPECIFIED",  # 폐기된 묶음 코드
    "OTHER": "UNSPECIFIED",   # 폐기된 모호 코드
}
VALID_ARTICLE_TYPES = {
    "policy", "official_doc", "corporate", "analysis", "opinion", "report", "news",
}


def norm_scope(value) -> str:
    """LLM의 scope 값을 정규화. 유효하지 않으면 빈 문자열.

    추정하지 않는다 — 값이 없으면 daily_brief.region() 이 section·도메인·제목
    언어로 판단한다 (같은 추정 로직을 두 곳에 두지 않기 위함).
    """
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in VALID_SCOPES else ""


def norm_topics(value) -> list[str]:
    """통제 태그 topics 정규화 — 목록 밖 값은 버린다 (트렌드 축 오염 방지)."""
    if not isinstance(value, list):
        return []
    out = [t.strip().lower() for t in value if isinstance(t, str)]
    return [t for t in out if t in VALID_TOPICS][:3]


def norm_countries(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for country in value:
        if not isinstance(country, str):
            continue
        code = COUNTRY_ALIASES.get(country.strip().upper(), country.strip().upper())
        if code in VALID_COUNTRIES and code not in out:
            out.append(code)
    return out[:2]


def norm_article_type(value) -> str:
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in VALID_ARTICLE_TYPES else "news"
