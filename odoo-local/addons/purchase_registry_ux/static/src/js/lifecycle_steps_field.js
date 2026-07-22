/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

// Компактный путь заказа вместо голого числа-процента (см. отзыв
// пользователя 2026-07-23: "проценты не отражают нужным образом процесс
// закупки" - заменено на ту же идею, что уже есть и нравится пользователю
// в statusbar на форме заявки: серые ещё не пройденные этапы, зелёные
// пройденные, текущий - выделен отдельным цветом.
//
// По второму отзыву (тот же день) - рядом с сегментами выводится короткая
// подпись из pending_action_short ("Оплатить" и т.п.), чтобы не заводить
// для неё отдельную колонку "Что требуется" - две задачи одним виджетом.
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
// Тот же смысл, что и decoration-* в pending_action_short раньше - если
// требуется что-то реальное от человека, подпись оранжевая, "Отклонена"
// красная, "нечего делать" - зелёная (см. house-палитра, skill odoo-ui).
const ACTION_COLOR_CLASS = {
    declined: "text-bg-danger",
    none: "text-bg-success",
};
const DEFAULT_ACTION_COLOR_CLASS = "text-bg-warning";

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

    get actionValue() {
        return this.props.record.data.pending_action_short;
    }

    get actionLabel() {
        const selection = this.props.record.fields.pending_action_short.selection;
        const found = selection.find(([value]) => value === this.actionValue);
        return found ? found[1] : "";
    }

    get actionColorClass() {
        return ACTION_COLOR_CLASS[this.actionValue] || DEFAULT_ACTION_COLOR_CLASS;
    }
}

registry.category("fields").add("lifecycle_steps", {
    component: LifecycleStepsField,
    supportedTypes: ["selection"],
    fieldDependencies: [{ name: "pending_action_short", type: "selection" }],
});
