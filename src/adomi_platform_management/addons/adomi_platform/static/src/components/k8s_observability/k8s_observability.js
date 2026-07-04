/** @odoo-module **/

import {Component, onWillStart, onWillUnmount, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

const SPARK_W = 260;
const SPARK_H = 44;
const HIST_H = 60;
const LOG_LIMIT = 200;

const RANGES = [
    {minutes: 15, label: "15m"},
    {minutes: 60, label: "1h"},
    {minutes: 360, label: "6h"},
    {minutes: 1440, label: "24h"},
    {minutes: 4320, label: "3d"},
];

/**
 * Application-scoped observability: CPU + memory sparklines (Prometheus) and an
 * ECS-style log explorer (Loki) — time-range picker, server-side search, a log
 * volume histogram whose bars drill into their time bucket, expandable lines.
 * Everything is fetched server-side via ORM and scoped to THIS resource's pods
 * (not the whole namespace). Refreshes on demand and every 30s while on "now".
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
            histogram: null,
            rangeMinutes: 60,
            search: "",
            // Drill-down window (epoch seconds); null = "the last N minutes".
            drill: null,
            expanded: {},
        });

        onWillStart(() => {
            this.load();
            this._poller = setInterval(() => {
                // Live-follow only while looking at "now"; a drilled window is
                // a fixed slice of the past.
                if (!this.state.drill) {
                    this.load();
                }
            }, 30000);
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

    get ranges() {
        return RANGES;
    }

    _window() {
        const drill = this.state.drill;
        return {
            minutes: this.state.rangeMinutes,
            start_s: drill ? drill.start : 0,
            end_s: drill ? drill.end : 0,
        };
    }

    async load() {
        if (!this.resId) {
            return;
        }
        this.state.loading = true;
        this.state.error = null;
        const win = this._window();
        const logKwargs = {...win, search: this.state.search, limit: LOG_LIMIT};
        try {
            const [metrics, logs, histogram] = await Promise.all([
                this.orm.call(this.model, "get_metrics", [[this.resId]], win),
                this.orm.call(this.model, "get_logs", [[this.resId]], logKwargs),
                this.orm.call(this.model, "get_log_histogram", [[this.resId]], {
                    ...win,
                    search: this.state.search,
                }),
            ]);
            this.state.metrics = metrics || {};
            this.state.logs = logs || [];
            this.state.histogram = histogram || null;
            this.state.expanded = {};
        } catch (e) {
            this.state.error = e?.message || "Failed to load observability data.";
        } finally {
            this.state.loading = false;
        }
    }

    setRange(minutes) {
        this.state.rangeMinutes = minutes;
        this.state.drill = null;
        this.load();
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            this.state.search = ev.target.value;
            this.load();
        }
    }

    applySearch(ev) {
        const input = ev.target.closest(".o_adomi_obs_toolbar")?.querySelector("input");
        this.state.search = input ? input.value : this.state.search;
        this.load();
    }

    drillInto(bucket) {
        const step = this.state.histogram?.step || 60;
        this.state.drill = {start: bucket[0] - step, end: bucket[0]};
        this.load();
    }

    clearDrill() {
        this.state.drill = null;
        this.load();
    }

    toggleExpand(index) {
        this.state.expanded[index] = !this.state.expanded[index];
    }

    // --- histogram geometry ---
    get histBuckets() {
        return this.state.histogram?.buckets || [];
    }

    get histMax() {
        return Math.max(1, ...this.histBuckets.map((b) => b[1]));
    }

    get histHeight() {
        return HIST_H;
    }

    barHeight(bucket) {
        return Math.max(bucket[1] > 0 ? 2 : 0, (bucket[1] / this.histMax) * HIST_H);
    }

    get windowLabel() {
        const drill = this.state.drill;
        if (drill) {
            return `${this.formatClock(drill.start * 1e9)} – ${this.formatClock(drill.end * 1e9)}`;
        }
        const range = RANGES.find((r) => r.minutes === this.state.rangeMinutes);
        return `Last ${range ? range.label : this.state.rangeMinutes + "m"}`;
    }

    // --- metric sparklines ---
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

    formatClock(tsNs) {
        return new Date(tsNs / 1e6).toLocaleTimeString();
    }

    formatTs(tsNs) {
        const d = new Date(tsNs / 1e6);
        // Wide windows need the date, not just the clock.
        if (this.state.rangeMinutes > 1440 && !this.state.drill) {
            return d.toLocaleString();
        }
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
