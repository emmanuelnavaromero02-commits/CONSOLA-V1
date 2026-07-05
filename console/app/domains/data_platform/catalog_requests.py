"""Request orchestration for the data catalog API."""

from __future__ import annotations

from typing import Any, Callable


async def catalog_get_payload(
    *,
    layer: str,
    cartridge: str,
    tags: str,
    datasets: str,
    user: dict[str, Any],
    scope_catalog_cartridge_arg: Callable[[dict[str, Any], str], Any],
    user_allowed_cartridges: Callable[[dict[str, Any]], Any],
    empty_catalog_payload: Callable[[], dict[str, Any]],
    catalog_query_args: Callable[..., dict[str, Any]],
    catalog_cache_key: Callable[[dict[str, Any]], str],
    refinement_invoke: Callable[..., Any],
    raise_for_refinement_payload_error: Callable[[Any, str], None],
    scoped_read_cache_get_or_set: Callable[..., Any],
) -> dict[str, Any]:
    scoped_cartridge = await scope_catalog_cartridge_arg(user, cartridge)
    if not scoped_cartridge and user_allowed_cartridges(user) is not None:
        return empty_catalog_payload()

    args = catalog_query_args(
        layer=layer,
        cartridge=scoped_cartridge,
        tags=tags,
        datasets=datasets,
    )
    cache_args = catalog_cache_key(args)

    async def load_catalog() -> Any:
        result = await refinement_invoke("get_data_catalog", args, user=user)
        raise_for_refinement_payload_error(result, "Refinement catalog failed")
        return result

    return await scoped_read_cache_get_or_set(
        "catalog", user, (cache_args,), load_catalog
    )


async def catalog_upsert_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    refinement_invoke: Callable[..., Any],
    raise_for_refinement_payload_error: Callable[[Any, str], None],
    scoped_read_cache_invalidate: Callable[[str, dict[str, Any]], None],
) -> Any:
    result = await refinement_invoke("upsert_catalog_entries", body, user=user)
    raise_for_refinement_payload_error(result, "Refinement catalog update failed")
    scoped_read_cache_invalidate("catalog", user)
    return result


async def catalog_relationship_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    refinement_invoke: Callable[..., Any],
    raise_for_refinement_payload_error: Callable[[Any, str], None],
    scoped_read_cache_invalidate: Callable[[str, dict[str, Any]], None],
) -> Any:
    result = await refinement_invoke("register_relationship", body, user=user)
    raise_for_refinement_payload_error(result, "Refinement relationship update failed")
    scoped_read_cache_invalidate("catalog", user)
    return result
