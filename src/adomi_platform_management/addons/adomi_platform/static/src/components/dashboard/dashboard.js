/** @odoo-module **/

import {Component, onWillStart, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";

/**
 * The platform Home dashboard (the addon's landing page).
 *
 * Surfaces high-level estate stats, a "Getting started with Adomi" guided setup
 * (only the steps not yet done are actionable), and card-style quick links. It is
 * a read-only client action — every tile/button just navigates to the relevant
 * view or wizard via the action service.
 */
export class AdomiDashboard extends Component {
    static template = "adomi_platform.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            syncing: false,
            stats: {customers: 0, appsTotal: 0, appsReady: 0, dbServers: 0, environments: 0},
            steps: [],
            allDone: false,
        });

        onWillStart(() => this.load());
    }

    async load() {
        try {
            const [customers, apps, dbServers, environments, orgs, ghInstalls] = await Promise.all([
                this.orm.searchCount("adomi.client", []),
                this.orm.searchRead("adomi.application", [], ["k8s_state"]),
                this.orm.searchCount("adomi.database.server", []),
                this.orm.searchCount("adomi.workspace", []),
                this.orm.searchCount("adomi.organization", []),
                this.orm.searchCount("adomi.github.installation", []),
            ]);

            const appsReady = apps.filter((a) => a.k8s_state === "ready").length;
            this.state.stats = {
                customers,
                appsTotal: apps.length,
                appsReady,
                dbServers,
                environments,
            };

            this.state.steps = [
                {
                    key: "github",
                    title: "Connect GitHub",
                    icon: "fa-github",
                    desc: "Install the GitHub App so the platform can manage repositories, pull requests and deployments on your behalf.",
                    done: ghInstalls > 0,
                    action: "adomi_platform.action_adomi_github_app",
                    cta: "Set up GitHub",
                },
                {
                    key: "org",
                    title: "Create your organization",
                    icon: "fa-building",
                    desc: "Define your organization — the top-level scope that owns your customers, their domains and environments.",
                    done: orgs > 0,
                    action: "adomi_platform.action_adomi_organization",
                    cta: "Add organization",
                },
                {
                    key: "customer",
                    title: "Add your first customer",
                    icon: "fa-user-plus",
                    desc: "Create a customer (tenant). Apps, databases and environments are all organized under a customer.",
                    done: customers > 0,
                    action: "adomi_platform.action_adomi_client",
                    cta: "Add customer",
                },
                {
                    key: "app",
                    title: "Deploy your first application",
                    icon: "fa-rocket",
                    desc: "Pick an app from the catalog and deploy it into a customer environment — committed to git and rolled out by Argo CD.",
                    done: apps.length > 0,
                    action: "adomi_platform.action_adomi_deploy_wizard",
                    cta: "Deploy an app",
                },
            ];
            this.state.allDone = this.state.steps.every((s) => s.done);
        } finally {
            this.state.loading = false;
        }
    }

    get completedCount() {
        return this.state.steps.filter((s) => s.done).length;
    }

    get quickLinks() {
        return [
            {
                title: "Customers",
                desc: "Your tenants and their estates",
                icon: "fa-users",
                color: "primary",
                action: "adomi_platform.action_adomi_client",
            },
            {
                title: "Deploy application",
                desc: "Roll out an app from the catalog",
                icon: "fa-rocket",
                color: "success",
                action: "adomi_platform.action_adomi_deploy_wizard",
            },
            {
                title: "Applications",
                desc: "Every deployed app and its health",
                icon: "fa-cubes",
                color: "info",
                action: "adomi_platform.action_adomi_application",
            },
            {
                title: "Database servers",
                desc: "Standalone Postgres servers",
                icon: "fa-database",
                color: "warning",
                action: "adomi_platform.action_adomi_database_server",
            },
            {
                title: "App catalog",
                desc: "Available application types",
                icon: "fa-th-large",
                color: "secondary",
                action: "adomi_platform.action_adomi_application_type",
            },
            {
                title: "GitHub",
                desc: "Connection and installations",
                icon: "fa-github",
                color: "dark",
                action: "adomi_platform.action_adomi_github_app",
            },
        ];
    }

    open(xmlid) {
        this.action.doAction(xmlid);
    }

    async syncCluster() {
        // Discover + import everything from the cluster, then refresh the tiles.
        this.state.syncing = true;
        try {
            await this.orm.call("adomi.application", "cron_sync_all", []);
            await this.load();
        } finally {
            this.state.syncing = false;
        }
    }
}

registry.category("actions").add("adomi_dashboard", AdomiDashboard);
