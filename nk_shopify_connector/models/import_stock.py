from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

class ProductProduct(models.Model):
    _inherit = 'product.product'

    shopify_variant_id = fields.Char("Shopify Variant ID")
    shopify_inventory_item_id = fields.Char("Shopify Inventory Item ID")

    def action_import_shopify_stock(self):
        """Manual action to sync stock from Shopify and update via stock.quant"""
        location = self.env.ref('stock.stock_location_stock')  # Default internal location
        updated = 0
        skipped = 0
        failed = 0

        products_by_instance = {}
        for product in self:
            instance = product.shopify_instance_id
            if not instance and self.env.context.get("shopify_instance_id"):
                instance = self.env["shopify.instance"].browse(self.env.context["shopify_instance_id"]).exists()
            if not instance:
                skipped += 1
                _logger.warning(f"⚠️ '{product.display_name}' has no Shopify instance. Skipping.")
                continue
            products_by_instance.setdefault(instance.id, self.env["product.product"])
            products_by_instance[instance.id] |= product

        if not products_by_instance:
            raise UserError("❌ No Shopify-mapped products selected.")

        for instance_id, products in products_by_instance.items():
            instance = self.env["shopify.instance"].browse(instance_id).exists()
            headers = {
                'X-Shopify-Access-Token': instance.shopify_token,
                'Content-Type': 'application/json',
            }
            shopify_domain = instance.shopify_domain

            for product in products:
                if product.type == 'consu' and not product.is_storable:
                    product.is_storable = True

                inventory_item_id = product.shopify_inventory_item_id
                if not inventory_item_id:
                    _logger.warning(f"⚠️ No inventory_item_id for '{product.name}' (ID {product.id})")
                    skipped += 1
                    continue

                stock_url = (
                    f"https://{shopify_domain}/admin/api/2023-04/inventory_levels.json"
                    f"?inventory_item_ids={inventory_item_id}"
                )
                response = requests.get(stock_url, headers=headers)
                if response.status_code != 200:
                    _logger.warning(f"❌ Failed to fetch stock for '{product.name}': {response.text}")
                    failed += 1
                    continue

                inventory_levels = response.json().get("inventory_levels", [])
                if not inventory_levels:
                    _logger.warning(f"⚠️ No stock data found for '{product.name}'")
                    skipped += 1
                    continue

                qty = inventory_levels[0].get("available", 0)

                quant = self.env['stock.quant'].search([
                    ('product_id', '=', product.id),
                    ('location_id', '=', location.id),
                ], limit=1)

                if quant:
                    quant.inventory_quantity = qty
                    quant._apply_inventory()
                    _logger.info(f"✅ Stock updated for '{product.name}' → {qty}")
                else:
                    self.env['stock.quant'].create({
                        'product_id': product.id,
                        'location_id': location.id,
                        'inventory_quantity': qty,
                    })._apply_inventory()
                    _logger.info(f"✅ Stock created for '{product.name}' → {qty}")
                updated += 1

        notif_type = 'danger' if failed else ('warning' if skipped else 'success')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Shopify Stock Import',
                'message': f'Updated: {updated}, Skipped: {skipped}, Failed: {failed}',
                'type': notif_type,
                'sticky': False,
            }
        }


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def action_bulk_import_shopify_stock_template(self):
        products = self.mapped('product_variant_ids')
        return products.action_import_shopify_stock()
