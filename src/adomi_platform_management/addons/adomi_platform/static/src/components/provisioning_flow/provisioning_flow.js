/** @odoo-module **/

import {Component} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

// The customer's provisioning journey, in order. "failed" is not a step — it
// decorates whichever step the journey stalled on.
const STEPS = [
    {key: "committed", label: "Infrastructure repository", icon: "fa-git-square"},
    {key: "applied", label: "Applied to the platform", icon: "fa-cloud-upload"},
    {key: "ready", label: "Ready", icon: "fa-check-circle"},
];

/**
 * Visual provisioning journey for a platform resource, rendered as a slim
 * stepper banner (committed -> applied -> ready) with a deep link to the
 * customer's infrastructure repository, so non-technical users can SEE the
 * system working instead of reading status strings.
 *
 * Usage (between the form header and sheet):
 *   <field name="provisioning_stage" widget="adomi_provisioning_flow" nolabel="1"/>
 * Reads siblings from the record: infra_repo_url, k8s_message.
 */
export class ProvisioningFlow extends Component {
    static template = "adomi_platform.ProvisioningFlow";
    static props = {...standardFieldProps};

    get stage() {
        return this.props.record.data[this.props.name] || "committed";
    }

    get failed() {
        return this.stage === "failed";
    }

    get repoUrl() {
        return this.props.record.data.infra_repo_url || "";
    }

    get message() {
        return this.props.record.data.k8s_message || "";
    }

    get steps() {
        // When failed, the journey stalled past "committed": first step reads
        // done, second carries the error, last is blocked.
        const reachedIdx = this.failed ? 1 : STEPS.findIndex((s) => s.key === this.stage);
        const allDone = !this.failed && this.stage === "ready";
        return STEPS.map((s, idx) => {
            const done = allDone || idx < reachedIdx;
            const current = !allDone && idx === reachedIdx;
            const blocked = this.failed && idx >= reachedIdx;
            let state = "o_todo";
            if (done) {
                state = "o_done";
            } else if (blocked) {
                state = "o_blocked";
            } else if (current) {
                state = "o_current";
            }
            return {
                ...s,
                done,
                blocked: blocked && !done,
                spinning: current && !this.failed,
                nodeClass: state,
                stateClass: state,
                connectorClass: done ? "o_done" : "",
            };
        });
    }
}

fieldRegistry.add("adomi_provisioning_flow", {
    component: ProvisioningFlow,
    supportedTypes: ["selection"],
});
