from __future__ import annotations

from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "console/app/static/js/studio/action-bridge.js").read_text(encoding="utf-8")


def test_dag_graph_svg_is_parsed_and_sanitized_instead_of_injected_as_html():
    render = SOURCE.split("function renderDagGraph(data) {", 1)[1].split("\n}\n", 1)[0]
    assert "innerHTML" not in render and "${data.svg}" not in SOURCE
    assert "sanitizedSvg(data.svg)" in render
    sanitizer = SOURCE.split("function sanitizedSvg(markup) {", 1)[1].split("\n}\n", 1)[0]
    for token in ('"image/svg+xml"', "script, foreignObject", 'name.startsWith("on")', '"xlink:href"'):
        assert token in sanitizer
