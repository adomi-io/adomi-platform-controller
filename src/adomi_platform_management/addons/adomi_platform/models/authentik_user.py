from odoo import api, fields, models


class AuthentikUser(models.Model):
    """A mirror of Authentik's user directory (read-only, synced on demand).

    Exists so access dialogs get a normal many2one picker; the source of truth
    stays Authentik (via the platform API), same pattern as the GitHub
    repository mirror on installations.
    """

    _name = "adomi.authentik.user"
    _description = "Authentik User"
    _order = "name"

    name = fields.Char(required=True, help="Display name (falls back to the username).")
    username = fields.Char(required=True)
    email = fields.Char()
    authentik_pk = fields.Integer(string="Authentik ID", required=True, index=True)

    _sql_constraints = [
        ("authentik_pk_unique", "unique(authentik_pk)", "That Authentik user is already mirrored."),
    ]

    @api.model
    def sync_from_platform(self):
        """Upsert the directory from the platform API; drop vanished accounts."""
        users = self.env["adomi.application"]._platform_api().get("/v1/identity/users") or []

        existing = {rec.authentik_pk: rec for rec in self.search([])}
        seen = set()
        for user in users:
            pk = user.get("pk")
            if not pk:
                continue
            seen.add(pk)
            vals = {
                "name": user.get("name") or user.get("username") or "",
                "username": user.get("username") or "",
                "email": user.get("email") or False,
                "authentik_pk": pk,
            }
            rec = existing.get(pk)
            if rec:
                rec.write(vals)
            else:
                self.create(vals)

        stale = [rec.id for pk, rec in existing.items() if pk not in seen]
        if stale:
            self.browse(stale).unlink()

        return len(seen)
