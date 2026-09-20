// 렌더러 계약 — **의미를 만들지 않는다.**
//
// 이 파일이 생긴 이유는 실제 사고 하나다. `pickEditorialArt` 가 그림과 도장을
// 고르면서 세 분기에서 `headline` 까지 시연용 고정 문구로 덮었다(법안 가결,
// 협력 채널 신설, MOU 서명 연기). 걸리는 조건이 `/대미|웨스팅하우스|MOU|한미/`
// 처럼 매우 넓다 — 한미 협력 기사는 거의 매주 나오므로, 그 주제의 카드는 그날
// 무슨 일이 있었든 몇 주 전 문구가 적힌 채 나갈 수 있었다.
//
// 검증과 편집 QA 는 전부 이 앞 단계에 있어서 아무도 못 잡는다 — **검증한
// 문장과 인쇄된 문장이 다른 것**이 문제의 본질이고, 그건 렌더러 쪽에서만
// 잡힌다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(here, "..", "build.js"), "utf8");

let passed = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.error(`  ✗ ${name}\n    ${error.message}`);
    process.exitCode = 1;
  }
}

console.log("렌더러는 의미를 바꾸지 않는다");

check("art selector 가 headline 을 받지도 돌려주지도 않는다", () => {
  const signature = source.match(/function pickEditorialArt\(([^)]*)\)/);
  assert.ok(signature, "pickEditorialArt 를 못 찾았다");
  assert.equal(signature[1].trim(), "haystack",
    "headline 을 다시 받기 시작했다 — 덮어쓸 길이 열린다");
  const body = source.slice(source.indexOf("function pickEditorialArt"));
  const ret = body.match(/return \{ image, stamp[^}]*\};/);
  assert.ok(ret, "반환문을 못 찾았다");
  assert.ok(!ret[0].includes("headline"), "반환값에 headline 이 돌아왔다");
});

check("고정 제목 문구가 코드에 남아 있지 않다", () => {
  // 세 분기가 덮어쓰던 문구. 여기서도 글자 그대로 적지 않는다 — 복사해
  // 되살리기 쉬워진다. 조각으로 찾는다.
  for (const ghost of ["비용 부담 법안 [[", "협력 [[채널", "서명 [[연기"]) {
    assert.ok(!source.includes(ghost), `덮어쓰기 문구가 살아 있다: ${ghost}`);
  }
});

check("editorialFromStep 이 headline 을 다시 대입하지 않는다", () => {
  const start = source.indexOf("function editorialFromStep");
  const body = source.slice(start, source.indexOf("\nfunction shell", start));
  // 객체 리터럴에서 `headline,` 또는 `headline:` 으로 값을 넣으면 덮어쓴 것이다.
  // 스프레드(...slide)로 들어오는 원본만 남아야 한다.
  assert.ok(!/^\s*headline[,:]/m.test(body),
    "editorialFromStep 이 headline 을 다시 쓴다");
});

check("덱이 why[0] 을 재사용하지 않는다", () => {
  const start = source.indexOf("function editorialFromStep");
  const body = source.slice(start, source.indexOf("\nfunction shell", start));
  const deck = body.match(/deck:\s*([^\n]*)/);
  assert.ok(deck, "deck 대입을 못 찾았다");
  assert.ok(!deck[1].includes("why["),
    "사실이 둘뿐인 날 why[0] 이 덱과 whyLead 두 자리에 같은 글자로 찍힌다");
});

console.log("\n일일 카드 머리는 한국어만 쓴다");

check("WHAT HAPPENED · WHY IT MATTERS 가 마크업에 없다", () => {
  // 주석에는 남는다(왜 걷었는지를 적어 둔 자리). 마크업 줄에서만 찾는다.
  const markup = source.split("\n")
    .filter((line) => line.includes("editorial-section-head") && line.includes("${"))
    .join("\n");
  assert.ok(markup.length > 0, "구역 머리 마크업을 못 찾았다");
  assert.ok(!markup.includes("WHAT HAPPENED"), "영문 라벨이 살아 있다");
  assert.ok(!markup.includes("WHY IT MATTERS"), "영문 라벨이 살아 있다");
  assert.ok(markup.includes("확인된 사실") && markup.includes("왜 중요한가"),
    "한국어 라벨이 사라졌다");
});

