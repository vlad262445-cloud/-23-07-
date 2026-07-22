/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { useState } from "@odoo/owl";

// Единственный кусок JS во всём наборе модулей ТЗ (см. п. 2.1) - заказчик
// подтвердил 22.07.2026, что штатных средств Odoo 18 для раскрывающихся
// строк списка нет (expand в списках относится только к группировке).
//
// recordRowTemplate - официальная точка расширения самого ListRenderer
// (см. web/static/src/views/list/list_renderer.js: `static recordRowTemplate
// = "web.ListRenderer.Rows"`, используется через t-call в шаблоне Rows).
// js_class регистрируется отдельным именем и подключается только к вьюхе
// реестра закупок - на остальные списки purchase.order в системе не влияет.
export class PurchaseRegistryListRenderer extends ListRenderer {
    static recordRowTemplate = "purchase_registry_ux.RecordRow";

    setup() {
        super.setup();
        this.registryExpandState = useState({ expandedIds: new Set() });
    }

    isRegistryRowExpanded(record) {
        return this.registryExpandState.expandedIds.has(record.id);
    }

    toggleRegistryRowExpand(record) {
        const ids = this.registryExpandState.expandedIds;
        if (ids.has(record.id)) {
            ids.delete(record.id);
        } else {
            ids.add(record.id);
        }
    }
}

export const purchaseRegistryListView = {
    ...listView,
    Renderer: PurchaseRegistryListRenderer,
};

registry.category("views").add("purchase_registry_list", purchaseRegistryListView);
