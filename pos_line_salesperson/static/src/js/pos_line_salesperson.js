/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";

patch(PosOrderline.prototype, {
    _findEmployeeIdBySalespersonName(name) {
        const target = (name || "").trim();
        if (!target) {
            return false;
        }
        const employees = this.order_id?.pos?.models?.["hr.employee"] || [];
        const employee = employees.find((emp) => (emp.name || "").trim() === target);
        return employee?.id || false;
    },

    setup(vals) {
        super.setup(vals);
        if (!this.salesperson_name) {
            this.salesperson_name = this.order_id?.getCashierName?.() || this.order_id?.user_id?.name || "";
        }
        if (!this.salesperson_employee_id) {
            this.salesperson_employee_id =
                this._findEmployeeIdBySalespersonName(this.salesperson_name) ||
                this.order_id?.cashier?.id ||
                false;
        }
    },

    setSalespersonName(name) {
        const value = (name || "").trim();
        // Force ORM payload consistency even if other patches alter orderline shape.
        this.update({
            salesperson_name: value,
            salesperson_employee_id: false,
        });
    },

    setSalesperson(salesperson) {
        const value = (salesperson?.name || "").trim();
        this.update({
            salesperson_name: value,
            salesperson_employee_id: salesperson?.id || false,
        });
    },

    getSalespersonName() {
        return this.salesperson_name || "";
    },

    getSalespersonEmployeeId() {
        const raw = this.salesperson_employee_id;
        if (!raw) {
            return false;
        }
        if (typeof raw === "number") {
            return raw;
        }
        if (Array.isArray(raw)) {
            return raw[0] || false;
        }
        return raw.id || false;
    },

    getSalespersonImage() {
        const employeeId = this.getSalespersonEmployeeId();
        return employeeId ? `/web/image/hr.employee.public/${employeeId}/avatar_128` : "";
    },

    canBeMergedWith(orderline) {
        return (
            super.canBeMergedWith(orderline) &&
            (this.salesperson_name || "") === (orderline.salesperson_name || "") &&
            (this.getSalespersonEmployeeId?.() || false) ===
                (orderline.getSalespersonEmployeeId?.() || false)
        );
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(opts);
        data.salesperson_name = this.getSalespersonName() || false;
        data.salesperson_employee_id = this.getSalespersonEmployeeId() || false;
        return data;
    },
});

patch(PosOrder.prototype, {
    // Compatibility for addons expecting legacy getChange() API.
    getChange() {
        return this.change || 0;
    },
});

patch(Orderline.prototype, {
    get lineScreenValues() {
        const vals = super.lineScreenValues;
        return {
            ...vals,
            salespersonName:
                this.line.getSalespersonName?.() || this.line.salesperson_name || "",
            salespersonImage:
                this.line.getSalespersonImage?.() || "",
        };
    },
});

patch(ControlButtons.prototype, {
    get selectedSalespersonName() {
        const line = this.currentOrder?.getSelectedOrderline();
        return line?.getSalespersonName?.() || _t("Not set");
    },

    _getConfiguredSalespersonIds() {
        const rawConfigured = this.pos.config.line_salesperson_employee_ids || [];
        const ids = rawConfigured
            .map((value) => (typeof value === "number" ? value : value?.id))
            .filter((id) => Number.isInteger(id));
        return new Set(ids);
    },

    _getSalespersonsFromLocalModels() {
        const emps = this.pos.models?.["hr.employee"] || [];
        if (!emps.length) {
            return [];
        }
        const configuredIds = this._getConfiguredSalespersonIds();
        const hasConfiguredIds = configuredIds.size > 0;
        return emps
            .filter((e) => {
                if (hasConfiguredIds) {
                    return configuredIds.has(e.id);
                }
                return e.show_in_pos_salesperson;
            })
            .map((e) => ({
                id: e.id,
                name: e.name,
                source: "employee",
                image: `/web/image/hr.employee.public/${e.id}/avatar_128`,
            }));
    },

    async clickSalesperson() {
        const selectedLine = this.currentOrder?.getSelectedOrderline();
        if (!selectedLine) {
            this.notification.add(_t("Please select an order line first."), {
                type: "warning",
            });
            return;
        }

        let salespersons = [];
        try {
            salespersons = await this.pos.data.call(
                "pos.config",
                "get_pos_salespersons",
                [this.pos.config.id]
            );
        } catch (error) {
            console.error("POS salesperson RPC failed", error);
            // Fallback for setups where employees are loaded in POS.
            salespersons = this._getSalespersonsFromLocalModels();
        }

        if (!salespersons.length) {
            this.notification.add(_t("No salesperson is available."), {
                type: "warning",
            });
            return;
        }

        const currentEmployeeId = selectedLine.getSalespersonEmployeeId?.() || false;
        const currentName = selectedLine.getSalespersonName?.() || "";
        const payload = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Select Salesperson"),
            list: salespersons.map((sp) => ({
                id: sp.id,
                label: sp.name,
                image: sp.image,
                isSelected: sp.id === currentEmployeeId || (!currentEmployeeId && sp.name === currentName),
                item: sp,
            })),
        });

        if (!payload) {
            return;
        }
        selectedLine.setSalesperson(payload);
    },
});
