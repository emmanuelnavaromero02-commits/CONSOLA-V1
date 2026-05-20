-- Some databases created semantic catalog sequences before the refinement
-- role grant was present. Keep startup seeding of data_relationships working.
GRANT USAGE, SELECT ON SEQUENCE data_catalog_id_seq TO omega_refinement;
GRANT USAGE, SELECT ON SEQUENCE data_relationships_id_seq TO omega_refinement;
