"""Headless smoke test for the engine GUI (not a pytest; run directly).

The gui is an auto-refreshing @st.fragment, so each AppTest .run()
advances one fragment pass; we pump a few passes to finish a burn.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

_GUI = Path(__file__).resolve().parents[1] / "simulator" / "engine" / "gui.py"


def main() -> None:
    at = AppTest.from_file(str(_GUI), default_timeout=60)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    print("load OK")

    at.number_input[6].set_value(1.0)  # 1 s burn -> one pass at 10x speed
    at.button[0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    print("run OK")

    at2 = AppTest.from_file(str(_GUI), default_timeout=120)
    at2.run()
    at2.number_input[5].set_value(2000.0)  # 2 t load -> must starve
    at2.button[0].click()
    for _ in range(80):                    # pump fragment passes until starved
        at2.run(timeout=60)
        if _starved_text(at2):
            break
    assert not at2.exception, [str(e.value) for e in at2.exception]
    hits = _starved_text(at2)
    print("starve OK:", hits)
    assert hits, "expected a starvation/burn-complete notice"


def _starved_text(at: AppTest) -> list[str]:
    texts = [m.value or "" for m in at.main.get("markdown")]
    texts += [c.value or "" for c in at.main.get("caption")]
    return [t[:100] for t in texts if "starved" in t.lower()]


if __name__ == "__main__":
    main()
