-- Cartridge-specific instructions surfaced to the studio / workspace
-- assistant whenever that cartridge is the active context.
-- Lives on the cartridges row itself so it travels with the cartridge
-- through export / import.

ALTER TABLE cartridges
    ADD COLUMN IF NOT EXISTS assistant_hints TEXT;
