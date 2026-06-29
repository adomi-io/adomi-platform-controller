/** @odoo-module **/

import {Component, onWillStart, onWillUnmount, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

const SPARK_W = 260;
const SPARK_H = 44;

/**
 * Inline observability for a platform resource: CPU + memory sparklines (from
 * Prometheus) and recent log lines (from Loki), fetched server-side via ORM so
 * nothing is embedded or exposed. Refreshes on demand and every 30s.
 *
 * Place on a form bound to a namespace-bearing field, e.g.
 *   <field name="namespace" widget="adomi_observability" nolabel="1"/>
 */
export class K8sObservability extends Component {
    static template = "adomi_platform.K8sObservability";
    static props = standardFieldProps;

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            loading: false,
            error: null,
            metrics: null,
            logs: [],
        });

        onWillStart(() => {
            this.load();
            this._poller = setInterval(() => this.load(), 30000);
        });
        onWillUnmount(() => {
            if (this._poller) {
                clearInterval(this._poller);
                this._poller = null;
            }
        });
    }

    get resId() {
        return this.props.record?.resId;
    }

    get model() {
        return this.props.record?.resModel;
    }

    async load() {
        if (!this.resId) {
            return;
        }
        this.state.loading = true;
        this.state.error = null;
        try {
            const [metrics, logs] = await Promise.all([
                this.orm.call(this.model, "get_metrics", [[this.resId]]),
                this.orm.call(this.model, "get_logs", [[this.resId], 100]),
            ]);
            this.state.metrics = metrics || {};
            this.state.logs = logs || [];
        } catch (e) {
            this.state.error = e?.message || "Failed to load observability data.";
        } finally {
            this.state.loading = false;
        }
    }

    _series(key) {
        return this.state.metrics?.series?.[key] || [];
    }

    _sparkline(points) {
        if (!points || points.length < 2) {
            return "";
        }
        const vals = points.map((p) => p[1]);
        const min = Math.min(...vals);
        const max = Math.max(...vals);
        const span = max - min || 1;
        const n = points.length;
        return points
            .map((p, i) => {
                const x = (i / (n - 1)) * SPARK_W;
                const y = SPARK_H - ((p[1] - min) / span) * SPARK_H;
                return `${x.toFixed(1)},${y.toFixed(1)}`;
            })
            .join(" ");
    }

    get cpuSpark() {
        return this._sparkline(this._series("cpu"));
    }

    get memSpark() {
        return this._sparkline(this._series("memory"));
    }

    get cpuLatest() {
        const s = this._series("cpu");
        if (!s.length) {
            return "—";
        }
        return `${s[s.length - 1][1].toFixed(2)} cores`;
    }

    get memLatest() {
        const s = this._series("memory");
        if (!s.length) {
            return "—";
        }
        return this._formatBytes(s[s.length - 1][1]);
    }

    _formatBytes(b) {
        if (!b) {
            return "0 B";
        }
        const units = ["B", "KiB", "MiB", "GiB", "TiB"];
        let i = 0;
        let v = b;
        while (v >= 1024 && i < units.length - 1) {
            v /= 1024;
            i++;
        }
        return `${v.toFixed(1)} ${units[i]}`;
    }

    get hasMetrics() {
        return this._series("cpu").length > 0 || this._series("memory").length > 0;
    }

    formatTs(tsNs) {
        // Loki timestamps are nanoseconds.
        const d = new Date(tsNs / 1e6);
        return d.toLocaleTimeString();
    }

    get sparkWidth() {
        return SPARK_W;
    }

    get sparkHeight() {
        return SPARK_H;
    }
}

fieldRegistry.add("adomi_observability", {
    component: K8sObservability,
    supportedTypes: ["char"],
});
