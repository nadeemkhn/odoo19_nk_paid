/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class ShopifyMultiStoreDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            stores: [],
            error: false,
        });

        onWillStart(async () => {
            await this.loadDashboard();
        });
    }

    async loadDashboard() {
        this.state.loading = true;
        this.state.error = false;
        try {
            this.state.stores = await this.orm.call(
                "shopify.instance",
                "get_multi_store_dashboard_data",
                []
            );
        } catch (error) {
            this.state.error = error.message || _t("Failed to load dashboard data.");
            this.notification.add(this.state.error, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    formatCurrency(amount) {
        const numeric = Number(amount || 0);
        return new Intl.NumberFormat(undefined, {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 2,
        }).format(numeric);
    }

    formatDate(value) {
        if (!value) {
            return _t("Never");
        }
        const date = new Date(value);
        if (isNaN(date.getTime())) {
            return value;
        }
        return new Intl.DateTimeFormat(undefined, {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        }).format(date);
    }

    async _openStoreAction(store, methodName) {
        if (!store || !store.id) {
            this.notification.add(_t("Invalid store selected."), { type: "warning" });
            return;
        }
        try {
            const action = await this.orm.call("shopify.instance", methodName, [[store.id]]);
            await this.action.doAction(action);
        } catch (error) {
            this.notification.add(
                error.message || _t("Failed to open the selected store action."),
                { type: "danger" }
            );
        }
    }

    async openOrders(store) {
        await this._openStoreAction(store, "action_view_shopify_orders");
    }

    async openProducts(store) {
        await this._openStoreAction(store, "action_view_shopify_products");
    }

    async openCustomers(store) {
        await this._openStoreAction(store, "action_view_shopify_customers");
    }

    async openLogs(store) {
        await this._openStoreAction(store, "action_view_shopify_logs");
    }
}

ShopifyMultiStoreDashboard.template = "shopify.MultiStoreDashboard";
registry.category("actions").add("shopify_multi_store_dashboard", ShopifyMultiStoreDashboard);
