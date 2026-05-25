from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "app" / "static"


def test_control_room_is_visible_in_home_navigation():
    navigation = (STATIC_ROOT / "js" / "home" / "navigation.js").read_text(encoding="utf-8")

    assert "Sala de Control" in navigation
    assert "href: '/control-room'" in navigation
    assert "workspace.access" in navigation
    assert "omega control room" in navigation


def test_control_room_is_visible_as_operational_card():
    render = (STATIC_ROOT / "js" / "home" / "render.js").read_text(encoding="utf-8")

    assert "title: 'Sala de Control'" in render
    assert "href: '/control-room'" in render
    assert "Dashboard operativo para anomalías" in render
    assert "Anomalías" in render
    assert "Auditoría" in render


def test_control_room_is_available_in_static_fallback():
    fallback = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert '<a href="/control-room">Sala de Control</a>' in fallback
