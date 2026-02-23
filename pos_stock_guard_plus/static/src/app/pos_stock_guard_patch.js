/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { _t } from "@web/core/l10n/translation";

function toNumber(value, fallback = 0) {
    const parsed = typeof value === "number" ? value : Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function asRecordId(value) {
    if (!value) {
        return false;
    }
    if (typeof value === "number") {
        return value;
    }
    if (Array.isArray(value)) {
        return typeof value[0] === "number" ? value[0] : false;
    }
    return typeof value.id === "number" ? value.id : false;
}

function normalizeIds(rawValues) {
    if (!Array.isArray(rawValues)) {
        return new Set();
    }
    const ids = rawValues
        .map((value) => asRecordId(value))
        .filter((id) => Number.isInteger(id));
    return new Set(ids);
}

function buildWarnBody(checkResult) {
    const details = checkResult?.message || _t("Stock is below the configured limit.");
    return (
        `${details}\n\n` +
        _t("Continue to add this quantity, or Cancel to keep the current quantity.") +
        `\n\n${_t("Do you want to continue anyway?")}`
    );
}

patch(PosStore.prototype, {
    _psgResolveProduct(vals) {
        if (!vals) {
            return null;
        }

        let product = vals.product_id;
        if (typeof product === "number") {
            product = this.models["product.product"].get(product);
        }
        if (product?.id) {
            return product;
        }

        let productTemplate = vals.product_tmpl_id;
        if (typeof productTemplate === "number") {
            productTemplate = this.models["product.template"].get(productTemplate);
        }

        const variants = productTemplate?.product_variant_ids || [];
        return variants.length ? variants[0] : null;
    },

    _psgGetOrderQty(productId) {
        const order = this.getOrder();
        if (!order || !productId) {
            return 0;
        }
        return order
            .getOrderlines()
            .filter((line) => line.product_id?.id === productId && line.getQuantity() > 0)
            .reduce((sum, line) => sum + line.getQuantity(), 0);
    },

    async _psgCheckStock(product, addQty) {
        const config = this.config;
        if (!config?.pos_stock_guard_enabled || !product?.id || addQty <= 0) {
            return { status: "ok" };
        }

        const excludedProductIds = normalizeIds(config.pos_stock_guard_excluded_product_ids);
        if (excludedProductIds.has(product.id)) {
            return { status: "ok" };
        }

        const isStorable = product.is_storable ?? product.type === "product";
        if (!isStorable) {
            return { status: "ok" };
        }

        const orderQty = this._psgGetOrderQty(product.id);

        try {
            return (
                (await this.data.call("pos.session", "check_pos_stock_guard", [
                    config.id,
                    product.id,
                    addQty,
                    orderQty,
                    this.session?.id || false,
                ])) || { status: "ok" }
            );
        } catch {
            // Fail open so checkout is not blocked by transient network/RPC issues.
            return { status: "ok" };
        }
    },

    async addLineToCurrentOrder(vals, opts = {}, configure = true) {
        const product = this._psgResolveProduct(vals);
        if (product) {
            let addQty = toNumber(vals?.qty, Number.NaN);
            if (Number.isNaN(addQty)) {
                addQty = this.getOrder()?.preset_id?.is_return ? -1 : 1;
            }

            const checkResult = await this._psgCheckStock(product, addQty);
            if (checkResult.status === "warn") {
                const confirmed = await ask(this.dialog, {
                    title: checkResult.title || _t("Stock Guard Warning"),
                    body: buildWarnBody(checkResult),
                    confirmLabel: _t("Continue"),
                    cancelLabel: _t("Cancel"),
                    confirmClass: "btn-warning",
                });
                if (!confirmed) {
                    return;
                }
            }
            if (checkResult.status === "block") {
                this.dialog.add(AlertDialog, {
                    title: checkResult.title || _t("Out of Stock"),
                    body: checkResult.message || _t("Cannot add this quantity because stock is insufficient."),
                });
                return;
            }
        }

        return await super.addLineToCurrentOrder(vals, opts, configure);
    },
});

patch(OrderSummary.prototype, {
    async _setValue(val) {
        if (this.pos.numpadMode !== "quantity" || val === "remove") {
            return super._setValue(...arguments);
        }

        let selectedLine = this.currentOrder?.getSelectedOrderline();
        if (!selectedLine) {
            return super._setValue(...arguments);
        }
        if (selectedLine.combo_parent_id) {
            selectedLine = selectedLine.combo_parent_id;
        }

        const targetQty = toNumber(val, Number.NaN);
        if (!Number.isFinite(targetQty)) {
            return super._setValue(...arguments);
        }

        const currentQty = selectedLine.getQuantity();
        if (targetQty <= currentQty || targetQty <= 0) {
            return super._setValue(...arguments);
        }

        const config = this.pos.config;
        const product = selectedLine.product_id;
        const isStorable = product?.is_storable ?? product?.type === "product";
        const excludedProductIds = normalizeIds(config?.pos_stock_guard_excluded_product_ids);
        if (!config?.pos_stock_guard_enabled || !product?.id || !isStorable || excludedProductIds.has(product.id)) {
            return super._setValue(...arguments);
        }

        const addQty = targetQty - currentQty;
        const orderQty = this.currentOrder
            .getOrderlines()
            .filter((line) => line.product_id?.id === product.id && line.getQuantity() > 0)
            .reduce((sum, line) => sum + line.getQuantity(), 0);

        let checkResult = { status: "ok" };
        try {
            checkResult =
                (await this.pos.data.call("pos.session", "check_pos_stock_guard", [
                    config.id,
                    product.id,
                    addQty,
                    orderQty,
                    this.pos.session?.id || false,
                ])) || { status: "ok" };
        } catch {
            checkResult = { status: "ok" };
        }

        if (checkResult.status === "warn") {
            const confirmed = await ask(this.dialog, {
                title: checkResult.title || _t("Stock Guard Warning"),
                body: buildWarnBody(checkResult),
                confirmLabel: _t("Continue"),
                cancelLabel: _t("Cancel"),
                confirmClass: "btn-warning",
            });
            if (!confirmed) {
                this.numberBuffer.reset();
                return;
            }
        }
        if (checkResult.status === "block") {
            this.dialog.add(AlertDialog, {
                title: checkResult.title || _t("Out of Stock"),
                body: checkResult.message || _t("Cannot set this quantity because stock is insufficient."),
            });
            this.numberBuffer.reset();
            return;
        }

        return super._setValue(...arguments);
    },
});

patch(PosOrder.prototype, {
    // Compatibility shim for addons still calling legacy order.getChange().
    getChange() {
        return this.change || 0;
    },
});
