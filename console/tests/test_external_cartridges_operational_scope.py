from app.domains.data_platform.source_visibility import OPERATIONAL_CARTRIDGES
from app.routers import operations


def test_external_market_cartridges_are_operational_for_sync_now():
    expected = {"banxico", "inegi", "sec_edgar"}
    assert expected <= OPERATIONAL_CARTRIDGES
    assert expected <= operations._OPERATIONAL_CARTRIDGES
