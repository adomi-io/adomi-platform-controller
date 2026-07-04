{
    "name": "Adomi Platform Management",
    "version": "19.0.6.0.0",
    "summary": "Manage the Adomi platform (Clients, Environments, Applications) from Odoo",
    "description": """
Adomi Platform Management
=========================

Mirrors the platform.adomi.io CustomResourceDefinitions as Odoo models and keeps
them in sync with Kubernetes. Creating or editing a record applies the matching
custom resource; the platform controller pushes live status (Ready / phase / URL)
back into Odoo as it changes (with a manual button + fallback cron). Odoo becomes
the primary portal for running the platform.
""",
    "category": "Services/Platform",
    "author": "Adomi Software, LLC",
    "website": "https://github.com/adomi-io/adomi-platform-controller",
    "depends": ["base", "mail", "web", "bus", "auth_oidc"],
    "external_dependencies": {"python": ["kubernetes", "jwt"]},
    "post_init_hook": "post_init_hook",
    "data": [
        "security/ir.model.access.csv",
        "data/config_parameters.xml",
        "data/ir_cron.xml",
        "views/dashboard_views.xml",
        "views/github_app_views.xml",
        "views/organization_views.xml",
        "views/client_views.xml",
        "views/environment_views.xml",
        "views/database_server_views.xml",
        "views/application_type_views.xml",
        "views/application_views.xml",
        "views/git_repository_views.xml",
        "views/snapshot_views.xml",
        "views/deploy_wizard_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "adomi_platform/static/src/components/dashboard/dashboard.js",
            "adomi_platform/static/src/components/dashboard/dashboard.xml",
            "adomi_platform/static/src/components/dashboard/dashboard.scss",
            "adomi_platform/static/src/components/k8s_bus_listener/k8s_bus_listener.js",
            "adomi_platform/static/src/components/k8s_bus_listener/k8s_bus_listener.xml",
            "adomi_platform/static/src/components/k8s_observability/k8s_observability.js",
            "adomi_platform/static/src/components/k8s_observability/k8s_observability.xml",
            "adomi_platform/static/src/components/app_catalog/app_catalog.js",
            "adomi_platform/static/src/components/app_catalog/app_catalog.xml",
            "adomi_platform/static/src/components/provisioning_flow/provisioning_flow.js",
            "adomi_platform/static/src/components/provisioning_flow/provisioning_flow.xml",
            "adomi_platform/static/src/components/provisioning_flow/provisioning_flow.scss",
            "adomi_platform/static/src/components/effective_config/effective_config.js",
            "adomi_platform/static/src/components/effective_config/effective_config.xml",
        ],
    },
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
