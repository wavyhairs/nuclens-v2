// 운영 콘솔의 쓰기 창구 — 사람이 내린 판정을 Cloudflare KV 에 적는다.
//
// 왜 여기서 저장소를 안 건드리나
// ------------------------------
// 이 함수는 엣지에서 돌고, 저장소에 쓰려면 GitHub 쓰기 토큰을 엣지에 두어야 한다.
// 콘솔 비밀번호 하나가 저장소 쓰기 권한으로 번지는 구조라 그렇게 하지 않는다.
// 대신 판정을 KV 에 쌓고, 워크플로가 시작할 때 `tools/sync_admin_overrides.py` 가
// 그것을 끌어와 `admin_overrides.json` 으로 커밋한다. KV 는 버퍼고 git 이 DB다.
//
// 그래서 여기 쓴 판정은 **다음 수집부터** 듣는다. 화면은 그 사실을 숨기지 않고
// "아직 반영 안 됨"으로 표시한다 — 눌렀는데 아무 일도 안 일어나는 것처럼 보이면
// 관리자는 같은 판정을 몇 번씩 다시 누른다.
//
// 접근 통제는 `functions/admin/_middleware.js` 가 이미 했다. 이 파일에 도달했다는
// 것은 서명된 세션 쿠키가 있다는 뜻이다. 여기서 더 볼 것은 **교차 사이트 위조**뿐이다.

const KV_KEY = "admin:overrides";
const MAX_ENTRIES = 400;
const MAX_BODY_BYTES = 64 * 1024;

// admin_overrides.py 의 KINDS 와 같은 목록. 둘이 갈라지면 콘솔은 저장에 성공했다고
// 말하는데 파이프라인은 그 항목을 조용히 무시한다 — 제일 나쁜 실패 방식이다.
const KINDS = new Set([
  "story_split", "issue_split", "issue_group_split", "issue_join", "learned_rule",
  "keyword_add", "keyword_remove", "anchor_add", "anchor_remove",
  "negative_add", "negative_remove", "anti_add", "anti_remove",
  "feed_add", "feed_disable", "official_disable",
  "tier_upsert", "tier_remove",
  "learned_term_add", "learned_term_remove", "learned_term_keep",
]);

const TIERS = new Set([1, 2, 3]);
const SOURCE_TYPES = new Set([
  "official", "specialist_media", "general_media", "press_release", "unknown",
]);
const EVIDENCE_ROLES = new Set([
  "primary", "independent", "distributed_claim", "unknown",
]);

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
}

