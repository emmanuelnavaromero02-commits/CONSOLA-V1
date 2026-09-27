CREATE OR REPLACE FUNCTION protect_council_decisions()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $protect$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.control_room_items AS item
         WHERE item.workspace_id = OLD.workspace_id
           AND item.decision_id = OLD.id
    ) OR EXISTS (
        SELECT 1
          FROM public.decision_actions AS action
         WHERE action.decision_id = OLD.id
           AND action.action_text LIKE ANY (ARRAY[
               'Aprobacion en el Consejo de Acciones: %',
               'Aprobación en el Consejo de Acciones: %',
               'Seguimiento operativo Control Room: %',
               'Propuesta descartada en el Consejo: %'
           ])
    ) THEN
        RAISE EXCEPTION 'decision is linked to the Control Room or the action council'
            USING ERRCODE = '23503',
                  CONSTRAINT = 'decisions_council_delete_protection';
    END IF;
    RETURN OLD;
END;
$protect$;

REVOKE ALL ON FUNCTION protect_council_decisions() FROM PUBLIC;
DROP TRIGGER IF EXISTS trg_decisions_council_delete_protection ON decisions;
CREATE TRIGGER trg_decisions_council_delete_protection
BEFORE DELETE ON decisions
FOR EACH ROW EXECUTE FUNCTION protect_council_decisions();

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzx_decision_council_delete_protection.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
