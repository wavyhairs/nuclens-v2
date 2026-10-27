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
#   302  정상 — 바깥 자물쇠 Cloudflare Access(이메일 인증)가 로그인으로 돌려보냈다
#   401  잠김 — 비밀번호 화면. 단 Access 가 빠져 비밀번호 한 겹뿐이라 경고를 남긴다
#   403  로그인은 됐지만 아직 기본 비밀번호 — 콘솔은 잠겨 있다
#   503  KV 미연결 — 잠겨 있으므로 통과지만 경고를 남긴다
#   200  실패 (문이 열려 있다)
#
# 302 는 **Access 로그인으로 가는 것만** 받는다. 다른 곳으로 보내는 302 는 미들웨어가
# 내는 일이 없는 응답이라(미인증이면 401 로 로그인 화면을 그린다) 예상 밖으로 본다.
#
# Access 가 없을 때(401) 실패가 아니라 경고인 이유: 바깥 자물쇠는 저장소가 아니라
# Cloudflare 대시보드 설정이라, 실패로 두면 그 설정 하나를 깜빡한 날 크롤 배포가
# 통째로 빨개진다. 대신 배포마다 경고가 남아 한 겹뿐이라는 사실이 묻히지 않는다.
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
  read -r code location < <(curl -s -o /dev/null -w "%{http_code} %{redirect_url}
"     --max-time 30 "${site}${path}?cb=$(date +%s)")
  case "$code" in
    302|303|307)
      case "$location" in
        https://*.cloudflareaccess.com/*|*/cdn-cgi/access/login*)
          echo "잠김 확인 — ${path} → ${code} (Cloudflare Access 이메일 인증)" ;;
        *)
          echo "::error::${path} → ${code} ${location:-(Location 없음)} — Access 가 아닌 곳으로 돌려보낸다"
          fail=1 ;;
      esac ;;
    401)
      echo "::warning::${path} → 401 — 비밀번호 한 겹뿐이다. Cloudflare Access(이메일 인증)가 /admin 앞에 없다 (README '운영 콘솔 접근')" ;;
    403)
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
