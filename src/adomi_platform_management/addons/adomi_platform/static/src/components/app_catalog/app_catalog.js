/** @odoo-module **/

import {Component, onWillStart, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

// A friendly glyph per catalog entry (keyed by the type's k8s name) so the catalog
// reads like an app store rather than a dropdown. Unknown types get a generic cube.
const TYPE_ICONS = {
    odoo: "fa-cubes",
    superset: "fa-bar-chart",
    mailpit: "fa-envelope-o",
    vaultwarden: "fa-shield",
    windmill: "fa-cogs",
    "uptime-kuma": "fa-heartbeat",
    "open-webui": "fa-comments-o",
};
const GENERIC_ICON = "fa-cube";

/**
 * Visual application-type picker for the deploy wizard. Renders the cluster's
 * ApplicationType catalog as selectable cards (icon + capabilities) and writes the
 * choice straight into the bound many2one (`type_id`). This replaces a plain
 * dropdown so "roll out an app" feels like picking from a store.
 *
 * Usage: <field name="type_id" widget="adomi_app_catalog" nolabel="1"/>
 */
export class AppCatalog extends Component {
    static template = "adomi_platform.AppCatalog";
    static props = {...standardFieldProps};

    setup() {
        this.orm = useService("orm");
        this.state = useState({types: [], loading: true});

        onWillStart(async () => {
            try {
                this.state.types = await this.orm.searchRead(
                    "adomi.application.type",
                    [],
                    ["id", "name", "k8s_name", "database_required", "sso_protocol"],
                    {order: "name"}
                );
            } finally {
                this.state.loading = false;
            }
        });
    }

    get selectedId() {
        const val = this.props.record.data[this.props.name];
        return (val && val.id) || false;
    }

    iconFor(type) {
        return TYPE_ICONS[type.k8s_name] || GENERIC_ICON;
    }

    isSelected(type) {
        return this.selectedId === type.id;
    }

    select(type) {
        this.props.record.update({[this.props.name]: {id: type.id, display_name: type.name}});
    }
}

fieldRegistry.add("adomi_app_catalog", {
    component: AppCatalog,
    supportedTypes: ["many2one"],
});
