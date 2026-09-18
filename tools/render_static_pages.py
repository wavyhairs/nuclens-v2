"""Render /issue and /brief entry HTML from a production render manifest."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://nuclens-v2.pages.dev"
DEFAULT_DESCRIPTION = (
    "Nuclens는 원자력 정책·산업 뉴스를 이슈 단위로 연결하고 중요한 변화를 근거와 함께 추적합니다."
)
DEFAULT_TITLE = "Nuclens · 원자력 정책·산업 이슈 트래커"
DEFAULT_OG_DESCRIPTION = "원자력 이슈를 연결하고, 변화를 추적합니다."

REDIRECT_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<link rel="canonical" href="{target_url}">
<meta http-equiv="refresh" content="0; url={target_path}">
<title>{title} | Nuclens</title>
</head>
<body>
<p>이 이슈는 <a href="{target_path}">현재 주소</a>로 옮겨졌습니다.</p>
<script>location.replace({target_json});</script>
</body>
</html>
"""


def _replace_metadata(template: str, *, title: str, description: str,
                      url: str, schema_type: str, published: str,
                      modified: str = "") -> str:
    page = template
    replacements = {
        f'<meta name="description" content="{DEFAULT_DESCRIPTION}">':
            f'<meta name="description" content="{html_escape(description, quote=True)}">',
        '<meta property="og:type" content="website">':
            '<meta property="og:type" content="article">',
        f'<meta property="og:title" content="{DEFAULT_TITLE}">':
            f'<meta property="og:title" content="{html_escape(title, quote=True)} | Nuclens">',
        f'<meta property="og:description" content="{DEFAULT_OG_DESCRIPTION}">':
            f'<meta property="og:description" content="{html_escape(description, quote=True)}">',
        f'<meta property="og:url" content="{SITE_URL}/">':
            f'<meta property="og:url" content="{html_escape(url, quote=True)}">',
        f'<link rel="canonical" href="{SITE_URL}/">':
            f'<link rel="canonical" href="{html_escape(url, quote=True)}">',
        f'<title>{DEFAULT_TITLE}</title>': f'<title>{html_escape(title)} | Nuclens</title>',
    }
    for old, new in replacements.items():
        if old not in page:
            raise RuntimeError(f"static page metadata template is missing: {old}")
        page = page.replace(old, new, 1)
    structured = {
        "@context": "https://schema.org",
        "@type": schema_type,
        ("headline" if schema_type == "Article" else "name"): title,
        "description": description,
        "datePublished": published,
        "mainEntityOfPage": url,
        "publisher": {"@type": "Organization", "name": "Nuclens", "url": SITE_URL},
    }
    if modified:
        structured["dateModified"] = modified
    encoded = json.dumps(structured, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return page.replace(
        "</head>", f'  <script type="application/ld+json">{encoded}</script>\n</head>', 1
    )


def render(public_dir: Path) -> dict[str, int]:
    manifest_path = public_dir / "data" / "_pages.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "nuclens-render-pages-v1":
        raise ValueError("지원하지 않는 _pages.json schema")
    template = (public_dir / "index.html").read_text(encoding="utf-8")
    counts = {"issue": 0, "redirect": 0, "brief": 0}
    for dirname in ("issue", "brief"):
        target = (public_dir / dirname).resolve()
        if target.parent != public_dir.resolve() or target.name != dirname:
            raise RuntimeError(f"unsafe render target: {target}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)

    for row in manifest.get("issues") or []:
        issue_id = str(row.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", issue_id):
            raise ValueError(f"unsafe issue id in manifest: {issue_id!r}")
        page_dir = public_dir / "issue" / issue_id
        page_dir.mkdir()
        kind = row.get("kind")
        if kind == "moved":
            target = str(row.get("target") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
                raise ValueError(f"unsafe redirect target in manifest: {target!r}")
            target_path = f"/issue/{quote(target, safe='-_')}/"
            page = REDIRECT_TEMPLATE.format(
                target_url=html_escape(f"{SITE_URL}{target_path}", quote=True),
                target_path=html_escape(target_path, quote=True),
                target_json=json.dumps(target_path),
                title=html_escape(str(row.get("title") or "Nuclens 이슈")),
            )
            counts["redirect"] += 1
        else:
            url = f"{SITE_URL}/issue/{quote(issue_id, safe='-_')}"
            page = _replace_metadata(
                template,
                title=str(row.get("title") or "Nuclens 이슈"),
                description=str(row.get("description") or ""),
                url=url,
                schema_type="Article",
                published=str(row.get("published") or ""),
                modified=str(row.get("modified") or ""),
            )
            counts["issue"] += 1
        (page_dir / "index.html").write_text(page, encoding="utf-8")

    for row in manifest.get("briefs") or []:
        day = str(row.get("date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError(f"unsafe brief date in manifest: {day!r}")
        page = _replace_metadata(
            template,
            title=str(row.get("title") or f"{day} 원자력 브리프"),
            description=str(row.get("description") or ""),
            url=f"{SITE_URL}/brief/{day}",
            schema_type="Report",
            published=day,
        )
        page_dir = public_dir / "brief" / day
        page_dir.mkdir()
        (page_dir / "index.html").write_text(page, encoding="utf-8")
        counts["brief"] += 1
    print("[static-render] " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-dir", type=Path, default=ROOT / "web" / "public")
    args = parser.parse_args()
    render(args.public_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
