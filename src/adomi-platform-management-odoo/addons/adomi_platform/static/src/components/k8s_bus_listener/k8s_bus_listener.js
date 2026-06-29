/** @odoo-module **/

import {Component, onWillStart, onWillUnmount, onWillUpdateProps, useState} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

// Single notification type for every platform model; the channel is per-record so a
// form only hears about its own resource. Must match k8s_mixin._notify_bus on the server.
const NOTIFICATION_TYPE = "adomi_platform_update";

function channelFor(model, resId) {
    return model && resId ? `adomi_platform_${model}_${resId}` : null;
}

/**
 * Live-updates a platform record's form. The platform controller pushes status
 * changes into Odoo (POST /adomi_platform/ingest -> write), which emits a bus
 * notification on this record's channel. When the form is clean we reload it
 * automatically; when the user has unsaved edits we show a non-destructive banner.
 *
 * Drop on any platform form as: <field name="id" widget="adomi_bus_listener" nolabel="1"/>
 */
export class K8sBusListener extends Component {
    static template = "adomi_platform.K8sBusListener";
    static props = standardFieldProps;

    setup() {
        this.bus = useService("bus_service");
        this.state = useState({hasRemoteUpdate: false});
        this.currentChannel = null;
        this._onBusMessage = this.onBusMessage.bind(this);

        this._model = () => this.props.record?.resModel;

        this._updateChannelSubscription = (resId) => {
            const desired = channelFor(this._model(), resId);
            if (this.currentChannel && this.currentChannel !== desired) {
                this.bus.deleteChannel(this.currentChannel);
                this.currentChannel = null;
            }
            if (desired && this.currentChannel !== desired) {
                this.bus.addChannel(desired);
                this.currentChannel = desired;
            }
        };

        onWillStart(() => {
            this.bus.subscribe(NOTIFICATION_TYPE, this._onBusMessage);
            this._updateChannelSubscription(this.props.record?.resId);
            // A new record gets its id only after first save; poll briefly so we
            // subscribe to the right channel once it exists.
            this._idPoller = setInterval(() => {
                const resId = this.props.record?.resId;
                if (resId && this.currentChannel !== channelFor(this._model(), resId)) {
                    this._updateChannelSubscription(resId);
                }
            }, 1000);
        });

        onWillUpdateProps((nextProps) => {
            if (this.props.record?.resId !== nextProps.record?.resId) {
                this._updateChannelSubscription(nextProps.record?.resId);
            }
        });

        onWillUnmount(() => {
            if (this.currentChannel) {
                this.bus.deleteChannel(this.currentChannel);
                this.currentChannel = null;
            }
            this.bus.unsubscribe(NOTIFICATION_TYPE, this._onBusMessage);
            if (this._idPoller) {
                clearInterval(this._idPoller);
                this._idPoller = null;
            }
        });
    }

    async _isDirty() {
        const root = this.props.record?.model?.root;
        try {
            const v = typeof root?.isDirty === "function" ? root.isDirty() : root?.isDirty;
            return v instanceof Promise ? await v : !!v;
        } catch {
            // If we can't tell, assume dirty so we never clobber unsaved edits.
            return true;
        }
    }

    async onBusMessage(payload) {
        const resId = this.props.record?.resId;
        if (!resId || !payload || payload.id !== resId || payload.model !== this._model()) {
            return;
        }
        if (await this._isDirty()) {
            this.state.hasRemoteUpdate = true;
        } else {
            this.reloadRecord();
        }
    }

    async reloadRecord() {
        try {
            await this.props.record.model.root.load();
        } catch {
            // best-effort reload
        }
        this.state.hasRemoteUpdate = false;
    }
}

fieldRegistry.add("adomi_bus_listener", {
    component: K8sBusListener,
    supportedTypes: ["integer", "char", "many2one"],
});
