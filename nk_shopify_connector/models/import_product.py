import requests
import base64
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ShopifyProductImporter(models.TransientModel):
    _name = 'shopify.product.importer'
    _description = 'Import Products from Shopify'

    def _log_sync(self, **vals):
        try:
            self.env['shopify.sync.log'].log_event(**vals)
        except Exception:
            _logger.debug("Failed to persist sync log", exc_info=True)

    def cron_import_products_from_shopify(self):
        return self.import_products()

    def _get_target_instances(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            instance = self.env["shopify.instance"].browse(instance_id).exists()
            return instance
        instances = self.env["shopify.instance"].search([("state", "=", "confirm")])
        if not instances:
            instances = self.env["shopify.instance"].search([])
        return instances

    def import_products(self):
        instances = self._get_target_instances()
        if not instances:
            raise UserError("❌ No Shopify instance configured.")
        total_imported = 0
        for instance in instances:
            total_imported += self._import_products_for_instance(instance)
        return total_imported

    def _import_products_for_instance(self, instance):
        domain = instance.shopify_domain
        token = instance.shopify_token
        limit = 50
        since_id = 0
        imported_count = 0

        self._log_sync(
            operation="Import Products",
            message=f"Scheduled Shopify product import started for instance '{instance.name}'.",
            status="info",
            level="info",
            channel="import",
            instance_id=instance.id,
        )

        while True:
            url = (f"https://{domain}/admin/api/2023-04/products.json"
                   f"?limit={limit}&since_id={since_id}")
            headers = {
                'X-Shopify-Access-Token': token,
                'Content-Type': 'application/json',
            }
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                self._log_sync(
                    operation="Import Products",
                    message=f"Shopify API error during import: {resp.text}",
                    status="failed",
                    level="error",
                    channel="import",
                    instance_id=instance.id,
                    response=resp.text,
                )
                raise UserError(f"Shopify API error: {resp.text}")

            products = resp.json().get("products", [])
            if not products:
                break

            for product in products:
                self._create_or_update_template(product, instance)
                imported_count += 1

            since_id = max(p.get('id', 0) for p in products)

        self._log_sync(
            operation="Import Products",
            message=(
                f"Scheduled Shopify product import finished for '{instance.name}'. "
                f"Imported/updated {imported_count} products."
            ),
            status="success",
            level="info",
            channel="import",
            instance_id=instance.id,
            payload={"imported_count": imported_count},
        )
        return imported_count

    def _create_or_update_template(self, product_data, instance):
        title      = product_data.get("title") or _("(no title)")
        shopify_id = product_data.get("id")
        variants   = product_data.get("variants", [])

        # 1) Lookup existing template by shopify ID or name in this instance
        template = self.env['product.template'].search([
            ('shopify_product_id', '=', str(shopify_id)),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)
        if not template:
            template = self.env['product.template'].search([
                ('name', '=', title),
                ('shopify_instance_id', 'in', [False, instance.id]),
            ], limit=1)

        if not variants:
            _logger.warning("No variants for %s – skipping import.", title)
            return

        v0 = variants[0]
        vals = {
            'name':               title,
            'shopify_product_id': str(shopify_id),
            'default_code':       v0.get("sku") or False,
            'list_price':         float(v0.get("price") or 0.0),
            'standard_price':     float(v0.get("compare_at_price") or 0.0),
            # Odoo 19: stockable goods use type=consu + is_storable=True
            'type':               'consu',
            'is_storable':        True,
            'shopify_instance_id': instance.id,
        }
        if not template:
            template = self.env['product.template'].create(vals)
        else:
            template.write(vals)

        # 2) Update Shopify IDs on the default variant
        default_var = template.product_variant_id
        default_var.write({
            'shopify_variant_id':        str(v0.get("id") or ""),
            'shopify_inventory_item_id': str(v0.get("inventory_item_id") or ""),
        })

        # 3) Safely download & attach the first image
        image_info = product_data.get("image")
        if image_info and image_info.get("src"):
            try:
                data = requests.get(image_info["src"]).content
                template.image_1920 = base64.b64encode(data)
            except Exception as e:
                _logger.warning("Failed to download image for %s: %s", title, e)

        # 4) Sync stock only when inventory tracking is enabled
        if template.type == 'consu' and template.is_storable:
            current_qty = default_var.qty_available
            target_qty  = int(v0.get("inventory_quantity", 0))
            delta_qty   = target_qty - current_qty

            if delta_qty:
                stock_location = self.env.ref('stock.stock_location_stock')
                self.env['stock.quant']._update_available_quantity(
                    default_var,
                    stock_location,
                    delta_qty
                )
        else:
            _logger.info(
                "Skipping stock sync for product %s (type=%s, is_storable=%s)",
                template.name, template.type, template.is_storable
            )


# Extend product.product to add Shopify fields
class ProductProduct(models.Model):
    _inherit = 'product.product'
