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
 * The GitHub-style roll-up: every Variable & Secret this app's workload
 * receives, with a badge showing WHICH scope each value comes from and
 * strikethrough entries that a nearer scope overrode. Secrets show a lock and
 * never a value.
 *
 * Usage: <field name="id" widget="adomi_effective_config" nolabel="1"/>
 */
export class EffectiveConfig extends Component {
    static template = "adomi_platform.EffectiveConfig";
    static props = {...standardFieldProps};

    setup() {
        this.orm = useService("orm");
        this.state = useState({entries: [], loading: true});

        onWillStart(async () => {
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
        });
    }

    scopeLabel(entry) {
        return SCOPE_LABELS[entry.scope] || entry.scope;
    }
}

fieldRegistry.add("adomi_effective_config", {
    component: EffectiveConfig,
    supportedTypes: ["integer"],
});
