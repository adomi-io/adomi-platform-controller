from odoo import api, fields, models


class SetupGuide(models.Model):
    """A guided-setup card on the platform homepage.

    Product addons (adomi_platform_odoo, ...) register one via data XML so their
    launch experience is one click from the dashboard. The core ships the generic
    "Launch an application" guide; addons add product pipelines.
    """

    _name = "adomi.setup.guide"
    _description = "Adomi Guided Setup"
    _order = "sequence, id"

    name = fields.Char(required=True)
    description = fields.Char()
    icon = fields.Char(default="fa-rocket", help="Font Awesome icon class.")
    sequence = fields.Integer(default=10)
    action_id = fields.Many2one("ir.actions.act_window", string="Opens", required=True)
    active = fields.Boolean(default=True)

    @api.model
    def get_guides(self):
        """Card data for the dashboard's Guided setup section."""
        return [
            {
                "id": g.id,
                "name": g.name,
                "description": g.description or "",
                "icon": g.icon or "fa-rocket",
                "action_id": g.action_id.id,
            }
            for g in self.search([])
        ]
