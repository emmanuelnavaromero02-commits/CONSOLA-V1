-- v1.45.80: Agent monitor advisory alerts for Control Room.
--
-- Monitor agents write scoped advisory alerts into the existing Control Room
-- tables. They do not execute actions, approve decisions, or write back to
-- external systems.

CREATE INDEX IF NOT EXISTS control_room_items_agent_alerts_idx
    ON control_room_items(workspace_id, last_seen_at DESC)
    WHERE item_kind = 'agent_alert';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
        GRANT SELECT, INSERT, UPDATE ON control_room_items TO omega_mcp_infra;
        GRANT SELECT, INSERT ON control_room_item_events TO omega_mcp_infra;
        IF to_regclass('public.control_room_item_events_id_seq') IS NOT NULL THEN
            GRANT USAGE, SELECT ON SEQUENCE control_room_item_events_id_seq TO omega_mcp_infra;
        END IF;
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99o_agent_monitor_control_room_alerts.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
