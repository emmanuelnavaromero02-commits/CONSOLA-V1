"""PostgreSQL PROCEDURAL LANGUAGE statements and business-copy controls."""

from __future__ import annotations


PROCEDURAL_LANGUAGE_STATEMENTS = (
    "DROP PROCEDURAL LANGUAGE Comercio",
    "DROP PROCEDURAL LANGUAGE IF EXISTS Comercio",
    "DROP PROCEDURAL LANGUAGE Comercio CASCADE",
    "DROP PROCEDURAL LANGUAGE Comercio RESTRICT",
    "ALTER PROCEDURAL LANGUAGE Comercio RENAME TO Comercio2",
    "ALTER PROCEDURAL LANGUAGE Comercio OWNER TO analyst",
    "ALTER PROCEDURAL LANGUAGE Comercio OWNER TO CURRENT_ROLE",
    "ALTER PROCEDURAL LANGUAGE Comercio OWNER TO CURRENT_USER",
    "ALTER PROCEDURAL LANGUAGE Comercio OWNER TO SESSION_USER",
)

PROCEDURAL_LANGUAGE_LAYOUT_VARIANTS = (
    "drop procedural language Comercio",
    "DrOp PrOcEdUrAl LaNgUaGe Comercio CaScAdE",
    "DROP\tPROCEDURAL\nLANGUAGE\tIF EXISTS\nComercio",
    "ALTER/* modifier */PROCEDURAL/* object */LANGUAGE/* name */"
    '"Comercio"/* action */OWNER/* target */TO/* owner */CURRENT_ROLE',
)

PROCEDURAL_LANGUAGE_BUSINESS_COPY = (
    "Procedural language training.",
    "Procedural learning program.",
    "Alter procedural guidance for employees.",
    "Alter the wording of the policy.",
    "Drop procedural documentation.",
    "Improve language training.",
    "Language learning program.",
    "Comercio.",
    "Región Norte.",
    "Sales Receipts.",
    "State: California.",
    "Create better outcomes for employees.",
    "Create temporary project teams.",
    "round table.",
    "water table.",
)


__all__ = (
    "PROCEDURAL_LANGUAGE_BUSINESS_COPY",
    "PROCEDURAL_LANGUAGE_LAYOUT_VARIANTS",
    "PROCEDURAL_LANGUAGE_STATEMENTS",
)
