from pathlib import Path


STYLE_PATH = Path(__file__).resolve().parents[1] / "public" / "style.css"


def test_change_log_after_uses_literal_arrow():
    css = STYLE_PATH.read_text(encoding="utf-8")
    line = next(
        line for line in css.splitlines()
        if line.startswith(".change-log-after::before")
    )

    assert 'content: "→";' in line
    assert "\x11" not in line
    assert 'content: "92";' not in line
