from __future__ import annotations

from dataclasses import dataclass

from app.services.control_room.business_copy_unicode import security_detection_forms
from app.services.control_room.diagnostic_escape_detection import (
    escaped_security_detection,
)
from app.services.control_room.diagnostic_path_form_detection import (
    path_form_detection,
)


_MAX_INPUT_LENGTH = 16_384
_MAX_FORMS = 32
_MAX_CANDIDATES = 64
_MAX_RECOMPOSED_COMPONENTS = 32
_MAX_METADATA_ROUNDS = 8
_ESCAPE_WIDTHS = {"u": 4, "U": 8, "x": 2}
_METADATA_SUFFIXES = (
    ":$data",
    "-backup",
    "_value",
    ".json",
    ".txt",
    ".env",
)
_REVERSE_SLASH_CONFUSABLES = str.maketrans(
    {
        "∖": "\\",
        "⧵": "\\",
        "╲": "\\",
    }
)
_PATH_SEPARATORS = frozenset(("/", "\\"))
_DOT_SEGMENTS = frozenset((".", ".."))


@dataclass(frozen=True)
class PathKeyDetection:
    terminals: tuple[str, ...] = ()
    path_like: bool = False
    unsafe: bool = False


def _path_form(value: str) -> str:
    return value.translate(_REVERSE_SLASH_CONFUSABLES)


def _path_components(value: str) -> tuple[str, ...] | None:
    candidate = _path_form(value).strip()
    drive_path = (
        len(candidate) >= 3
        and candidate[0].isascii()
        and candidate[0].isalpha()
        and candidate[1] == ":"
        and candidate[2] in _PATH_SEPARATORS
    )
    unc_path = (
        len(candidate) >= 3
        and candidate[0] in _PATH_SEPARATORS
        and candidate[1] in _PATH_SEPARATORS
    )
    if not drive_path and not unc_path:
        return None
    start = 2 if drive_path else 0
    components: list[str] = []
    component: list[str] = []
    for character in candidate[start:]:
        if character in _PATH_SEPARATORS:
            if component:
                components.append("".join(component))
                component = []
        else:
            component.append(character)
    if component:
        components.append("".join(component))
    return tuple(components)


def _effective_components(components: tuple[str, ...]) -> tuple[str, ...]:
    end = len(components)
    while end and components[end - 1] in _DOT_SEGMENTS:
        end -= 1
    return components[:end]


def _drop_lengths(component: str) -> tuple[int, ...]:
    lengths = [1] if component else []
    if component and component[0] in _ESCAPE_WIDTHS:
        encoded = 1 + _ESCAPE_WIDTHS[component[0]]
        if len(component) >= encoded:
            lengths.append(encoded)
    return tuple(dict.fromkeys(lengths))


def _component_candidates(components: tuple[str, ...]) -> tuple[str, ...]:
    effective = _effective_components(components)
    if not effective:
        return ()
    candidates = [effective[-1]]
    first = max(0, len(effective) - _MAX_RECOMPOSED_COMPONENTS)
    for index in range(first, len(effective) - 2):
        if len(effective[index + 1]) == 1:
            candidate = f"{effective[index]}{effective[index + 2]}"
            if candidate not in candidates:
                candidates.append(candidate)
    for start in range(first, len(effective) - 1):
        frontier = [effective[start]]
        for component in effective[start + 1 :]:
            frontier = [
                f"{prefix}{component[drop:]}"
                for prefix in frontier
                for drop in _drop_lengths(component)
            ][:_MAX_FORMS]
            if not frontier:
                break
        for candidate in frontier:
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return tuple(candidates)


def _decoded_candidates(value: str) -> tuple[str, ...]:
    components = _path_components(value)
    if components is not None:
        return _component_candidates(components)
    candidate = _path_form(value).strip()
    if (
        len(candidate) > 2
        and candidate[0].isascii()
        and candidate[0].isalpha()
        and candidate[1] == ":"
    ):
        candidate = candidate[2:].lstrip("/\\")
    if not candidate or any(mark in candidate for mark in "{}[];,="):
        return ()
    return (candidate,)


def _append(values: list[str], candidate: str, *, limit: int) -> bool:
    if candidate and candidate not in values:
        values.append(candidate)
    return len(values) <= limit


def _metadata_base(value: str) -> str | None:
    lowered = value.casefold()
    suffix = next(
        (
            item
            for item in _METADATA_SUFFIXES
            if lowered.endswith(item) and len(value) > len(item)
        ),
        None,
    )
    if suffix is not None:
        return value[: -len(suffix)]
    if "." in value:
        base, extension = value.rsplit(".", 1)
        if base and extension:
            return base
    if ":" in value:
        base, annotation = value.split(":", 1)
        if base and annotation:
            return base
    return None


def _semantic_candidates(values: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    candidates: list[str] = []
    for value in values:
        for form in security_detection_forms(value):
            if not _append(candidates, form, limit=_MAX_CANDIDATES):
                return (), True
            current = form
            for _round in range(_MAX_METADATA_ROUNDS):
                base = _metadata_base(current)
                if base is None:
                    break
                current = base
                if not _append(candidates, current, limit=_MAX_CANDIDATES):
                    return (), True
            else:
                if _metadata_base(current) is not None:
                    return (), True
    return tuple(candidates), False


def path_key_detection(value: str) -> PathKeyDetection:
    """Expose bounded semantic terminals only when a mapping key is a path."""

    if len(value) > _MAX_INPUT_LENGTH:
        return PathKeyDetection(unsafe=True)
    initial_forms = list(security_detection_forms(_path_form(value)))
    forms = list(initial_forms)
    decoded_forms: list[str] = []
    path_like = any(_path_components(form) is not None for form in initial_forms)
    unsafe = False
    for candidate in tuple(initial_forms):
        detection = path_form_detection(candidate)
        unsafe = unsafe or detection.unsafe
        for decoded in detection.forms:
            for form in security_detection_forms(_path_form(decoded)):
                if not _append(forms, form, limit=_MAX_FORMS):
                    return PathKeyDetection(path_like=path_like, unsafe=path_like)
                _append(decoded_forms, form, limit=_MAX_FORMS)

    terminals: list[str] = []
    for candidate in forms:
        components = _path_components(candidate)
        if components is not None:
            path_like = True
            if len(components) > _MAX_RECOMPOSED_COMPONENTS:
                return PathKeyDetection(path_like=True, unsafe=True)
            for terminal in _component_candidates(components):
                if not _append(terminals, terminal, limit=_MAX_CANDIDATES):
                    return PathKeyDetection(path_like=True, unsafe=True)
    if path_like:
        for decoded in decoded_forms:
            for terminal in _decoded_candidates(decoded):
                if not _append(terminals, terminal, limit=_MAX_CANDIDATES):
                    return PathKeyDetection(path_like=True, unsafe=True)

    for terminal in tuple(terminals):
        detection = escaped_security_detection(f"\\{terminal}")
        if detection.unsafe:
            return PathKeyDetection(path_like=path_like, unsafe=path_like)
        for decoded in detection.forms:
            for form in security_detection_forms(decoded):
                if not _append(terminals, form, limit=_MAX_CANDIDATES):
                    return PathKeyDetection(path_like=True, unsafe=True)

    semantic, excessive = _semantic_candidates(tuple(terminals))
    return PathKeyDetection(
        terminals=semantic,
        path_like=path_like,
        unsafe=path_like and (unsafe or excessive),
    )


__all__ = ("PathKeyDetection", "path_key_detection")
