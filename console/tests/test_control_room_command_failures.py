from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service


USER = {
    "id": 7,
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


@pytest.mark.asyncio
async def test_mutation_lookup_propagates_database_failures():
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(side_effect=RuntimeError("database unavailable")),
    ):
        with pytest.raises(RuntimeError, match="database unavailable"):
            await control_room_service._persisted_item_for_mutation(
                "business-1",
                USER,
            )