function text(value, limit) {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function textList(value, limit, itemLimit) {
  if (!Array.isArray(value)) return [];
  const out = [];
  for (const item of value.slice(0, limit)) {
    const cleaned = text(item, itemLimit);
    if (cleaned && !out.includes(cleaned)) out.push(cleaned);
  }
  return out;
}

async function readDoc(kv) {
  let raw = null;
  try {
    raw = await kv.get(KV_KEY, "json");
  } catch {
    raw = null;
  }
  const entries = Array.isArray(raw?.entries)
    ? raw.entries.filter(entry => entry && typeof entry === "object" && entry.id)
    : [];
  return {
    version: 1,
    rev: Number.isInteger(raw?.rev) ? raw.rev : 0,
    updated_at: typeof raw?.updated_at === "string" ? raw.updated_at : "",
    entries,
  };
}

// ── 항목 검증 ──────────────────────────────────────────────────────────────
//
// 화면을 믿지 않는다. 저장은 되는데 파이프라인이 못 읽는 항목(축이 빈 학습 규칙,
// http 가 아닌 피드 URL)이 KV 에 남으면 관리자는 자기가 무엇을 고쳤는지 모르는
// 채로 다음 수집을 기다린다. 여기서 이유를 붙여 되돌려 준다.

export function normalizeEntry(input) {
  const kind = text(input?.kind, 40);
  if (!KINDS.has(kind)) return { error: `알 수 없는 판정 종류입니다: ${kind || "(없음)"}` };

  const entry = {
    kind,
    note: text(input?.note, 300),
    created_at: new Date().toISOString(),
  };

  if (kind === "story_split" || kind === "issue_split" || kind === "issue_join") {
    entry.left_hash = text(input?.left_hash, 64);
    entry.right_hash = text(input?.right_hash, 64);
    if (!entry.left_hash || !entry.right_hash) return { error: "기사 두 건을 지정하세요." };
    if (entry.left_hash === entry.right_hash) return { error: "같은 기사끼리는 갈라 놓을 수 없습니다." };
    entry.left_title = text(input?.left_title, 180);
    entry.right_title = text(input?.right_title, 180);
    entry.issue_id = text(input?.issue_id, 80);
    return { entry };
  }

  // 사건군 나누기. 쌍이 아니라 **선**이다 — 파이프라인이 선을 가로지르는 쌍
  // 전부로 펼친다(admin_overrides.group_splits). 한쪽이 비면 가를 것이 없고,
  // 같은 기사가 양쪽에 서면 그 기사는 자기 자신과 다른 사건이 된다.
  if (kind === "issue_group_split") {
    entry.left_hashes = textList(input?.left_hashes, 60, 64);
    entry.right_hashes = textList(input?.right_hashes, 60, 64);
    if (!entry.left_hashes.length || !entry.right_hashes.length) {
      return { error: "양쪽 모두 기사가 한 건 이상 필요합니다 — 한쪽이 비면 나눌 것이 없습니다." };
    }
    const both = entry.left_hashes.filter(hash => entry.right_hashes.includes(hash));
    if (both.length) {
      return { error: "같은 기사가 양쪽에 있습니다 — 한 기사는 한쪽에만 설 수 있습니다." };
    }
    // 제목은 화면 표시용이다. 없어도 판정은 성립하지만, 없으면 '내 판정' 목록이
    // 16진수 두 줄이 되고 그건 되짚을 수 없는 기록이다.
    entry.left_titles = textList(input?.left_titles, 60, 180);
    entry.right_titles = textList(input?.right_titles, 60, 180);
    entry.issue_id = text(input?.issue_id, 80);
    return { entry };
  }

  if (kind === "learned_rule") {
    entry.left_terms = textList(input?.left_terms, 20, 60);
    entry.right_terms = textList(input?.right_terms, 20, 60);
    if (!entry.left_terms.length || !entry.right_terms.length) {
      return { error: "판별축은 양쪽 모두 최소 한 낱말이 필요합니다 — 한쪽만 있으면 아무것도 가르지 못합니다." };
    }
    // 양쪽에 같은 말이 있으면 그 말로는 절대 갈리지 않는다(rule_conflict 는 겹치면
    // 침묵한다). 저장은 되지만 영원히 안 듣는 규칙이라 여기서 막는다.
    const overlap = entry.left_terms.filter(term =>
      entry.right_terms.some(other => other.toLowerCase() === term.toLowerCase()));
    if (overlap.length) {
      return { error: `양쪽에 같은 낱말이 있습니다(${overlap[0]}) — 겹치는 축으로는 사건을 가를 수 없습니다.` };
    }
    entry.label = text(input?.label, 120)
      || `${entry.left_terms[0]} ↔ ${entry.right_terms[0]}`;
    entry.axis = text(input?.axis, 40) || "custom";
    entry.origin_pair = textList(input?.origin_pair, 2, 64);
    return { entry };
  }

  if (kind.startsWith("keyword_") || kind.startsWith("anchor_") || kind.startsWith("negative_")) {
    entry.group = text(input?.group, 80);
    entry.value = text(input?.value, 200);
    if (!entry.group) return { error: "키워드 그룹을 지정하세요." };
    if (!entry.value) return { error: "값이 비어 있습니다." };
    return { entry };
  }

  // 학습된 검색어. 그룹이 없다 — 고정 키워드와 다른 층이고, 앵커·제외어를
  // 함께 갖는 '한 벌'이 아니라 24~72시간짜리 임시 검색어이기 때문이다.
  if (kind.startsWith("learned_term_")) {
    entry.value = text(input?.value, 60);
    if (!entry.value) return { error: "검색어가 비어 있습니다." };
    // 한 글자짜리 검색어는 네이버에서 사실상 전체 검색이 된다. 임시 검색어는
    // 예산을 나눠 쓰므로 그런 질의 하나가 그날 몫을 통째로 태운다.
    if (entry.value.replace(/\s+/g, "").length < 2) {
      return { error: "검색어는 두 글자 이상이어야 합니다." };
    }
    if (kind === "learned_term_add") {
      entry.query = text(input?.query, 80);
      entry.type = text(input?.type, 20);
    }
    return { entry };
  }

  if (kind === "anti_add" || kind === "anti_remove") {
    entry.value = text(input?.value, 120);
    if (!entry.value) return { error: "값이 비어 있습니다." };
    return { entry };
  }

  if (kind === "feed_add") {
    entry.url = text(input?.url, 400);
    let parsed;
    try {
      parsed = new URL(entry.url);
    } catch {
      return { error: "수집원 주소가 URL 이 아닙니다." };
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return { error: "수집원 주소는 http/https 만 됩니다." };
    }
    entry.name = text(input?.name, 120) || parsed.hostname;
    entry.domain_label = text(input?.domain_label, 120)
      || parsed.hostname.replace(/^www\./, "");
    entry.require_keywords = textList(input?.require_keywords, 24, 40);
    entry.resolve_publisher = Boolean(input?.resolve_publisher);
    return { entry };
  }

  if (kind === "feed_disable" || kind === "official_disable") {
    entry.target = text(input?.target, 400);
    entry.label = text(input?.label, 120);
    if (!entry.target) return { error: "중지할 수집원을 지정하세요." };
    return { entry };
  }

  if (kind === "tier_upsert" || kind === "tier_remove") {
    entry.domain = text(input?.domain, 200).toLowerCase().replace(/^www\./, "");
    if (!entry.domain || !entry.domain.includes(".")) {
      return { error: "도메인을 입력하세요(예: world-nuclear-news.org)." };
    }
    if (kind === "tier_remove") return { entry };
    const tier = Number(input?.tier);
    if (!TIERS.has(tier)) return { error: "등급은 1·2·3 중 하나입니다." };
    entry.tier = tier;
    entry.name = text(input?.name, 120) || entry.domain;
    entry.source_type = text(input?.source_type, 40);
    entry.evidence_role = text(input?.evidence_role, 40);
    if (entry.source_type && !SOURCE_TYPES.has(entry.source_type)) {
      return { error: `매체 성격 값이 올바르지 않습니다: ${entry.source_type}` };
    }
    if (entry.evidence_role && !EVIDENCE_ROLES.has(entry.evidence_role)) {
      return { error: `근거 역할 값이 올바르지 않습니다: ${entry.evidence_role}` };
    }
    entry.aliases = textList(input?.aliases, 24, 80);
    return { entry };
  }

  return { error: `처리할 수 없는 판정입니다: ${kind}` };
}

// ── 요청 처리 ──────────────────────────────────────────────────────────────

export async function onRequest(context) {
  const { request, env } = context;
  const kv = env.ADMIN_KV;
  if (!kv || typeof kv.get !== "function") {
    return json({ error: "KV 가 연결되지 않아 판정을 저장할 수 없습니다." }, 503);
  }

  if (request.method === "GET") {
    return json(await readDoc(kv));
  }
  if (request.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405);
  }

  // 교차 사이트 위조 방어. 세션 쿠키는 SameSite=Lax 라 다른 사이트의 폼 POST 에는
  // 실리지 않지만, 그 방어는 브라우저 구현에 기대는 것이라 서버에서도 확인한다.
  const origin = request.headers.get("Origin") || "";
  if (origin && origin !== new URL(request.url).origin) {
    return json({ error: "요청 출처가 올바르지 않습니다." }, 403);
  }
  if (!(request.headers.get("Content-Type") || "").includes("application/json")) {
    return json({ error: "JSON 요청만 받습니다." }, 415);
  }

  let body;
  try {
    const raw = await request.text();
    if (raw.length > MAX_BODY_BYTES) return json({ error: "요청이 너무 큽니다." }, 413);
    body = JSON.parse(raw);
  } catch {
    return json({ error: "요청을 읽지 못했습니다." }, 400);
  }

  const doc = await readDoc(kv);
  // 낙관적 동시성. KV 에는 CAS 가 없어서, 두 창을 열어 두면 나중 저장이 앞 저장을
  // 통째로 덮는다. 화면이 마지막으로 본 rev 를 같이 보내고 어긋나면 되돌린다.
  if (Number.isInteger(body?.rev) && body.rev !== doc.rev) {
    return json({ error: "다른 곳에서 먼저 저장했습니다 — 새로고침한 뒤 다시 하세요.", rev: doc.rev }, 409);
  }

  const op = text(body?.op, 20);
  let entries = doc.entries;

  if (op === "add") {
    if (entries.length >= MAX_ENTRIES) {
      return json({ error: `판정은 최대 ${MAX_ENTRIES}건까지입니다 — 오래된 항목을 지우세요.` }, 409);
    }
    const { entry, error } = normalizeEntry(body?.entry);
    if (error) return json({ error }, 400);
    entry.id = `${entry.kind.split("_")[0]}-${crypto.randomUUID().slice(0, 12)}`;
    // 같은 판정을 두 번 누르면 두 줄이 생기고, 지울 때 하나만 지워 절반이 남는다.
    const duplicate = entries.find(row => sameJudgment(row, entry));
    if (duplicate) return json({ error: "같은 판정이 이미 있습니다.", id: duplicate.id }, 409);
    entries = [...entries, entry];
  } else if (op === "delete") {
    const id = text(body?.id, 64);
    if (!entries.some(row => row.id === id)) return json({ error: "없는 항목입니다." }, 404);
    entries = entries.filter(row => row.id !== id);
  } else if (op === "toggle") {
    const id = text(body?.id, 64);
    if (!entries.some(row => row.id === id)) return json({ error: "없는 항목입니다." }, 404);
    const enabled = body?.enabled !== false;
    entries = entries.map(row => (row.id === id ? { ...row, enabled } : row));
  } else {
    return json({ error: `알 수 없는 동작입니다: ${op}` }, 400);
  }

  const next = {
    version: 1,
    rev: doc.rev + 1,
    updated_at: new Date().toISOString(),
    entries,
  };
  try {
    await kv.put(KV_KEY, JSON.stringify(next));
  } catch (error) {
    return json({ error: `저장하지 못했습니다: ${String(error).slice(0, 120)}` }, 502);
  }
  return json(next);
}

