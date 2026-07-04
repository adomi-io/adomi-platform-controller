"""GitHub App + installations — the company's loosely-coupled GitHub identity.

The portal owns its *own* GitHub App (created via GitHub's App-Manifest flow, or
entered manually). Installing that App on an org yields an installation the portal
mints short-lived tokens for to drive the odoo.sh-style workflow. Webhooks from
the App give Odoo visibility into the lifecycle.
"""

import logging
import secrets
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import github_client

_logger = logging.getLogger(__name__)

# Permissions/events the App requests (manifest defaults). Least-privilege for
# the deploy + ticket workflow.
APP_PERMISSIONS = {
    "metadata": "read",
    "contents": "write",
    "pull_requests": "write",
    "issues": "write",
    "administration": "write",
}
# Subscribable webhook events for the manifest's default_events. NOTE: `installation`
# and `installation_repositories` are delivered automatically to every App and are NOT
# valid default_events (GitHub rejects them) — the webhook handler still receives them
# regardless, so they don't belong here.
APP_EVENTS = ["push", "pull_request", "issues"]


class GithubApp(models.Model):
    _name = "adomi.github.app"
    _description = "Adomi GitHub App"
    _inherit = ["mail.thread"]
    _rec_name = "name"
    _order = "create_date desc"

    name = fields.Char(
        string="Name",
        required=True,
        default="Adomi Deploy",
        help="The GitHub App name. Must be globally unique on GitHub.",
    )
    company_id = fields.Many2one(
        "res.company", string="Company", default=lambda self: self.env.company, required=True
    )

    # --- App identity (filled by the manifest conversion, or entered manually) ---
    app_id = fields.Char(string="App ID", copy=False, tracking=True)
    slug = fields.Char(string="Slug", copy=False)
    owner_login = fields.Char(string="Owner", copy=False)
    html_url = fields.Char(string="App URL", copy=False)
    client_id = fields.Char(string="Client ID", copy=False, groups="base.group_system")
    client_secret = fields.Char(string="Client secret", copy=False, groups="base.group_system")
    webhook_secret = fields.Char(string="Webhook secret", copy=False, groups="base.group_system")
    private_key = fields.Text(string="Private key (PEM)", copy=False, groups="base.group_system")

    # Nonce that ties a manifest/install redirect back to this record.
    manifest_state = fields.Char(copy=False, groups="base.group_system")

    state = fields.Selection(
        [("draft", "Not set up"), ("created", "App created"), ("installed", "Installed"), ("error", "Error")],
        string="Status",
        default="draft",
        readonly=True,
        copy=False,
        tracking=True,
    )
    last_error = fields.Text(readonly=True, copy=False)

    installation_ids = fields.One2many(
        "adomi.github.installation", "app_id", string="Installations"
    )
    installation_count = fields.Integer(compute="_compute_installation_count")

    @api.depends("installation_ids")
    def _compute_installation_count(self):
        for rec in self:
            rec.installation_count = len(rec.installation_ids)

    # --- urls ---
    @api.model
    def _base_url(self):
        return (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")

    def _manifest(self):
        """The GitHub App manifest GitHub uses to create the App (right perms/URLs)."""
        self.ensure_one()
        base = self._base_url()
        return {
            "name": self.name,
            "url": base or "https://example.com",
            "hook_attributes": {"url": "%s/adomi_platform/github/webhook" % base, "active": True},
            "redirect_url": "%s/adomi_platform/github/manifest_callback" % base,
            "setup_url": "%s/adomi_platform/github/setup" % base,
            "callback_urls": ["%s/adomi_platform/github/oauth_callback" % base],
            "setup_on_update": True,
            "public": False,
            "default_permissions": APP_PERMISSIONS,
            "default_events": APP_EVENTS,
        }

    # --- app auth ---
    def _app_jwt(self):
        self.ensure_one()
        rec = self.sudo()
        if not rec.app_id or not rec.private_key:
            raise UserError(_("This GitHub App is not fully configured yet."))
        return github_client.app_jwt(rec.app_id, rec.private_key)

    def _app_client(self):
        import requests

        return github_client.GitHubAppClient(self._app_jwt(), requests)

    # --- manifest flow ---
    def action_start_manifest(self):
        """Kick off GitHub's App-Manifest creation flow (browser POST to GitHub)."""
        self.ensure_one()
        if not self._base_url():
            raise UserError(
                _("Set the portal URL first (System Parameter 'web.base.url') so GitHub "
                  "can redirect back here.")
            )
        self.sudo().manifest_state = secrets.token_urlsafe(24)
        return {
            "type": "ir.actions.act_url",
            "url": "/adomi_platform/github/manifest_new/%s" % self.id,
            "target": "self",
        }

    def _apply_manifest_conversion(self, data):
        """Store the App credentials returned by the manifest conversion."""
        self.ensure_one()
        self.sudo().write(
            {
                "app_id": str(data.get("id") or ""),
                "slug": data.get("slug"),
                "name": data.get("name") or self.name,
                "owner_login": (data.get("owner") or {}).get("login"),
                "html_url": data.get("html_url"),
                "client_id": data.get("client_id"),
                "client_secret": data.get("client_secret"),
                "webhook_secret": data.get("webhook_secret"),
                "private_key": data.get("pem"),
                "state": "created",
                "last_error": False,
            }
        )
        self.message_post(body=_("GitHub App '%s' created.") % (data.get("name") or self.name))

    # --- install flow ---
    def action_install(self):
        """Send the operator to GitHub to install the App on their org."""
        self.ensure_one()
        if not self.slug:
            raise UserError(_("Create the GitHub App first."))
        self.sudo().manifest_state = secrets.token_urlsafe(24)
        url = "https://github.com/apps/%s/installations/new?state=%s" % (
            self.slug,
            self.sudo().manifest_state,
        )
        return {"type": "ir.actions.act_url", "url": url, "target": "self"}

    def action_sync_installations(self):
        """Pull the App's installations from GitHub and upsert them."""
        self.ensure_one()
        try:
            installs = self._app_client().list_installations()
        except Exception as exc:  # noqa: BLE001
            self.sudo().write({"state": "error", "last_error": str(exc)})
            raise UserError(_("Could not list installations: %s") % exc)
        Inst = self.env["adomi.github.installation"]
        for obj in installs:
            Inst._upsert_from_github(self, obj)
        if self.installation_ids and self.state == "created":
            self.sudo().state = "installed"
        return True

    def action_view_installations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Installations"),
            "res_model": "adomi.github.installation",
            "view_mode": "list,form",
            "domain": [("app_id", "=", self.id)],
            "context": {"default_app_id": self.id},
        }

    # --- webhook handling (visibility into the lifecycle) ---
    @api.model
    def _handle_webhook(self, app, event, payload):
        """Dispatch a verified GitHub event. Installation events maintain the
        installation list; pull_request/issues/push are the hook points the ticket
        lifecycle (later) will consume to mirror state into Odoo.
        """
        Inst = self.env["adomi.github.installation"]
        if event in ("installation", "installation_repositories"):
            obj = payload.get("installation")
            action = payload.get("action")
            if obj and action in ("deleted", "suspend"):
                rec = Inst.search([("installation_id", "=", str(obj.get("id")))], limit=1)
                if rec:
                    rec.write({"state": "suspended"} if action == "suspend" else {})
                    if action == "deleted":
                        rec.unlink()
            elif obj:
                Inst._upsert_from_github(app, obj)
        # pull_request / issues / push: consumed by the ticket lifecycle (next phase).
        _logger.debug("GitHub event %s handled for app %s", event, app.id)
        return True


