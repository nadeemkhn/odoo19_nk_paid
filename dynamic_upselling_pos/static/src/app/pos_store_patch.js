/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";
import { DynamicUpsellingPopup } from "./dynamic_upselling_popup/dynamic_upselling_popup";

patch(PosStore.prototype, {
    async _getDynamicUpsellingProducts() {
        const configId = this.config?.id;
        if (!configId) {
            return [];
        }

        const productIds = await this.data.call(
            "axsync.dynamic.upselling",
            "get_upsell_products_for_config",
            [configId]
        );

        if (!productIds?.length) {
            return [];
        }

        const productModel = this.models["product.product"];
        const missingProductIds = productIds.filter((productId) => !productModel.get(productId));

        if (missingProductIds.length) {
            await this.data.searchRead(
                "product.product",
                [["id", "in", missingProductIds]],
                this.data.fields["product.product"]
            );
            await this.processProductAttributes();
        }

        return productIds.map((productId) => productModel.get(productId)).filter(Boolean);
    },

    async pay() {
        const currentOrder = this.get_order();

        if (!currentOrder?.canPay()) {
            return;
        }

        try {
            const upsellProducts = await this._getDynamicUpsellingProducts();
            if (upsellProducts.length) {
                const payload = await makeAwaitable(this.dialog, DynamicUpsellingPopup, {
                    products: upsellProducts,
                });

                for (const line of payload?.lines || []) {
                    const product = this.models["product.product"].get(line.product_id);
                    if (product) {
                        await this.addLineToCurrentOrder(
                            {
                                product_id: product,
                                qty: line.qty,
                            },
                            {}
                        );
                    }
                }
            }
        } catch {
            // Never block checkout if upselling data cannot be loaded.
        }

        return await super.pay(...arguments);
    },
});