check("빈 문자열로 숨기는 방식이 아니다", () => {
  // `sectionA`/`sectionB` 자체가 사라져야 한다 — 남아 있으면 슬라이드가 값을
  // 넘기는 순간 영문이 되살아난다.
  assert.ok(!/s\.sectionA/.test(source), "sectionA 가 남아 있다");
  assert.ok(!/s\.sectionB/.test(source), "sectionB 가 남아 있다");
  // 라벨 이름도 whyLabel 관례로 통일했다 — "A 는 영문, B 는 한글" 구조가
  // 이름에 남아 있으면 그 구조가 계속 암시된다.
  assert.ok(source.includes("s.factsLabel"), "factsLabel 이 없다");
});

check("스토리의 디자인용 영문 도장은 그대로다", () => {
  assert.ok(source.includes("editorial-stamp"), "도장이 사라졌다");
  assert.ok(source.includes("ENERGY INFRASTRUCTURE"),
    "도장 문구까지 같이 걷어냈다 — 걷을 것은 본문 구역 머리뿐이다");
});

console.log("\n제목 맞춤은 먼저 맞는 배치가 아니라 크게 앉는 배치를 고른다");

// 이 검사가 있는 이유도 실제 사고다. 예전 맞춤은 강조를 제 줄에 세우는 배치로
// 먼저 줄여 보고 **그게 실패할 때만** 인라인으로 내렸다. 그래서 블록이 작은
// 크기에서 우연히 두 줄에 들어맞으면 거기서 멈췄고, 더 크게 앉는 인라인은
// 시도조차 되지 않았다 — 칸이 넓어질수록 제목이 작아지는 구간이 생긴다
// (실측 09-20 `미 에너지부 … 긴급명령`: 칸 612px 에서 70px, 649px 에서 52px).
//
// 렌더 가드는 이걸 못 잡는다. 52px 로 앉은 제목은 넘치지 않기 때문이다.
check("두 배치를 모두 재고 큰 쪽을 고른다", () => {
  const start = source.indexOf("const fit = (inline)");
  assert.ok(start > 0, "제목 맞춤(fit)을 못 찾았다");
  const body = source.slice(start, source.indexOf("title.dataset.finalFs", start));
  assert.ok(/const asBlock = fit\(false\)/.test(body), "블록 배치를 안 잰다");
  assert.ok(/fit\(true\)/.test(body), "인라인 배치를 안 잰다");
  assert.ok(/asInline\.fs > asBlock\.fs/.test(body),
    "크기로 고르지 않는다 — 먼저 맞는 배치에서 멈추면 칸이 넓어질수록 제목이 작아진다");
});

check("제목 칸은 오버레이가 짙은 구간 안에 있다", () => {
  const width = source.match(/\.editorial-copy \{[^}]*width: (\d+)%/);
  assert.ok(width, "제목 칸 폭을 못 찾았다");
  // 가로 그라디언트가 74% 에서 α.28 까지 옅어진다. 칸은 좌우 패딩 76px 안쪽이라
  // 카드 기준 오른쪽 끝 = (76 + 928 * width) / 1080. 그 자리가 74% 를 넘으면
  // 밝은 사진에서 제목이 배경에 묻는다.
  const edge = (76 + 928 * (Number(width[1]) / 100)) / 1080;
  assert.ok(edge <= 0.74,
    `제목 칸이 카드의 ${(edge * 100).toFixed(1)}% 까지 간다 — 오버레이가 옅어지는 74% 안쪽이어야 한다`);
});

if (process.exitCode) {
  console.error("\n실패");
} else {
  console.log(`\n${passed}건 전부 통과`);
}
