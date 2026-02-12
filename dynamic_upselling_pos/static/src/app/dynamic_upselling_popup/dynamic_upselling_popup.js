/** @odoo-module */

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class DynamicUpsellingPopup extends Component {
    static template = "dynamic_upselling_pos.DynamicUpsellingPopup";
    static components = { Dialog };
    static props = {
        products: Array,
        close: Function,
        getPayload: Function,
    };

    setup() {
        this.state = useState({
            selectedQty: {},
        });
    }

    get title() {
        return _t("Do you want to add more products?");
    }
//
//    get subtitle() {
//        return _t("(Avez-vous des bocaux consignes)");
//    }

    getProductName(product) {
        return product.display_name || product.name;
    }

    getProductPrice(product) {
        return this.env.utils.formatCurrency(product.lst_price || 0);
    }

    getProductImageUrl(product) {
        return `/web/image?model=product.product&field=image_128&id=${product.id}`;
    }

    getProductQty(productId) {
        return this.state.selectedQty[productId] || 0;
    }

    onAdd(productId) {
        this.state.selectedQty[productId] = 1;
    }

    onRemove(productId) {
        delete this.state.selectedQty[productId];
    }

    onIncrease(productId) {
        this.state.selectedQty[productId] = this.getProductQty(productId) + 1;
    }

    onDecrease(productId) {
        const qty = this.getProductQty(productId);
        if (qty <= 1) {
            this.onRemove(productId);
            return;
        }
        this.state.selectedQty[productId] = qty - 1;
    }

    onConfirm() {
        const lines = Object.entries(this.state.selectedQty)
            .filter(([, qty]) => qty > 0)
            .map(([productId, qty]) => ({
                product_id: Number(productId),
                qty,
            }));
        this.props.getPayload({ lines });
        this.props.close();
    }

    onSkip() {
        this.props.getPayload({ lines: [] });
        this.props.close();
    }
}
