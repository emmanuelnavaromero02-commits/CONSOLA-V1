from app.domains.viewer.navigation import viewer_redirect_url


def test_viewer_redirect_url_sets_type_and_preserves_query_params():
    url = viewer_redirect_url(
        {"cartridge": "sap_successfactors", "type": "jobs"},
        "pipeline",
        {"scope": "tenant", "empty": ""},
    )

    assert url == "/viewer?cartridge=sap_successfactors&type=pipeline&scope=tenant"


def test_viewer_redirect_url_encodes_values():
    url = viewer_redirect_url({}, "schema", {"source": "raw/sap successfactors/User"})

    assert url == "/viewer?type=schema&source=raw%2Fsap+successfactors%2FUser"