class GithubInstallation(models.Model):
    _name = "adomi.github.installation"
    _description = "Adomi GitHub Installation"
    _inherit = ["mail.thread"]
    _rec_name = "account_login"
    _order = "account_login"

    app_id = fields.Many2one(
        "adomi.github.app", string="GitHub App", required=True, ondelete="cascade"
    )
    installation_id = fields.Char(string="Installation ID", required=True, copy=False, index=True)
    account_login = fields.Char(string="Account", tracking=True)
    account_type = fields.Char(string="Account type")
    account_avatar = fields.Char(string="Avatar URL")
    html_url = fields.Char(string="Installation URL")
    repository_selection = fields.Selection(
        [("all", "All repositories"), ("selected", "Selected repositories")],
        string="Repositories",
    )
    state = fields.Selection(
        [("active", "Active"), ("suspended", "Suspended")], string="Status", default="active"
    )
    repository_ids = fields.One2many(
        "adomi.github.repository", "installation_id", string="Repositories"
    )
    repository_count = fields.Integer(compute="_compute_repository_count")

    # short-lived installation-token cache (admin-only)
    cached_token = fields.Char(copy=False, groups="base.group_system")
    cached_token_expiry = fields.Datetime(copy=False, groups="base.group_system")

    _sql_constraints = [
        ("installation_uniq", "unique(installation_id)", "This installation is already linked."),
    ]

    @api.depends("repository_ids")
    def _compute_repository_count(self):
        for rec in self:
            rec.repository_count = len(rec.repository_ids)

    @api.model
    def _upsert_from_github(self, app, obj):
        """Create/update an installation record from a GitHub installation object."""
        account = obj.get("account") or {}
        vals = {
            "app_id": app.id,
            "installation_id": str(obj.get("id")),
            "account_login": account.get("login"),
            "account_type": account.get("type"),
            "account_avatar": account.get("avatar_url"),
            "html_url": obj.get("html_url"),
            "repository_selection": obj.get("repository_selection"),
            "state": "suspended" if obj.get("suspended_at") else "active",
        }
        rec = self.search([("installation_id", "=", str(obj.get("id")))], limit=1)
        if rec:
            rec.write(vals)
        else:
            rec = self.create(vals)
        return rec

    # --- token + client ---
    def _installation_token(self):
        """A valid ~1h installation token, minted/refreshed via the App JWT."""
        self.ensure_one()
        rec = self.sudo()
        now = fields.Datetime.now()
        if rec.cached_token and rec.cached_token_expiry and rec.cached_token_expiry > now:
            return rec.cached_token
        data = self.app_id._app_client().create_installation_token(self.installation_id)
        expires_at = data.get("expires_at")
        expiry = False
        if expires_at:
            # GitHub returns ISO-8601 with trailing Z; trim it for fromisoformat.
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", ""))
            except ValueError:
                expiry = False
        rec.write({"cached_token": data.get("token"), "cached_token_expiry": expiry})
        return data.get("token")

    def _client(self):
        import requests

        return github_client.GitHubClient(self._installation_token(), requests)

    def action_sync_repos(self):
        """Mirror the repos this installation can see into Odoo — they back the
        repository pickers (wizards) and the per-customer git views."""
        Repo = self.env["adomi.github.repository"]
        for rec in self:
            try:
                data = rec._client().installation_repos()
            except Exception as exc:  # noqa: BLE001
                raise UserError(_("Could not list repositories: %s") % exc)
            seen = Repo
            for obj in data.get("repositories") or []:
                seen |= Repo._upsert_from_github(rec, obj)
            # Repos the installation lost access to disappear from the picker.
            (rec.repository_ids - seen).unlink()
        return True

    def action_view_repos(self):
        self.ensure_one()
        self.action_sync_repos()
        return {
            "type": "ir.actions.act_window",
            "name": _("%s · Repositories") % (self.account_login or ""),
            "res_model": "adomi.github.repository",
            "view_mode": "list",
            "domain": [("installation_id", "=", self.id)],
        }


