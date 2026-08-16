-- 99zzzzb_control_room_lessons_honest_confidence.sql
--
-- F11 (hechos confiables): control_room_lessons.confidence carried
-- NOT NULL DEFAULT 0.70 — the DEFAULT itself was the fabricated value the
-- writer papered over missing data with (`.get("confidence") or 0.7`).
-- A confidence nobody computed must persist as NULL and render as
-- "sin dato", never as an invented 0.70.
--
-- Forward-only: the original 91_control_room_v1_operational.sql is
-- checksum-recorded and stays untouched. Existing rows are NOT rewritten —
-- a stored 0.70 cannot be distinguished from a legitimately computed 0.70
-- after the fact, so history keeps its values and only new writes become
-- honest.
--
-- (Naming debt, again: prefix 99zzzzb to sort after 99zzzza; infra/init
-- needs a real ordering sequence.)

ALTER TABLE control_room_lessons
    ALTER COLUMN confidence DROP NOT NULL;

ALTER TABLE control_room_lessons
    ALTER COLUMN confidence DROP DEFAULT;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzb_control_room_lessons_honest_confidence.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
