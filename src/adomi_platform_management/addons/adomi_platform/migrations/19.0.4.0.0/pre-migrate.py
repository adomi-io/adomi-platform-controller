"""Rename adomi.workspace -> adomi.environment (terminology canon: environment).

Runs BEFORE the registry loads the new code, so the existing table/columns are
renamed in place and the ORM sees them as already matching the new model. Covers:
the model registry rows, the table, the renamed columns (workspace_id on
application, workspace_class on the model itself), xmlids (so the loader updates
records in place instead of delete+create), and chatter/attachment continuity.
"""


def _table_exists(cr, table):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
    return bool(cr.fetchone())


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _table_exists(cr, "adomi_workspace"):
        return  # fresh install or already migrated

    # --- model registry -----------------------------------------------------
    # (ir_model.name is a translatable JSONB in Odoo 19 — leave it; the module
    # loader rewrites it from the new _description.)
    cr.execute("UPDATE ir_model SET model = 'adomi.environment' WHERE model = 'adomi.workspace'")
    cr.execute(
        "UPDATE ir_model_fields SET model = 'adomi.environment' WHERE model = 'adomi.workspace'"
    )
    cr.execute(
        "UPDATE ir_model_fields SET relation = 'adomi.environment' "
        "WHERE relation = 'adomi.workspace'"
    )
    cr.execute(
        "UPDATE ir_model_data SET model = 'adomi.environment' WHERE model = 'adomi.workspace'"
    )

    # --- table + renamed columns --------------------------------------------
    cr.execute("ALTER TABLE adomi_workspace RENAME TO adomi_environment")

    if _column_exists(cr, "adomi_environment", "workspace_class"):
        cr.execute("ALTER TABLE adomi_environment RENAME COLUMN workspace_class TO environment_class")
        cr.execute(
            "UPDATE ir_model_fields SET name = 'environment_class' "
            "WHERE model = 'adomi.environment' AND name = 'workspace_class'"
        )

    if _column_exists(cr, "adomi_application", "workspace_id"):
        cr.execute("ALTER TABLE adomi_application RENAME COLUMN workspace_id TO environment_id")
        cr.execute(
            "UPDATE ir_model_fields SET name = 'environment_id' "
            "WHERE model = 'adomi.application' AND name = 'workspace_id'"
        )

    # --- xmlids: match the new module source so records update in place ------
    cr.execute(
        "UPDATE ir_model_data SET name = replace(name, 'workspace', 'environment') "
        "WHERE module = 'adomi_platform' AND name LIKE '%workspace%'"
    )

    # --- chatter / attachments keep pointing at the records -------------------
    for table, column in (
        ("mail_message", "model"),
        ("mail_followers", "res_model"),
        ("ir_attachment", "res_model"),
    ):
        if _table_exists(cr, table):
            cr.execute(
                f"UPDATE {table} SET {column} = 'adomi.environment' "  # noqa: S608 - fixed identifiers
                f"WHERE {column} = 'adomi.workspace'"
            )
