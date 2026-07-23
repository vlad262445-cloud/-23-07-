"""Генератор owner_dashboard.json - не запускается Odoo, только для ручной
регенерации при правке дашборда. Строит спредшит по образцу реального файла
Odoo (spreadsheet_dashboard_account/data/files/invoicing_dashboard.json) -
структура (pivots + PIVOT.VALUE на скрытом листе Data под scorecard-плитки,
графики odoo_pie/odoo_bar/odoo_line напрямую на purchase.finance.report)
подсмотрена там, а не придумана вслепую. См. NOTES.md.

Запуск (внутри контейнера, где смонтирован /mnt/extra-addons):
    cat data/files/generate_owner_dashboard.py | docker compose exec -T odoo python3 -
"""
import json

MODEL = 'purchase.finance.report'


def pivot(pid, name, measure_field):
    return {
        "type": "ODOO",
        "id": pid,
        "name": name,
        "model": MODEL,
        "domain": [],
        "context": {},
        "measures": [{"id": measure_field, "fieldName": measure_field}],
        "rows": [],
        "columns": [],
        "sortedColumn": None,
        "formulaId": pid,
        "fieldMatching": {},
    }


def scorecard_figure(fig_id, x, y, w, h, title, cell_ref, background="#EFF6FF"):
    return {
        "id": fig_id, "x": x, "y": y, "width": w, "height": h, "tag": "chart",
        "data": {
            "type": "scorecard",
            "title": {"text": title, "color": "#434343", "bold": True},
            "background": background,
            "baselineColorDown": "#DC6965",
            "baselineColorUp": "#00A04A",
            "baselineMode": "text",
            "keyValue": cell_ref,
            "humanize": False,
        },
    }


def odoo_chart_figure(fig_id, x, y, w, h, title, chart_type, group_by, measure, mode=None, domain=None):
    domain = domain or []
    data = {
        "title": {"text": title},
        "background": "#FFFFFF",
        "legendPosition": "top" if chart_type != "odoo_line" else "none",
        "metaData": {
            "groupBy": group_by,
            "measure": measure,
            "order": None,
            "resModel": MODEL,
        },
        "searchParams": {
            "comparison": None,
            "context": {},
            "domain": domain,
            "groupBy": group_by,
            "orderBy": [],
        },
        "type": chart_type,
        "fieldMatching": {},
    }
    if mode:
        data["metaData"]["mode"] = mode
    if chart_type == "odoo_line":
        data["verticalAxisPosition"] = "left"
        data["stacked"] = False
        data["fillArea"] = True
    if chart_type == "odoo_bar":
        data["stacked"] = False
        data["horizontal"] = False
    return {"id": fig_id, "x": x, "y": y, "width": w, "height": h, "tag": "chart", "data": data}


def make_sheet_common():
    return {
        "merges": [], "conditionalFormats": [], "tables": [],
        "headerGroups": {"ROW": [], "COL": []},
        "dataValidationRules": [], "comments": {},
        "styles": {}, "formats": {}, "borders": {},
        "areGridLinesVisible": True,
    }


def build():
    kpi_defs = [
        ("1", "Заморожено в предоплатах", "amount_frozen", "#EFF6FF"),
        ("2", "Получено, не оплачено", "amount_debt_received", "#FEF2F2"),
        ("3", "Не оплачено срочного", "amount_unpaid_urgent", "#FEF2F2"),
        ("4", "Оплачено всего", "amount_paid", "#EFF6FF"),
    ]
    pivots = {pid: pivot(pid, name, field) for pid, name, field, _bg in kpi_defs}

    data_cells = {}
    for i, (pid, name, field, _bg) in enumerate(kpi_defs, start=1):
        row = i
        data_cells[f"A{row}"] = {"content": f'="{name}"'}
        data_cells[f"B{row}"] = {"content": f'=PIVOT.VALUE({pid},"{field}")'}
        data_cells[f"C{row}"] = {"content": f"=FORMAT.LARGE.NUMBER(B{row})"}

    data_sheet = {
        "id": "data_sheet", "name": "Data", "colNumber": 6, "rowNumber": 20,
        "rows": {}, "cols": {"0": {"size": 220}, "1": {"size": 140}, "2": {"size": 140}},
        "cells": data_cells, "figures": [], "isVisible": False,
        **make_sheet_common(),
    }

    figures = []
    x = 0
    for i, (pid, name, _field, bg) in enumerate(kpi_defs):
        figures.append(scorecard_figure(
            f"kpi_{pid}", x, 0, 240, 110, name, f"Data!C{i + 1}", bg))
        x += 250

    figures.append(odoo_chart_figure(
        "chart_cost_structure", 0, 130, 490, 320,
        "Структура затрат (верхний уровень)", "odoo_pie",
        group_by=["cost_plan_root_id"], measure="amount_total"))
    figures.append(odoo_chart_figure(
        "chart_frozen_age", 500, 130, 490, 320,
        "Заморозка по возрасту", "odoo_bar",
        group_by=["days_frozen_bucket"], measure="amount_frozen",
        domain=[["amount_frozen", ">", 0]]))
    figures.append(odoo_chart_figure(
        "chart_dynamics", 0, 470, 490, 320,
        "Динамика по периодам", "odoo_line",
        group_by=["date_order:month"], measure="amount_total", mode="line"))
    figures.append(odoo_chart_figure(
        "chart_vendor_concentration", 500, 470, 490, 320,
        "Концентрация на поставщиках", "odoo_bar",
        group_by=["partner_id"], measure="amount_total"))

    dashboard_sheet = {
        "id": "dashboard_sheet", "name": "Дашборд", "colNumber": 16, "rowNumber": 60,
        "rows": {}, "cols": {}, "cells": {}, "figures": figures, "isVisible": True,
        **make_sheet_common(),
    }

    return {
        "version": 21,
        "odooVersion": 12,
        "sheets": [dashboard_sheet, data_sheet],
        "styles": {}, "formats": {}, "borders": {},
        "revisionId": "START_REVISION",
        "uniqueFigureIds": True,
        "settings": {
            "locale": {
                "name": "Russian", "code": "ru_RU",
                "thousandsSeparator": " ", "decimalSeparator": ",",
                "dateFormat": "dd/mm/yyyy", "timeFormat": "hh:mm:ss",
                "formulaArgSeparator": ";", "weekStart": 1,
            },
        },
        "pivots": pivots,
        "pivotNextId": len(kpi_defs) + 1,
        "customTableStyles": {},
        "globalFilters": [],
        "lists": {},
        "listNextId": 1,
        "chartOdooMenusReferences": {},
    }


if __name__ == "__main__":
    wb = build()
    out_path = "/mnt/extra-addons/purchase_finance_dashboard/data/files/owner_dashboard.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(wb, f, ensure_ascii=False, indent=None)
    print("written", out_path, "figures:", len(wb["sheets"][0]["figures"]))
