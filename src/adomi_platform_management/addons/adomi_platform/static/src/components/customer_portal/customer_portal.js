/** @odoo-module **/

import {Component, onWillStart, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

const SCOPE_LABELS = {
    organization: "Org",
    client: "Customer",
    environment: "Env",
    application: "App",
};

/**
 * The customer page IS the portal: one view over everything the platform runs
 * for this customer — domains, database servers, and the environment ->
 * application tree (host, databases, variables per app). Cards are summaries;
 * every line opens the right record dialog in place and reloads on close.
 *
 * Usage: <field name="id" widget="adomi_customer_portal" nolabel="1"/>
 */
export class CustomerPortal extends Component {
    static template = "adomi_platform.CustomerPortal";
    static props = {...standardFieldProps};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            data: null,
            loading: true,
            collapsed: {},
            git: null,
            gitLoading: true,
        });

        onWillStart(() => this.load());
    }

    get resId() {
        return this.props.record.resId;
    }

    async load() {
        if (!this.resId) {
            this.state.loading = false;
            this.state.gitLoading = false;
            return;
        }
        try {
            this.state.data = await this.orm.call("adomi.client", "get_portal_data", [
                [this.resId],
            ]);
        } finally {
            this.state.loading = false;
        }
        // Git reads go out to Forgejo — refresh them after the page, never
        // instead of it.
        this.loadGit();
    }

    async loadGit() {
        try {
            this.state.git = await this.orm.call("adomi.client", "get_git_panel", [
                [this.resId],
            ]);
        } catch {
            this.state.git = {available: false, reason: "error"};
        } finally {
            this.state.gitLoading = false;
        }
    }

    commitAge(commit) {
        if (!commit.date) {
            return "";
        }
        const mins = Math.max(0, Math.floor((Date.now() - new Date(commit.date)) / 60000));
        if (mins < 1) {
            return "just now";
        }
        if (mins < 60) {
            return `${mins} min ago`;
        }
        const hours = Math.floor(mins / 60);
        if (hours < 48) {
            return `${hours} h ago`;
        }
        return `${Math.floor(hours / 24)} d ago`;
    }

    // The customer-scoped "Getting started": the same idea as the dashboard's
    // guided setup, but for standing up ONE customer. Hidden once complete.
    get setupSteps() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        return [
            {
                key: "domain",
                label: "Add a domain",
                hint: "Their own domain (one CNAME on their side) or a branded name on ours.",
                done: d.domains.length > 0,
                action: () => this.addDomain(),
                button: "Add domain",
            },
            {
                key: "server",
                label: "Add a database server",
                hint: "In-cluster (provisioned for them) or a connection to one they run.",
                done: d.servers.length > 0,
                action: () => this.addServer(),
                button: "Add server",
            },
            {
                key: "environment",
                label: "Create an environment",
                hint: "production, development, … — each gets its own namespace.",
                done: d.environments.length > 0,
                action: () => this.newEnvironment(),
                button: "New environment",
            },
            {
                key: "app",
                label: "Deploy the first application",
                hint: "Pick from the catalog; hosts, databases and SSO wire up from here.",
                done: d.environments.some((e) => e.apps.length > 0),
                action: () => this.deployApp(null),
                button: "Deploy application",
            },
        ];
    }

    get setupDone() {
        const steps = this.setupSteps;
        return steps.length > 0 && steps.every((s) => s.done);
    }

    // The GitOps journey of this customer's intent, shown with the repository
    // it lives in: committed (in git) -> applied (in the cluster) -> ready.
    get flowSteps() {
        const stage = this.state.data?.client?.provisioning_stage || "committed";
        const order = ["committed", "applied", "ready"];
        const reached = stage === "failed" ? 1 : order.indexOf(stage);
        return order.map((key, i) => ({
            key,
            label: {committed: "Committed", applied: "Applied", ready: "Ready"}[key],
            done: i <= reached && stage !== "failed",
            failed: stage === "failed" && i === reached,
        }));
    }

    get flowFailed() {
        return this.state.data?.client?.provisioning_stage === "failed";
    }

    // Environments start expanded; production estates are small enough that
    // seeing everything beats remembering to unfold it.
    isCollapsed(env) {
        return Boolean(this.state.collapsed[env.id]);
    }

    toggleEnv(env) {
        this.state.collapsed[env.id] = !this.state.collapsed[env.id];
    }

    scopeLabel(entry) {
        return SCOPE_LABELS[entry.scope] || entry.scope;
    }

    stateClass(state) {
        return {
            ready: "text-bg-success",
            not_ready: "text-bg-danger",
            pending: "text-bg-warning",
        }[state] || "text-bg-light border";
    }

    stateLabel(state) {
        return {
            ready: "Ready",
            not_ready: "Attention",
            pending: "Provisioning",
            unknown: "Unknown",
        }[state] || state;
    }

    // --- dialogs: open the record, reload the portal when it closes ---
    _dialog(action) {
        this.action.doAction(
            {
                type: "ir.actions.act_window",
                views: [[false, "form"]],
                target: "new",
                ...action,
            },
            {onClose: () => this.load()}
        );
    }

    addDomain() {
        this._dialog({
            name: "Add a domain",
            res_model: "adomi.domain",
            context: {default_client_id: this.resId},
        });
    }

    editDomain(domain) {
        this._dialog({
            name: domain.fqdn,
            res_model: "adomi.domain",
            res_id: domain.id,
        });
    }

    addServer() {
        this._dialog({
            name: "Add a database server",
            res_model: "adomi.database.server",
            context: {default_client_id: this.resId},
        });
    }

    editServer(server) {
        this._dialog({
            name: server.name,
            res_model: "adomi.database.server",
            res_id: server.id,
        });
    }

    newEnvironment() {
        this._dialog({
            name: "New environment",
            res_model: "adomi.environment",
            context: {default_client_id: this.resId},
        });
    }

    openEnvironment(env) {
        this._dialog({
            name: env.name,
            res_model: "adomi.environment",
            res_id: env.id,
        });
    }

    async deployApp(env) {
        const action = await this.orm.call("adomi.client", "action_open_deploy_wizard", [
            [this.resId],
        ]);
        if (env) {
            action.context = {...action.context, default_environment_id: env.id};
        }
        this.action.doAction(action, {onClose: () => this.load()});
    }

    async editHost(app) {
        const action = await this.orm.call("adomi.application", "action_open_host_dialog", [
            [app.id],
        ]);
        this.action.doAction(action, {onClose: () => this.load()});
    }

    addConfig(app) {
        this._dialog({
            name: `Add a variable or secret — ${app.name}`,
            res_model: "adomi.scoped.config",
            context: {default_application_id: app.id},
        });
    }

    editConfig(app, entry) {
        this._dialog({
            name: `${entry.name} (${this.scopeLabel(entry)})`,
            res_model: "adomi.scoped.config",
            res_id: entry.id,
        });
    }

    editDatabase(app) {
        // Databases are provision-time wiring; the full app form is the editor.
        this.openAppForm(app);
    }

    openApp(app) {
        if (app.url) {
            window.open(app.url, "_blank", "noopener");
        }
    }

    openAppForm(app) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "adomi.application",
            res_id: app.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openRepo() {
        const url = this.state.data?.client?.infra_repo_url;
        if (url) {
            window.open(url, "_blank", "noopener");
        }
    }
}

fieldRegistry.add("adomi_customer_portal", {
    component: CustomerPortal,
    supportedTypes: ["integer"],
});