class GithubRepository(models.Model):
    """A repository visible to a GitHub App installation, mirrored into Odoo.

    Kept in sync by ``action_sync_repos`` (button + wizard onchange) so pickers
    offer real repositories instead of free-text names. Not a platform CR — the
    deployable source repos become ``adomi.git.repository`` records when linked
    to an application.
    """

    _name = "adomi.github.repository"
    _description = "Adomi GitHub Repository"
    _rec_name = "full_name"
    _order = "pushed_at desc, full_name"

    installation_id = fields.Many2one(
        "adomi.github.installation",
        string="Installation",
        required=True,
        ondelete="cascade",
        index=True,
    )
    name = fields.Char(required=True)
    full_name = fields.Char(string="Full name", required=True, index=True)
    owner_login = fields.Char(string="Owner")
    html_url = fields.Char(string="URL")
    private = fields.Boolean(string="Private")
    default_branch = fields.Char(string="Default branch")
    description = fields.Char()
    pushed_at = fields.Datetime(string="Last push")

    _sql_constraints = [
        (
            "installation_repo_uniq",
            "unique(installation_id, full_name)",
            "This repository is already mirrored for this installation.",
        ),
    ]

    @api.model
    def _upsert_from_github(self, installation, obj):
        pushed_at = False
        if obj.get("pushed_at"):
            try:
                pushed_at = datetime.fromisoformat(obj["pushed_at"].replace("Z", ""))
            except ValueError:
                pushed_at = False
        vals = {
            "installation_id": installation.id,
            "name": obj.get("name"),
            "full_name": obj.get("full_name"),
            "owner_login": (obj.get("owner") or {}).get("login"),
            "html_url": obj.get("html_url"),
            "private": bool(obj.get("private")),
            "default_branch": obj.get("default_branch"),
            "description": obj.get("description") or False,
            "pushed_at": pushed_at,
        }
        rec = self.search(
            [
                ("installation_id", "=", installation.id),
                ("full_name", "=", obj.get("full_name")),
            ],
            limit=1,
        )
        if rec:
            rec.write(vals)
        else:
            rec = self.create(vals)
        return rec
