"""Scope existing git repositories to their customer.

adomi.git.repository used to be platform-scoped, so pipeline repos created by
the launch wizard were applied to the shared platform namespace — where the
customer's applications cannot resolve them (their sourceRepositoryRef is
looked up in the client namespace). Backfill client_id from the application
that links each repo, then re-push so the GitRepository CR is committed to the
customer's infrastructure repo. The stray platform-namespace CRs are left for
manual cleanup (the controller never garbage-collects unreferenced repos).
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    repos = env["adomi.git.repository"].search([("client_id", "=", False)])
    for repo in repos:
        application = env["adomi.application"].search(
            [("git_repository_id", "=", repo.id), ("client_id", "!=", False)], limit=1
        )
        if application:
            # A plain write: the k8s mixin pushes the CR (now client-scoped,
            # via the platform API into the customer's repo) best-effort.
            repo.client_id = application.client_id
