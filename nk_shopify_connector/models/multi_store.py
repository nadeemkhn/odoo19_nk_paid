from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    _shopify_order_instance_unique = models.Constraint(
        "unique(shopify_instance_id, shopify_order_id)",
        "This Shopify order is already imported for the selected Shopify instance.",
    )

    shopify_instance_id = fields.Many2one("shopify.instance", string="Shopify Instance", index=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for vals in vals_list:
            if vals.get("shopify_instance_id"):
                instance_id = vals.get("shopify_instance_id")
            else:
                instance_id = self._resolve_shopify_instance_id(vals)
                if instance_id:
                    vals["shopify_instance_id"] = instance_id

            shopify_order_id = str(vals.get("shopify_order_id") or "").strip()
            # Deduplicate Shopify orders per instance to prevent duplicate sale orders.
            if shopify_order_id and instance_id:
                existing = self.search([
                    ("shopify_order_id", "=", shopify_order_id),
                    ("shopify_instance_id", "=", instance_id),
                ], limit=1)
                if existing:
                    records |= existing
                    continue

            records |= super(SaleOrder, self).create([vals])
        return records

    def _resolve_shopify_instance_id(self, vals):
        context_instance_id = self.env.context.get("shopify_instance_id")
        if context_instance_id:
            return context_instance_id

        order_url = vals.get("shopify_order_url") or ""
        if order_url:
            for instance in self.env["shopify.instance"].search([]):
                if instance.shopify_domain and instance.shopify_domain in order_url:
                    return instance.id
        return False


class ShopifyOrderImport(models.Model):
    _inherit = "shopify.order.import"

    shopify_instance_id = fields.Many2one("shopify.instance", string="Shopify Instance", index=True, copy=False)
