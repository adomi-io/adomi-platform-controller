/** @odoo-module **/

import {Component, onWillStart, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

const SCOPE_LABELS = {
    organization: "Organization",
    client: "Customer",
    environment: "Environment",
    application: "Application",
};

/**
 * The GitHub-style roll-up AND the editor: every Variable & Secret this app's
 * workload receives, with a badge showing WHICH scope each value comes from,
 * strikethrough entries a nearer scope overrode, and add/edit straight from
 * the table (a dialog on the underlying adomi.scoped.config record — values
 * set in git are picked up here too and stay editable). Secrets show a lock
 * and never a value.
 *
 * Usage: <field name="id" widget="adomi_effective_config" nolabel="1"/>
 */
export class EffectiveConfig extends Component {
    static template = "adomi_platform.EffectiveConfig";
    static props = {...standardFieldProps};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({entries: [], loading: true});

        onWillStart(() => this.load());
    }

    async load() {
        const resId = this.props.record.resId;
        if (!resId) {
            this.state.loading = false;
            return;
        }
        try {
            this.state.entries = await this.orm.call(
                "adomi.application",
                "get_effective_config",
                [[resId]]
            );
        } finally {
            this.state.loading = false;
        }
    }

    scopeLabel(entry) {
        return SCOPE_LABELS[entry.scope] || entry.scope;
    }

    _openDialog(action) {
        this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "adomi.scoped.config",
                views: [[false, "form"]],
                target: "new",
                ...action,
            },
            {onClose: () => this.load()}
        );
    }

    addEntry() {
        this._openDialog({
            name: "Add a variable or secret",
            context: {default_application_id: this.props.record.resId},
        });
    }

    editEntry(entry) {
        this._openDialog({
            name: `${entry.name} (${this.scopeLabel(entry)})`,
            res_id: entry.id,
        });
    }
}

fieldRegistry.add("adomi_effective_config", {
    component: EffectiveConfig,
    supportedTypes: ["integer"],
});
