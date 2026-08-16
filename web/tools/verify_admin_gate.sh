#!/usr/bin/env bash
# 배포된 사이트에서 운영 콘솔이 실제로 잠겨 있는지 확인한다.
#
# 왜 배포마다 확인하나
# --------------------
# 자물쇠는 Cloudflare Pages Function(functions/admin/_middleware.js)이다. 이 폴더는
# `wrangler pages deploy` 가 **실행 디렉터리 기준으로** 찾는다. 경로가 어긋나거나
# 폴더가 업로드에서 빠지면 배포는 조용히 성공하고, /admin 은 그냥 열린다. 그
# 실패는 화면에 아무 표시도 남기지 않아서 알아차릴 방법이 없다 — 그래서 매 배포
# 마다 살아 있는 사이트에 직접 물어본다.
#
# 통과 조건: /admin/ 과 /admin/data/merges.json 이 200 이 아닐 것.
#   401  정상 (비밀번호 필요)
#   403  로그인은 됐지만 아직 기본 비밀번호 — 콘솔은 잠겨 있다
#   503  KV 미연결 — 잠겨 있으므로 통과지만 경고를 남긴다
#   200  실패 (문이 열려 있다)
#
#   사용법: web/tools/verify_admin_gate.sh https://nuclens-v2.pages.dev

set -euo pipefail

site="${1:-${SITE_URL:-}}"
if [ -z "$site" ]; then
  echo "::error::확인할 사이트 주소가 없다 (인자 또는 SITE_URL)"
  exit 1
fi
site="${site%/}"

fail=0
for path in "/admin/" "/admin/data/merges.json"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "${site}${path}?cb=$(date +%s)")
  case "$code" in
    401|403)
      echo "잠김 확인 — ${path} → ${code}" ;;
    503)
      echo "::warning::${path} → 503 — 자물쇠는 살아 있으나 KV 네임스페이스(ADMIN_KV)가 아직 연결되지 않았다" ;;
    200)
      echo "::error::${path} 가 인증 없이 200 을 냈다 — 운영 콘솔이 공개돼 있다"
      fail=1 ;;
    404)
      # 데이터 파일이 아직 안 만들어졌을 수 있다. 하지만 /admin/ 자체가 404 면
      # 미들웨어가 없다는 뜻이므로(정상이면 401) 실패로 본다.
      if [ "$path" = "/admin/" ]; then
        echo "::error::${path} → 404 — 미들웨어가 배포되지 않은 것으로 보인다"
        fail=1
      else
        echo "::warning::${path} → 404 — 아직 빌드되지 않은 데이터일 수 있다"
      fi ;;
    *)
      echo "::error::${path} → $code — 예상하지 못한 응답"
      fail=1 ;;
  esac
done

exit "$fail"
