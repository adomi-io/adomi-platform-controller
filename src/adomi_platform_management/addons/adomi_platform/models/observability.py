"""Deep links + server-side metrics/logs for platform resources.

An AbstractModel mixin that turns a resource's namespace into:

* deep links to the related platform services (the app itself, Argo CD, Grafana,
  logs, Harbor) — derived from the platform base domain, overridable per service
  via ``ir.config_parameter``;
* live metrics (CPU / memory) queried from Prometheus and recent logs queried from
  Loki, both over the in-cluster service so nothing has to be embedded or exposed.

All queries are best-effort with a short timeout: the management portal must stay
responsive even when monitoring is briefly unreachable.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

from odoo import fields, models

_logger = logging.getLogger(__name__)

# In-cluster monitoring endpoints (kube-prometheus-stack + Loki, monitoring ns).
# Overridable via ir.config_parameter so a different stack just needs new params.
PROMETHEUS_DEFAULT = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
LOKI_DEFAULT = "http://loki.monitoring.svc.cluster.local:3100"
HTTP_TIMEOUT = 5  # seconds; keep the Odoo worker responsive when monitoring is down


class ObservabilityMixin(models.AbstractModel):
    _name = "adomi.observability.mixin"
    _description = "Adomi deep links + observability"

    # Deep links (computed, not stored; reflect live config + status).
    link_app_url = fields.Char(compute="_compute_links")
    link_argocd_url = fields.Char(compute="_compute_links")
    link_grafana_url = fields.Char(compute="_compute_links")
    link_logs_url = fields.Char(compute="_compute_links")
    link_harbor_url = fields.Char(compute="_compute_links")

    # --- hooks (overridable per model) ---
    def _obs_namespace(self):
        self.ensure_one()
        return getattr(self, "namespace", "") or ""

    def _obs_pod_regex(self):
        """Regex matching this resource's pods within the namespace.

        Empty means the whole namespace (right for an Environment); models that
        are ONE workload (Application) override so metrics/logs are scoped to
        that app, not everything sharing its namespace.
        """
        return ""

    def _obs_label_filters(self, extra=""):
        """The Prom/Loki label filter list for this resource ('' when unscoped)."""
        ns = self._obs_namespace()
        if not ns:
            return ""
        filters = ['namespace="%s"' % ns]
        pod = self._obs_pod_regex()
        if pod:
            filters.append('pod=~"%s"' % pod)
        if extra:
            filters.append(extra)
        return ",".join(filters)

    def _obs_app_url(self):
        self.ensure_one()
        return getattr(self, "url", "") or ""

    def _obs_argocd_app(self):
        """Name of the Argo CD Application for this resource, if any."""
        return ""

    def _obs_has_source(self):
        return False

    # --- config helpers ---
    def _obs_param(self, key, default=""):
        return self.env["ir.config_parameter"].sudo().get_param("adomi_platform.%s" % key, default)

    def _obs_base_domain(self):
        # Prefer an explicit param; else the (single) Organization's base domain.
        base = self._obs_param("base_domain")

        if base:
            return base

        org = self.env["adomi.organization"].sudo().search([("base_domain", "!=", False)], limit=1)

        return org.base_domain or ""

    def _obs_host(self, key, prefix):
        explicit = self._obs_param("%s_host" % key)

        if explicit:
            return explicit

        base = self._obs_base_domain()

        return ("%s.%s" % (prefix, base)) if base else ""

    def _obs_prometheus_url(self):
        return (self._obs_param("prometheus_url") or PROMETHEUS_DEFAULT).rstrip("/")

    def _obs_loki_url(self):
        return (self._obs_param("loki_url") or LOKI_DEFAULT).rstrip("/")

    # --- deep links ---
    def _compute_links(self):
        argocd_ns = self._obs_param("argocd_namespace", "argocd")

        for rec in self:
            ns = rec._obs_namespace()
            argocd = rec._obs_host("argocd", "argocd")
            grafana = rec._obs_host("grafana", "grafana")
            harbor = rec._obs_host("harbor", "harbor")
            app = rec._obs_argocd_app()

            rec.link_app_url = rec._obs_app_url()
            rec.link_argocd_url = (
                "https://%s/applications/%s/%s" % (argocd, argocd_ns, app) if argocd and app else ""
            )
            # Grafana Explore, pre-filtered to this resource's logs (Loki datasource).
            if grafana and ns:
                explore = {
                    "datasource": "loki",
                    "queries": [{"expr": "{%s}" % rec._obs_label_filters()}],
                    "range": {"from": "now-1h", "to": "now"},
                }
                qs = urllib.parse.quote(json.dumps(explore))
                rec.link_logs_url = "https://%s/explore?left=%s" % (grafana, qs)
                rec.link_grafana_url = "https://%s/d?var-namespace=%s" % (grafana, ns)
            else:
                rec.link_logs_url = ""
                rec.link_grafana_url = ""
            rec.link_harbor_url = (
                "https://%s/harbor/projects" % harbor if harbor and rec._obs_has_source() else ""
            )

    def _obs_open(self, url):
        if not url:
            return False

        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    def action_open_app(self):
        return self._obs_open(self.link_app_url)

    def action_open_argocd(self):
        return self._obs_open(self.link_argocd_url)

    def action_open_grafana(self):
        return self._obs_open(self.link_grafana_url)

    def action_open_logs(self):
        return self._obs_open(self.link_logs_url)

    def action_open_harbor(self):
        return self._obs_open(self.link_harbor_url)

    # --- HTTP helpers ---
    def _obs_get_json(self, url):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 - in-cluster
                return json.loads(resp.read() or b"{}")
        except Exception as exc:  # noqa: BLE001 - monitoring is optional
            _logger.warning("Adomi observability query failed (%s): %s", url, exc)
            return {}

    # --- time windows ---
    def _obs_window(self, minutes, start_s=0, end_s=0):
        """(start, end) epoch seconds: an explicit window (drill-down) or now-N."""
        if start_s and end_s and end_s > start_s:
            return int(start_s), int(end_s)
        end = int(time.time())
        return end - int(minutes) * 60, end

    # --- metrics (Prometheus) ---
    def get_metrics(self, minutes=60, start_s=0, end_s=0):
        """Return CPU + memory time series for this resource's workload.

        Shape: {"namespace", "start", "end", "series": {"cpu": [[ts, v], ...],
        "memory": [[ts, v], ...]}}. Empty when no namespace / monitoring down.
        """
        self.ensure_one()

        selector = self._obs_label_filters('container!=""')

        if not selector:
            return {}

        prom = self._obs_prometheus_url()
        start, end = self._obs_window(minutes, start_s, end_s)
        step = max(15, (end - start) // 60)

        queries = {
            "cpu": "sum(rate(container_cpu_usage_seconds_total{%s}[5m]))" % selector,
            "memory": "sum(container_memory_working_set_bytes{%s})" % selector,
        }

        series = {key: self._prom_range(prom, q, start, end, step) for key, q in queries.items()}

        return {
            "namespace": self._obs_namespace(),
            "start": start,
            "end": end,
            "series": series,
        }

    def _prom_range(self, prom, query, start, end, step):
        params = urllib.parse.urlencode(
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            }
        )

        data = self._obs_get_json("%s/api/v1/query_range?%s" % (prom, params))
        result = (data.get("data") or {}).get("result") or []

        if not result:
            return []

        # Single aggregated series (we sum() in the query).
        return [[int(float(ts)), float(val)] for ts, val in result[0].get("values", [])]

    # --- logs (Loki) ---
    def _obs_log_query(self, search=""):
        """The LogQL stream selector (+ optional line filter) for this resource."""
        selector = self._obs_label_filters()
        if not selector:
            return ""
        query = "{%s}" % selector
        search = (search or "").strip()
        if search:
            query += ' |= "%s"' % search.replace("\\", "\\\\").replace('"', '\\"')
        return query

    def get_logs(self, limit=200, minutes=60, search="", start_s=0, end_s=0):
        """Log lines for this resource's workload, newest first.

        ``search`` becomes a Loki line filter (server-side, so it searches the
        full window, not just the fetched page); ``start_s``/``end_s`` pin an
        explicit window for time drill-down.
        """
        self.ensure_one()

        query = self._obs_log_query(search)

        if not query:
            return []

        loki = self._obs_loki_url()
        start, end = self._obs_window(minutes, start_s, end_s)
        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": limit,
                "start": int(start * 1e9),
                "end": int(end * 1e9),
                "direction": "backward",
            }
        )

        data = self._obs_get_json("%s/loki/api/v1/query_range?%s" % (loki, params))
        streams = (data.get("data") or {}).get("result") or []
        lines = []

        for stream in streams:
            pod = (stream.get("stream") or {}).get("pod") or ""

            for ts_ns, line in stream.get("values", []):
                lines.append({"ts": int(ts_ns), "pod": pod, "line": line})

        lines.sort(key=lambda x: x["ts"], reverse=True)

        return lines[:limit]

    def get_log_histogram(self, minutes=60, search="", start_s=0, end_s=0, buckets=40):
        """Log volume over time (the ECS-style bar strip above the log list).

        Returns {"start", "end", "step", "buckets": [[ts, count], ...]} — counts
        come from Loki's count_over_time so they reflect the FULL volume, not
        just the fetched page. Clicking a bucket drills into (ts, ts + step).
        """
        self.ensure_one()

        query = self._obs_log_query(search)

        if not query:
            return {}

        loki = self._obs_loki_url()
        start, end = self._obs_window(minutes, start_s, end_s)
        step = max(5, (end - start) // max(1, buckets))
        params = urllib.parse.urlencode(
            {
                "query": "sum(count_over_time(%s [%ss]))" % (query, step),
                "start": int(start * 1e9),
                "end": int(end * 1e9),
                "step": step,
            }
        )

        data = self._obs_get_json("%s/loki/api/v1/query_range?%s" % (loki, params))
        result = (data.get("data") or {}).get("result") or []
        values = result[0].get("values", []) if result else []

        return {
            "start": start,
            "end": end,
            "step": step,
            "buckets": [[int(float(ts)), int(float(val))] for ts, val in values],
        }
