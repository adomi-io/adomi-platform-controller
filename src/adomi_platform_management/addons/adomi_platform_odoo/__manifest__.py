{
    "name": "Adomi Platform — Odoo Pipeline",
    "version": "19.0.1.0.0",
    "summary": "Guided setup for a customer's Odoo pipeline (boilerplate repo, edition, launch)",
    "description": """
Extends the platform launch wizard with an Odoo product step: pick Community or
Enterprise, generate the customer's pipeline repository from
adomi-io/odoo-boilerplate on their GitHub, and launch — one guided flow from the
homepage.
""",
    "category": "Administration",
    "license": "LGPL-3",
    "depends": ["adomi_platform"],
    "data": [
        "views/deploy_wizard_views.xml",
        "data/setup_guides.xml",
    ],
    "installable": True,
    "application": False,
}
