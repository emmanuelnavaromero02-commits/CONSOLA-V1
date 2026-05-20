-- MODecissionsPaaS — single-use VPN config download tokens.
-- Extends user_tokens with the WG-easy client id so /vpn-config/<token>
-- can fetch and serve the .conf, then burn the token.

ALTER TABLE user_tokens ADD COLUMN IF NOT EXISTS wg_client_id TEXT;
