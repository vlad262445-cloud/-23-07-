/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { formatDate } from "@web/core/l10n/dates";
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
//
// По третьему отзыву - вместо голых прямоугольников теперь по одной
// простой иконке на этап (та же тройная раскраска: серый/жёлтый/зелёный).
// Иконки - из штатного набора FontAwesome 4.7, который уже используется
// во всём проекте (см. odoo-ui), новых шрифтов/картинок не добавляли.
const STEPS = [
    ["draft", "Черновик", "fa-file-text-o"],
    ["to_approve", "На согласовании", "fa-hourglass-half"],
    ["approved", "Согласовано", "fa-thumbs-o-up"],
    ["prepaid", "Предоплачено", "fa-credit-card"],
    ["in_transit", "В пути", "fa-truck"],
    ["in_stock", "На складе", "fa-archive"],
    ["completed", "Завершена", "fa-flag-checkered"],
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

    stepIconClass(index) {
        return `fa ${this.steps[index][2]} o_lifecycle_step ${this.stepClass(index)}`;
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

    // Отзыв 2026-07-23: "должна быть возможность увидеть ориентировочную
    // дату прибытия" - показывается подсказкой при наведении. Дальше
    // выяснилось (тот же день, следующее сообщение), что на заказе уже
    // есть НАТИВНОЕ поле date_planned ("Ожидаемое прибытие") - оно и есть
    // основной источник, наше expected_arrival_date (с заявки, "желаемая
    // дата") показывается ВТОРЫМ пунктом только если отличается - не
    // дублируем, если заявитель и факт совпадают.
    get plannedArrival() {
        return this.props.record.data.date_planned;
    }

    get requestedArrival() {
        return this.props.record.data.expected_arrival_date;
    }

    get wrapperTitle() {
        const parts = [];
        const stageLabel = this.steps[this.currentIndex] && this.steps[this.currentIndex][1];
        if (stageLabel) {
            parts.push(stageLabel);
        }
        if (this.plannedArrival) {
            parts.push(`Ожидаемое прибытие: ${formatDate(this.plannedArrival)}`);
        }
        // hasSame(..., 'day'), а не equals() - date_planned технически
        // datetime (несёт время), expected_arrival_date - date; сравниваем
        // по календарному дню, а не поминутно.
        if (this.requestedArrival &&
            (!this.plannedArrival || !this.requestedArrival.hasSame(this.plannedArrival, 'day'))) {
            parts.push(`Желаемая дата (заявка): ${formatDate(this.requestedArrival)}`);
        }
        return parts.join("\n");
    }
}

registry.category("fields").add("lifecycle_steps", {
    component: LifecycleStepsField,
    supportedTypes: ["selection"],
    fieldDependencies: [
        { name: "pending_action_short", type: "selection" },
        { name: "expected_arrival_date", type: "date" },
        { name: "date_planned", type: "datetime" },
    ],
});
