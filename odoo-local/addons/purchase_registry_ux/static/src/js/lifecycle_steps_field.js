/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

// Компактный путь заказа вместо голого числа-процента (см. отзыв
// пользователя 2026-07-23: "проценты не отражают нужным образом процесс
// закупки" - заменено на ту же идею, что уже есть и нравится пользователю
// в statusbar на форме заявки: серые ещё не пройденные этапы, зелёные
// пройденные, текущий - выделен отдельным цветом.
const STEPS = [
    ["draft", "Черновик"],
    ["to_approve", "На согласовании"],
    ["approved", "Согласовано"],
    ["prepaid", "Предоплачено"],
    ["in_transit", "В пути"],
    ["in_stock", "На складе"],
    ["completed", "Завершена"],
];
const STEP_INDEX = Object.fromEntries(STEPS.map(([key], index) => [key, index]));
const OFF_TRACK_LABELS = {
    declined: "Отклонена",
    cancel: "Отменена",
};

export class LifecycleStepsField extends Component {
    static template = "purchase_registry_ux.LifecycleSteps";
    static props = { ...standardFieldProps };

    get steps() {
        return STEPS;
    }

    get stage() {
        return this.props.record.data[this.props.name];
    }

    get offTrackLabel() {
        return OFF_TRACK_LABELS[this.stage];
    }

    get currentIndex() {
        return this.stage in STEP_INDEX ? STEP_INDEX[this.stage] : -1;
    }

    stepClass(index) {
        if (index < this.currentIndex) {
            return "o_lifecycle_step_done";
        }
        if (index === this.currentIndex) {
            return "o_lifecycle_step_current";
        }
        return "o_lifecycle_step_todo";
    }
}

registry.category("fields").add("lifecycle_steps", {
    component: LifecycleStepsField,
    supportedTypes: ["selection"],
});