export function sameJudgment(left, right) {
  if (left.kind !== right.kind) return false;
  // 사건군 나누기는 목록이라 필드 비교로는 안 잡힌다. 순서만 다른 같은 선을
  // 두 번 저장하면 쌍이 두 벌로 늘고, 지울 때 하나만 지워 절반이 남는다.
  if (left.kind === "issue_group_split") {
    const side = (row, key) => [...(row[key] || [])].map(String).sort().join("|");
    return ["left_hashes", "right_hashes"].every(key => side(left, key) === side(right, key))
      || (side(left, "left_hashes") === side(right, "right_hashes")
        && side(left, "right_hashes") === side(right, "left_hashes"));
  }
  const fields = {
    story_split: ["left_hash", "right_hash"],
    issue_split: ["left_hash", "right_hash"],
    issue_join: ["left_hash", "right_hash"],
    keyword_add: ["group", "value"], keyword_remove: ["group", "value"],
    anchor_add: ["group", "value"], anchor_remove: ["group", "value"],
    negative_add: ["group", "value"], negative_remove: ["group", "value"],
    anti_add: ["value"], anti_remove: ["value"],
    learned_term_add: ["value"], learned_term_remove: ["value"],
    learned_term_keep: ["value"],
    feed_add: ["url"], feed_disable: ["target"], official_disable: ["target"],
    tier_upsert: ["domain"], tier_remove: ["domain"],
    learned_rule: ["label"],
  }[left.kind] || [];
  if (!fields.length) return false;
  return fields.every(field => String(left[field] ?? "") === String(right[field] ?? ""));
}
