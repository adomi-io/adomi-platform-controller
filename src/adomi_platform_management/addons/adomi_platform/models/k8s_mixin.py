"""AbstractModel mixin: keep an Odoo record in sync with a platform.adomi.io CR.

Concrete models set ``_k8s_plural`` / ``_k8s_kind`` / ``_k8s_cluster_scoped`` and
implement ``_k8s_spec`` (and optionally ``_k8s_status_vals``). Create/write push the
CR; unlink deletes it; ``action_k8s_sync`` (button + cron) reads status back.

Pushing on create/write is best-effort (failures are logged + recorded in
``k8s_message`` and posted to chatter, not raised) so Odoo stays usable even when the
cluster is briefly unreachable. The manual Sync button raises so users see errors.
"""

import logging
import os
from datetime import timedelta

from odoo import _, api, fields, models

from . import k8s

_logger = logging.getLogger(__name__)


class K8sMixin(models.AbstractModel):
    _name = "adomi.k8s.mixin"
    _description = "Adomi Kubernetes-synced resource"

    # Overridden by concrete models.
    _k8s_plural = None
    _k8s_kind = None
    _k8s_cluster_scoped = False

    k8s_name = fields.Char(
        string="Resource name",
        required=True,
        copy=False,
        index=True,
        help="metadata.name of the Kubernetes custom resource (DNS-1123 label).",
    )
    # Selection order IS the statusbar display order (left -> right): the happy
    # path reads Pending -> Ready, with the exceptional states in between.
    k8s_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("unknown", "Unknown"),
            ("not_ready", "Not ready"),
            ("ready", "Ready"),
        ],
        string="Status",
        default="pending",
        readonly=True,
        copy=False,
    )
    k8s_message = fields.Text(string="Status message", readonly=True, copy=False)
    k8s_last_sync = fields.Datetime(string="Last synced", readonly=True, copy=False)

    # --- to override ---
    def _k8s_spec(self):
        self.ensure_one()
        return {}

    def _k8s_status_vals(self, obj):
        """Extra status field values to write from the CR object (model-specific)."""
        return {}

    # --- config ---
    @api.model
    def _k8s_namespace(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param("adomi_platform.namespace", "adomi-system")
        )

    @api.model
    def _k8s_sync_enabled(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param("adomi_platform.sync_enabled", "1") == "1"
        )

    @api.model
    def _k8s_client_namespace_prefix(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("adomi_platform.client_namespace_prefix", "adomi-client-")
        )

    def _k8s_ns(self):
        """The namespace this record's CR lives in.

        Client-owned intent lives in the per-client namespace
        (``<prefix><slug>``, e.g. adomi-client-acme); platform-scoped resources
        stay in the flat platform namespace; cluster-scoped kinds have none.
        """
        if self._k8s_cluster_scoped:
            return None
        slug = self._k8s_client_slug()
        if slug:
            return self._k8s_client_namespace_prefix() + slug
        return self._k8s_namespace()

    # --- write backend: kubernetes API vs platform API (git source of truth) ---
    @api.model
    def _k8s_write_backend(self):
        """'kubernetes' (apply CRs straight to the cluster API) or 'api' (POST intent
        to the platform API, which commits the CR to the customer's client git repo).

        The ADOMI_WRITE_BACKEND env var (set on the in-cluster deployment) overrides
        the config parameter, so the default stays 'kubernetes' for offline/local dev.
        """
        return (
            os.environ.get("ADOMI_WRITE_BACKEND")
            or self.env["ir.config_parameter"].sudo().get_param("adomi_platform.write_backend", "kubernetes")
        )

    def _k8s_client_slug(self):
        """Owning customer slug (the client repo) for the platform-API path.

        Returns False for records that are not customer-owned (cluster-scoped
        platform resources), which always take the Kubernetes API path. Concrete
        customer models (Client/Environment/Application/Snapshot) override this.
        """
        return False

    def _api_path(self):
        """Platform-API resource path for this record.

        Default: a resource collection under the owning client
        (``/v1/clients/{client}/{plural}/{name}``). Models with a different shape
        override this — Client (the client IS the resource) and Application
        (nested under its environment).
        """
        self.ensure_one()
        return "/v1/clients/%s/%s/%s" % (
            self._k8s_client_slug(),
            self._k8s_plural,
            self.k8s_name,
        )

    def _api_body(self):
        """Typed request body for the platform API (its OpenAPI contract).

        Distinct from ``_k8s_spec()``: the API takes request-level intent
        (snake_case fields, refs from the URL path) and builds the CR spec itself.
        Every customer-owned model (``_k8s_client_slug`` truthy) must implement it.
        """
        self.ensure_one()
        raise NotImplementedError(
            "%s is routed to the platform API but defines no _api_body()" % self._name
        )

    @api.model
    def _platform_api(self):
        import requests

        from . import api_client

        icp = self.env["ir.config_parameter"].sudo()
        base_url = os.environ.get("ADOMI_PLATFORM_API_URL") or icp.get_param("adomi_platform.api_url")
        token = os.environ.get("ADOMI_PLATFORM_API_TOKEN") or icp.get_param("adomi_platform.api_token")
        verify = icp.get_param("adomi_platform.api_verify_tls", "1") == "1"

        return api_client.PlatformApiClient(
            base_url,
            token,
            requests,
            verify=verify,
        )

    def _k8s_api_apply(self):
        """Send this record's intent to the platform API, which commits the CR."""
        self.ensure_one()
        self._platform_api().upsert(self._api_path(), self._api_body())

    def _k8s_api_delete(self):
        """Ask the platform API to remove this record's CR from the client repo."""
        self.ensure_one()
        self._platform_api().delete(self._api_path())

    # --- body / push ---
    def _k8s_body(self):
        self.ensure_one()
        meta = {"name": self.k8s_name}
        if not self._k8s_cluster_scoped:
            meta["namespace"] = self._k8s_ns()
        meta["labels"] = {"app.kubernetes.io/managed-by": "adomi-platform-management"}
        return {
            "apiVersion": "%s/%s" % (k8s.GROUP, k8s.VERSION),
            "kind": self._k8s_kind,
            "metadata": meta,
            "spec": self._k8s_spec(),
        }

    def _k8s_push(self):
        """Best-effort apply of the CR for each record (git commit or K8s apply)."""
        backend = self._k8s_write_backend()
        for rec in self:
            if not rec.k8s_name:
                continue
            try:
                if backend == "api" and rec._k8s_client_slug():
                    rec._k8s_api_apply()
                else:
                    k8s.apply(rec._k8s_plural, rec.k8s_name, rec._k8s_body(), rec._k8s_ns())
            except Exception as exc:  # noqa: BLE001 - keep Odoo usable on cluster errors
                _logger.exception("Adomi push failed for %s %s", rec._name, rec.k8s_name)
                rec.with_context(adomi_no_push=True).write(
                    {"k8s_state": "unknown", "k8s_message": _("Sync failed: %s") % exc}
                )
                if hasattr(rec, "message_post"):
                    rec.message_post(body=_("Kubernetes sync failed: %s") % exc)

    def _k8s_refresh_quiet(self):
        try:
            self.action_k8s_sync()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Adomi status refresh failed: %s", exc)

    # --- live UI updates (websocket bus) ---
    def _notify_bus(self, updated_fields=None):
        """Tell open form views these records changed so they live-refresh.

        Emits on a per-record channel read by the adomi_bus_listener widget. Best
        effort: a bus failure never blocks the write (e.g. the controller's status
        push still succeeds even if no UI is listening).
        """
        try:
            bus = self.env["bus.bus"]
            fields_list = list(updated_fields or [])
            for rec in self:
                if not rec.id:
                    continue
                bus._sendone(
                    "adomi_platform_%s_%s" % (rec._name, rec.id),
                    "adomi_platform_update",
                    {"id": rec.id, "model": rec._name, "updated_fields": fields_list},
                )
        except Exception as exc:  # noqa: BLE001 - UI notification is non-critical
            _logger.warning("Adomi bus notify failed: %s", exc)

    # --- read / status ---
    def _k8s_apply_obj(self, obj):
        """Write status fields from a CR object dict (status-only, no push back).

        Shared by the manual/cron Sync (which reads the CR) and ``ingest_status``
        (which receives the CR pushed by the controller).
        """
        self.ensure_one()
        ready = k8s.ready(obj)
        state = {"True": "ready", "False": "not_ready"}.get(ready, "pending" if ready == "" else "unknown")
        vals = {
            "k8s_state": state,
            "k8s_message": k8s.ready_message(obj),
            "k8s_last_sync": fields.Datetime.now(),
        }
        vals.update(self._k8s_status_vals(obj))
        self.with_context(adomi_no_push=True).write(vals)
        self._adomi_sync_variables(obj)

    # How long a portal-side Variable edit is protected from being reverted by a
    # stale cluster CR (git leads; the cluster catches up within a sync cycle).
    _adomi_variable_sync_quiet = timedelta(minutes=10)

    def _adomi_sync_variables(self, obj):
        """Mirror the CR's ``spec.variables`` into this scope's Variable records.

        Variables edited straight in git (or kubectl) show up in the portal on the
        next status push/sync — the reverse of the portal's API write. Records the
        user touched within the quiet period are left alone: the cluster CR lags
        the git commit, and a stale spec must not revert or resurrect what was
        just changed here. Any divergence self-heals on a later sync.
        """
        if "scoped_config_ids" not in self._fields:
            return
        self.ensure_one()

        desired = {}
        for var in (obj.get("spec") or {}).get("variables") or []:
            if var.get("name"):
                desired[var["name"]] = var.get("value") or ""

        cutoff = fields.Datetime.now() - self._adomi_variable_sync_quiet
        existing = {
            rec.name: rec for rec in self.scoped_config_ids if rec.kind == "variable"
        }
        no_push = self.env["adomi.scoped.config"].with_context(adomi_config_no_push=True)
        inverse = self._fields["scoped_config_ids"].inverse_name

        for name, value in desired.items():
            rec = existing.get(name)
            if rec is None:
                no_push.create(
                    {"name": name, "kind": "variable", "value": value, inverse: self.id}
                )
            elif rec.value != value and (rec.write_date or rec.create_date) < cutoff:
                rec.with_context(adomi_config_no_push=True).write({"value": value})

        for name, rec in existing.items():
            if name not in desired and (rec.write_date or rec.create_date) < cutoff:
                rec.with_context(adomi_config_no_push=True).unlink()

    def action_k8s_sync(self):
        """Read the CR and update status fields. Raises on cluster errors (manual use)."""
        for rec in self:
            obj = k8s.get(rec._k8s_plural, rec.k8s_name, rec._k8s_ns())
            if obj is None:
                # On the api backend git is the source of truth: right after a
                # write the CR is committed but GitOps hasn't applied it to the
                # cluster yet — that's in-flight provisioning, not an error.
                if self._k8s_write_backend() == "api" and rec._k8s_client_slug():
                    vals = {
                        "k8s_state": "pending",
                        "k8s_message": _(
                            "Committed to the client repo; waiting for the platform to apply it."
                        ),
                    }
                else:
                    vals = {
                        "k8s_state": "unknown",
                        "k8s_message": _("Not found in cluster."),
                    }
                vals["k8s_last_sync"] = fields.Datetime.now()
                rec.with_context(adomi_no_push=True).write(vals)
                continue
            rec._k8s_apply_obj(obj)
        return True

    # --- reverse sync: discover + import CRs FROM the cluster ---
    def _k8s_import_vals(self, obj):
        """Odoo field values from a CR object — the reverse of ``_k8s_spec``.

        Return None to skip (model not importable, or a required parent hasn't been
        imported yet). Concrete models that should be discoverable from the cluster
        override this.
        """
        return None

    @api.model
    def _k8s_obj_client_slug(self, obj):
        """Client slug a CR belongs to, derived from its intent namespace.

        Client intent namespaces are ``<prefix><slug>`` (default ``adomi-client-``,
        must match the platform API / provisioner). False for CRs outside a client
        namespace (platform-scoped resources, or the legacy single-namespace mode).
        """
        ns = (obj.get("metadata") or {}).get("namespace") or ""
        prefix = self._k8s_client_namespace_prefix()
        return ns[len(prefix) :] if prefix and ns.startswith(prefix) else False

    @api.model
    def _k8s_identity_domain(self, obj):
        """Domain identifying THE Odoo record for a CR.

        Name-only by default; client-scoped models must narrow it, because the same
        k8s_name (``production``, ``superset``, ...) exists in many clients.
        """
        return [("k8s_name", "=", (obj.get("metadata") or {}).get("name"))]

    @api.model
    def _adomi_import_one(self, obj):
        """Upsert the Odoo record for one CR (best-effort, never pushes back).

        A missing record is created from the CR spec so Odoo reflects resources made
        outside the portal (git / kubectl); an existing record gets only a status
        refresh, so a live status push never clobbers Odoo-side intent.
        """
        name = (obj.get("metadata") or {}).get("name")
        if not name:
            return False
        rec = self.search(self._k8s_identity_domain(obj), limit=1)
        if not rec:
            vals = self._k8s_import_vals(obj)
            if vals is None:
                return False  # not importable, or a parent isn't there yet
            vals["k8s_name"] = name
            rec = self.with_context(adomi_no_push=True).create(vals)
        rec._k8s_apply_obj(obj or {})
        return rec

    @api.model
    def _adomi_import_kind(self):
        """Discover + import every CR of this model's kind, cluster-wide."""
        if not self._k8s_plural:
            return 0
        count = 0
        for obj in k8s.list_(self._k8s_plural):
            try:
                if self._adomi_import_one(obj):
                    count += 1
            except Exception as exc:  # noqa: BLE001 - one bad CR shouldn't stop the sweep
                _logger.warning("Adomi import skipped a %s: %s", self._k8s_plural, exc)
        return count

    @api.model
    def ingest_status(self, k8s_name, obj):
        """Apply a status push from the platform controller (matched by k8s_name).

        Upserts: an unknown resource (created in git / kubectl outside the portal) is
        imported so Odoo stays reflective; a known one gets a status refresh. Never
        pushes back. Returns True if a record was matched or created.
        """
        return bool(self._adomi_import_one(obj or {}))

    @api.model
    def cron_sync_all(self):
        """Cron + manual entry point: discover and import every platform resource
        from the cluster. New CRs become Odoo records; known ones get a status
        refresh. Processed in dependency order so parent references resolve.
        """
        order = [
            "adomi.organization",
            "adomi.application.type",
            "adomi.client",
            "adomi.environment",
            "adomi.database.server",
            "adomi.application",
            "adomi.git.repository",
            "adomi.snapshot",
        ]
        for model_name in order:
            model = self.env.get(model_name)
            if model is None:
                continue
            try:
                model._adomi_import_kind()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("Adomi cluster sync failed for %s: %s", model_name, exc)
        return True

    # --- ORM overrides ---
    @api.model_create_multi
    def create(self, vals_list):
        # Quick-create (name_create from a many2one) bypasses the form onchange,
        # so derive the required resource name from the display name here.
        for vals in vals_list:
            if not vals.get("k8s_name") and vals.get("name"):
                vals["k8s_name"] = k8s.slugify(vals["name"])
        records = super().create(vals_list)
        if self._k8s_sync_enabled() and not self.env.context.get("adomi_no_push"):
            records._k8s_push()
            records._k8s_refresh_quiet()
        return records

    def write(self, vals):
        res = super().write(vals)
        if self._k8s_sync_enabled() and not self.env.context.get("adomi_no_push"):
            self._k8s_push()
        # Notify regardless of adomi_no_push: status writes from the controller's
        # push come in with adomi_no_push set, and those are exactly the changes the
        # open form wants to live-refresh on.
        self._notify_bus(list(vals.keys()))
        return res

    def unlink(self):
        if self._k8s_sync_enabled() and not self.env.context.get("adomi_no_push"):
            backend = self._k8s_write_backend()
            for rec in self:
                if not rec.k8s_name:
                    continue
                try:
                    if backend == "api" and rec._k8s_client_slug():
                        rec._k8s_api_delete()
                    else:
                        k8s.delete(rec._k8s_plural, rec.k8s_name, rec._k8s_ns())
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("Adomi delete failed for %s: %s", rec.k8s_name, exc)
        return super().unlink()

    # --- UX ---
    @api.onchange("name")
    def _onchange_name_k8s(self):
        if getattr(self, "name", False) and not self.k8s_name:
            self.k8s_name = k8s.slugify(self.name)
