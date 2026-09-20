"""보조 근거(수치 공유 · 레지스트리 actor 겹침)를 기존 판정 위에 얹었을 때의 counterfactual 재생.

production 을 바꾸지 않는다. `issue_continuity.same_issue` 와 `web.build_data.issue_similarity` 를
**그대로 호출**해 production 판정을 얻고, 그 판정이 None/미매칭인 쌍에 보조 근거를 얹었을 때 무엇이
새로 붙는지를 gold·silver 위에서 잰다. LLM · 네트워크 · 파일 변경 없음.

    signals.py   수치 정규화 v2(앞 감사의 오류 수정) · 레지스트리 actor 겹침 · 근거 판정
    replay.py    continuity(발송 카드 × 14일) · web(회색지대 쌍) 두 자리의 counterfactual
"""
