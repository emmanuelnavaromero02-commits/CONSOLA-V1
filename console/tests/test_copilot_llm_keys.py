from app.domains.copilot.llm_keys import llm_secret_keys


def test_llm_secret_keys_reads_flat_keys_and_secret_objects():
    keys = llm_secret_keys(
        {
            "keys": ["anthropic_api_key", "", None],
            "secrets": [{"key": "openai_api_key"}, {"key": ""}, "ignored"],
        }
    )

    assert keys == {"anthropic_api_key", "openai_api_key"}
