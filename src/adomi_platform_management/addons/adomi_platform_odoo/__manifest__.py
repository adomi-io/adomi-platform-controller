{
    "name": "Adomi Platform — Odoo Pipeline",
    "version": "19.0.4.0.0",
    "summary": "Guided setup for a customer's Odoo pipeline (boilerplate repo, edition, launch)",
    "description": """
Extends the platform launch wizard with an Odoo product step: pick Community or
Enterprise, generate the customer's pipeline repository from
adomi-io/odoo-boilerplate on their GitHub, and launch — one guided flow from the
homepage. Odoo-type applications additionally get a Pipeline tab: addon sources
and pip/apt dependencies as data, committed to the pipeline repository as
adomi-pipeline.yaml plus a generated Dockerfile.
""",
    "category": "Administration",
    "license": "LGPL-3",
    "depends": ["adomi_platform"],
    "external_dependencies": {"python": ["yaml"]},
    "data": [
        "security/ir.model.access.csv",
        "views/deploy_wizard_views.xml",
        "views/application_views.xml",
        "views/odoo_project_views.xml",
        "data/setup_guides.xml",
    ],
    "installable": True,
    "application": False,
}
