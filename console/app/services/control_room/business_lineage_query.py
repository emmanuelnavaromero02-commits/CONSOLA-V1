from __future__ import annotations


def lineage_reference_lateral() -> str:
    return """
        SELECT direct.parent_id
          FROM (VALUES
              (lineage.metadata->>'parent_item_id'),
              (lineage.metadata->>'source_item_id'),
              (CASE
                  WHEN jsonb_typeof(lineage.metadata->'derived_from') = 'string'
                  THEN lineage.metadata->>'derived_from'
               END),
              (lineage.metadata->'derived_from'->>'item_id'),
              (lineage.metadata->'derived_from'->>'id'),
              (lineage.metadata->'lineage'->>'parent_item_id'),
              (lineage.metadata->'lineage'->>'source_item_id')
          ) direct(parent_id)
        UNION ALL
        SELECT CASE jsonb_typeof(entry.value)
                   WHEN 'string' THEN entry.value #>> '{}'
                   WHEN 'object' THEN COALESCE(
                       entry.value->>'item_id', entry.value->>'id'
                   )
               END
          FROM jsonb_array_elements(
              CASE
                  WHEN jsonb_typeof(lineage.metadata->'derived_from') = 'array'
                  THEN lineage.metadata->'derived_from'
                  ELSE '[]'::jsonb
              END
          ) entry(value)
        UNION ALL
        SELECT claim_ref.parent_id
          FROM jsonb_array_elements(
              CASE
                  WHEN jsonb_typeof(
                      lineage.metadata->'business_observation'->'claims'
                  ) = 'array'
                  THEN lineage.metadata->'business_observation'->'claims'
                  ELSE '[]'::jsonb
              END
          ) claim(value)
          CROSS JOIN LATERAL (
              SELECT direct.parent_id
                FROM (VALUES
                    (claim.value->>'parent_item_id'),
                    (claim.value->>'source_item_id'),
                    (CASE
                        WHEN jsonb_typeof(claim.value->'derived_from') = 'string'
                        THEN claim.value->>'derived_from'
                     END),
                    (claim.value->'derived_from'->>'item_id'),
                    (claim.value->'derived_from'->>'id'),
                    (claim.value->'lineage'->>'parent_item_id'),
                    (claim.value->'lineage'->>'source_item_id')
                ) direct(parent_id)
              UNION ALL
              SELECT CASE jsonb_typeof(derived.value)
                         WHEN 'string' THEN derived.value #>> '{}'
                         WHEN 'object' THEN COALESCE(
                             derived.value->>'item_id', derived.value->>'id'
                         )
                     END
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(claim.value->'derived_from') = 'array'
                        THEN claim.value->'derived_from'
                        ELSE '[]'::jsonb
                    END
                ) derived(value)
          ) claim_ref(parent_id)
    """


__all__ = ("lineage_reference_lateral",)
