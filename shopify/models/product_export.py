# -*- coding: utf-8 -*-
import logging
import requests
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'
    shopify_product_id = fields.Char("Shopify Product ID", copy=False)
    shopify_instance_id = fields.Many2one("shopify.instance", string="Shopify Instance", index=True, copy=False)

    def _run_shopify_manual_export(self):
        exporter = self.env['shopify.product.export']
        success = 0
        skipped = 0
        failed = 0

        for template in self:
            try:
                instance = exporter._resolve_instance_for_template(template)
                exported = exporter.push_single_product_to_shopify(template, instance=instance)
                if exported:
                    success += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                instance = template.shopify_instance_id
                exporter._log_sync(
                    operation="Export Product",
                    message=f"Manual export failed for '{template.name}': {exc}",
                    status="failed",
                    level="error",
                    channel="manual",
                    model_name="product.template",
                    res_id=template.id,
                    reference=template.default_code or template.name,
                    instance_id=instance.id if instance else False,
                )

        notif_type = 'danger' if failed else ('warning' if skipped else 'success')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Shopify Export',
                'message': f'Exported: {success}, Skipped: {skipped}, Failed: {failed}',
                'type': notif_type,
                'sticky': False,
            }
        }

    def action_export_to_shopify(self):
        self.ensure_one()
        return self._run_shopify_manual_export()

    def action_bulk_export_to_shopify(self):
        return self._run_shopify_manual_export()

    # Auto-sync disabled - products are exported via scheduled action only
    # @api.model_create_multi
    # def create(self, vals_list):
    #     """Auto-sync to Shopify when product is created (Odoo 17 Flush Fix)"""
    #     templates = super(ProductTemplate, self).create(vals_list)
    #     return templates

    # Auto-sync disabled - products are exported via scheduled action only
    # def write(self, vals):
    #     """Auto-sync to Shopify when product is updated"""
    #     result = super(ProductTemplate, self).write(vals)
    #     return result


class ProductProduct(models.Model):
    _inherit = 'product.product'
    shopify_variant_id = fields.Char("Shopify Variant ID", copy=False)
    shopify_inventory_item_id = fields.Char("Shopify Inventory Item ID", copy=False)
    shopify_instance_id = fields.Many2one(
        "shopify.instance",
        related="product_tmpl_id.shopify_instance_id",
        store=True,
        readonly=False,
    )

    # Auto-sync disabled - products are exported via scheduled action only
    # def write(self, vals):
    #     """Auto-sync to Shopify when variant is updated"""
    #     result = super(ProductProduct, self).write(vals)
    #     return result


class ShopifyProductExport(models.Model):
    _name = 'shopify.product.export'
    _description = 'Push Odoo Products to Shopify'

    def _log_sync(self, **vals):
        try:
            self.env['shopify.sync.log'].log_event(**vals)
        except Exception:
            _logger.debug("Failed to persist sync log", exc_info=True)

    def _get_missing_sku_variants(self, template):
        return template.product_variant_ids.filtered(lambda v: not v.default_code)

    def _get_target_instances(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            instance = self.env["shopify.instance"].browse(instance_id).exists()
            if not instance:
                raise UserError("Selected Shopify instance not found.")
            return instance
        instances = self.env["shopify.instance"].search([("state", "=", "confirm")])
        if not instances:
            instances = self.env["shopify.instance"].search([])
        return instances

    def _resolve_instance_for_template(self, template):
        ctx_instance_id = self.env.context.get("shopify_instance_id")
        if ctx_instance_id:
            instance = self.env["shopify.instance"].browse(ctx_instance_id).exists()
            if not instance:
                raise UserError("Selected Shopify instance not found.")
            return instance
        if template.shopify_instance_id:
            return template.shopify_instance_id
        instances = self._get_target_instances()
        if len(instances) == 1:
            return instances
        raise UserError(
            f"Product '{template.display_name}' has no Shopify Instance. "
            "Set Shopify Instance on product first."
        )

    def push_products_to_shopify(self):
        """Push all saleable products to Shopify - IMMEDIATE EXECUTION"""
        _logger.info("=" * 80)
        _logger.info("🚀 MANUAL SYNC STARTED: Push Products to Shopify button clicked")
        _logger.info("=" * 80)

        instances = self._get_target_instances()
        if not instances:
            _logger.error("❌ CRITICAL: No Shopify instance configured!")
            self._log_sync(
                operation="Export Products",
                message="Product export aborted. No Shopify instance configured.",
                status="failed",
                level="error",
                channel="export",
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': 'No Shopify instance configured. Please configure one first.',
                    'type': 'danger',
                    'sticky': False,
                }
            }
        total = 0
        success = 0
        skipped = 0
        failed = 0

        for instance in instances:
            _logger.info(f"✅ Shopify Instance Found: {instance.name}")
            _logger.info(f"   • Shopify Domain: {instance.shopify_domain}")
            _logger.info(f"   • Odoo Domain: {instance.odoo_domain}")
            _logger.info(f"   • Token Present: {bool(instance.shopify_token)}")

            if self.env.context.get("shopify_instance_id"):
                templates = self.env['product.template'].search([
                    ('sale_ok', '=', True),
                    '|',
                    ('shopify_instance_id', '=', instance.id),
                    ('shopify_instance_id', '=', False),
                ])
            else:
                templates = self.env['product.template'].search([
                    ('sale_ok', '=', True),
                    ('shopify_instance_id', '=', instance.id),
                ])

            total += len(templates)
            _logger.info(f"📊 Found {len(templates)} saleable products to sync for '{instance.name}'")
            _logger.info("=" * 80)

            for idx, template in enumerate(templates, 1):
                try:
                    _logger.info(f"\n{'=' * 80}")
                    _logger.info(
                        f"📦 Processing {idx}/{len(templates)} for '{instance.name}': "
                        f"'{template.name}' (ID: {template.id})"
                    )
                    _logger.info(f"{'=' * 80}")
                    exported = self.push_single_product_to_shopify(template, instance=instance)
                    if exported:
                        success += 1
                        _logger.info(f"✅ SUCCESS: Product {idx}/{len(templates)} synced")
                    else:
                        skipped += 1
                        _logger.info(f"⏭️ SKIPPED: Product {idx}/{len(templates)} not exported")
                except Exception as e:
                    failed += 1
                    _logger.error(f"❌ FAILED: Product {idx}/{len(templates)} - {str(e)}", exc_info=True)
                    self._log_sync(
                        operation="Export Product",
                        message=f"Failed to export product '{template.name}': {e}",
                        status="failed",
                        level="error",
                        channel="export",
                        model_name="product.template",
                        res_id=template.id,
                        reference=template.default_code or template.name,
                        instance_id=instance.id,
                    )

        _logger.info("\n" + "=" * 80)
        _logger.info(f"🏁 SYNC COMPLETE!")
        _logger.info(f"   • Total: {total}")
        _logger.info(f"   • Success: {success}")
        _logger.info(f"   • Skipped: {skipped}")
        _logger.info(f"   • Failed: {failed}")
        _logger.info("=" * 80)
        status = "failed" if failed else ("warning" if skipped else "success")
        level = "error" if failed else ("warning" if skipped else "info")
        self._log_sync(
            operation="Export Products",
            message=f"Product export finished. Total={total}, Success={success}, Skipped={skipped}, Failed={failed}",
            status=status,
            level=level,
            channel="export",
            instance_id=self.env.context.get("shopify_instance_id") or False,
            payload={"total": total, "success": success, "skipped": skipped, "failed": failed},
        )

        notif_type = 'danger' if failed else ('warning' if skipped else 'success')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Complete',
                'message': f'Success: {success}, Skipped: {skipped}, Failed: {failed}',
                'type': notif_type,
                'sticky': False,
            }
        }

    def push_single_product_to_shopify(self, template, instance=None):
        """Push a single product to Shopify - Core sync logic"""
        _logger.info(f"\n🎯 PUSH_SINGLE_PRODUCT_TO_SHOPIFY: Starting for '{template.name}'")
        start_time = fields.Datetime.now()

        instance = instance or self._resolve_instance_for_template(template)
        if not instance:
            raise ValueError("❌ No Shopify instance configured")
        if template.shopify_instance_id and template.shopify_instance_id != instance:
            raise UserError(
                f"Product '{template.display_name}' is linked to instance "
                f"'{template.shopify_instance_id.name}', not '{instance.name}'."
            )
        if not template.shopify_instance_id:
            template.shopify_instance_id = instance.id

        missing_sku_variants = self._get_missing_sku_variants(template)
        if missing_sku_variants:
            missing_names = ", ".join(missing_sku_variants.mapped('display_name'))
            _logger.warning(
                f"⏭️ Skipping '{template.name}' - missing SKU on variants: {missing_names}. "
                "Add SKU(s) first, then sync."
            )
            self._log_sync(
                operation="Export Product",
                message=(
                    f"Skipped product '{template.name}' due to missing SKU on variant(s): {missing_names}."
                ),
                status="warning",
                level="warning",
                channel="export",
                model_name="product.template",
                res_id=template.id,
                reference=template.name,
                instance_id=instance.id,
            )
            return False

        _logger.info(f"📋 Product Details:")
        _logger.info(f"   • Name: {template.name}")
        _logger.info(f"   • ID: {template.id}")
        _logger.info(f"   • Shopify Product ID: {template.shopify_product_id or 'Not linked'}")
        _logger.info(f"   • Variant Count: {len(template.product_variant_ids)}")

        # 1. Validation
        _logger.info(f"\n1️⃣ VALIDATION: Checking variants...")
        for variant in template.product_variant_ids:
            _logger.info(f"   • {variant.display_name}:")
            _logger.info(f"     - SKU: {variant.default_code or '❌ MISSING'}")
            _logger.info(f"     - Barcode: {variant.barcode or '❌ MISSING'}")
            if not variant.default_code:
                _logger.warning(f"⚠️ Variant '{variant.display_name}' missing SKU")
            if not variant.barcode:
                _logger.warning(f"⚠️ Variant '{variant.display_name}' missing barcode")

        headers = {
            'X-Shopify-Access-Token': instance.shopify_token,
            'Content-Type': 'application/json',
        }

        # 2. Check if product exists
        _logger.info(f"\n2️⃣ EXISTENCE CHECK:")
        if not template.shopify_product_id:
            _logger.info("   • No Shopify ID found, searching by SKU...")
            shopify_product = self._find_shopify_product_by_sku(instance, template)
            if shopify_product:
                template.shopify_product_id = str(shopify_product['id'])
                _logger.info(f"   ✅ Found existing product! Linked to Shopify ID: {shopify_product['id']}")
            else:
                _logger.info("   • No existing product found, will create new")
        else:
            _logger.info(f"   • Already linked to Shopify ID: {template.shopify_product_id}")

        # 3. Prepare payload
        _logger.info(f"\n3️⃣ PREPARING PAYLOAD:")
        is_localhost = self._is_localhost(instance.odoo_domain)
        _logger.info(f"   • Environment: {'Localhost/Development' if is_localhost else 'Production'}")

        # Prepare options
        attribute_lines = template.attribute_line_ids
        option_names = [l.attribute_id.name for l in attribute_lines]
        _logger.info(f"   • Options: {option_names or ['Default Title']}")

        # Prepare images
        images_payload = []
        if template.image_1920:
            if not is_localhost:
                main_variant = template.product_variant_id
                image_url = f"{instance.odoo_domain}/shopify/images/{main_variant.id}/{template.name.replace(' ', '-')}.jpg"
                images_payload.append({"src": image_url})
                _logger.info(f"   • Image URL: {image_url}")
            else:
                _logger.info(f"   • Image: Will upload via base64 after creation")
        else:
            _logger.info(f"   • Image: None")

        # Prepare variants
        _logger.info(f"   • Preparing {len(template.product_variant_ids)} variant(s)...")
        variants_payload = []
        for variant in template.product_variant_ids:
            option_values = []
            for line in attribute_lines:
                val = variant.product_template_attribute_value_ids.filtered(
                    lambda x: x.attribute_id == line.attribute_id)
                option_values.append(val.name if val else "")
            if not option_values:
                option_values = ["Default Title"]

            price = float(variant.list_price) if variant.list_price else 0.0
            inventory_qty = int(variant.qty_available) if variant.qty_available >= 0 else 0

            variant_payload = {
                "sku": variant.default_code or "",
                "barcode": str(variant.barcode) if variant.barcode else "",
                "price": f"{price:.2f}",
                "inventory_quantity": inventory_qty,
                "inventory_management": "shopify",
            }

            for i, value in enumerate(option_values[:3]):
                if value:
                    variant_payload[f"option{i + 1}"] = str(value)[:255]

            variants_payload.append(variant_payload)
            _logger.info(f"     - {variant.display_name}: SKU={variant.default_code}, Price={price}")

        # 4. Create or Update
        _logger.info(f"\n4️⃣ API CALL:")
        try:
            if template.shopify_product_id:
                # UPDATE
                _logger.info(f"   📤 UPDATING existing product (ID: {template.shopify_product_id})")

                # CRITICAL: Do NOT include 'id' in update payload - Shopify rejects it
                product_data = {
                    "product": {
                        "title": template.name,
                        "body_html": template.description_sale or '',
                        "variants": variants_payload,
                        "images": images_payload,
                        "options": [{"name": n} for n in option_names] if option_names else [{"name": "Title"}]
                    }
                }

                url = f"https://{instance.shopify_domain}/admin/api/2023-10/products/{template.shopify_product_id}.json"
                _logger.info(f"   🔗 URL: {url}")

                response = requests.put(url, headers=headers, json=product_data, timeout=30)
                _logger.info(f"   📥 Response Status: {response.status_code}")

                if response.status_code == 404:
                    # Product deleted or invalid in Shopify - clear link and search/create
                    _logger.warning(f"   ⚠️ Product {template.shopify_product_id} not found (404). Clearing link and re-syncing...")
                    template.shopify_product_id = False
                    for v in template.product_variant_ids:
                        v.shopify_variant_id = False
                        v.shopify_inventory_item_id = False

                    shopify_product = self._find_shopify_product_by_sku(instance, template)
                    if shopify_product:
                        template.shopify_product_id = str(shopify_product['id'])
                        _logger.info(f"   ✅ Found by SKU! Re-linking to Shopify ID: {shopify_product['id']}")
                        # Retry update with new ID
                        url = f"https://{instance.shopify_domain}/admin/api/2023-10/products/{template.shopify_product_id}.json"
                        response = requests.put(url, headers=headers, json=product_data, timeout=30)
                        response.raise_for_status()
                        shopify_product = response.json().get('product')
                        _logger.info(f"   ✅ UPDATE SUCCESSFUL!")
                    else:
                        _logger.info(f"   📤 Product not found by SKU - creating new...")
                        product_data["product"]["status"] = "draft"
                        response = requests.post(
                            f"https://{instance.shopify_domain}/admin/api/2023-10/products.json",
                            headers=headers, json=product_data, timeout=30
                        )
                        response.raise_for_status()
                        shopify_product = response.json().get('product')
                        template.shopify_product_id = str(shopify_product['id'])
                        _logger.info(f"   ✅ CREATE SUCCESSFUL! New Shopify ID: {shopify_product['id']}")
                else:
                    response.raise_for_status()
                    shopify_product = response.json().get('product')
                    _logger.info(f"   ✅ UPDATE SUCCESSFUL!")
            else:
                # CREATE
                _logger.info(f"   📤 CREATING new product")
                product_data = {
                    "product": {
                        "title": template.name,
                        "body_html": template.description_sale or '',
                        "variants": variants_payload,
                        "images": images_payload,
                        "options": [{"name": n} for n in option_names] if option_names else [{"name": "Title"}],
                        "status": "draft"
                    }
                }

                url = f"https://{instance.shopify_domain}/admin/api/2023-10/products.json"
                _logger.info(f"   🔗 URL: {url}")
                _logger.info(f"   📦 Payload: {product_data}")

                response = requests.post(url, headers=headers, json=product_data, timeout=30)
                _logger.info(f"   📥 Response Status: {response.status_code}")
                _logger.info(f"   📥 Response Body: {response.text[:500]}")

                response.raise_for_status()
                shopify_product = response.json().get('product')

                if shopify_product:
                    template.shopify_product_id = str(shopify_product['id'])
                    _logger.info(f"   ✅ CREATE SUCCESSFUL! Shopify ID: {shopify_product['id']}")

            # 5. Post-processing
            if shopify_product:
                _logger.info(f"\n5️⃣ POST-PROCESSING:")

                # Upload base64 image for localhost
                if template.image_1920 and is_localhost:
                    try:
                        _logger.info("   🖼️ Uploading image via base64...")
                        self._upload_base64_image_to_shopify(instance, shopify_product['id'], template)
                    except Exception as img_error:
                        _logger.warning(f"   ⚠️ Image upload failed: {img_error}")

                # Link variants
                _logger.info("   🔗 Linking variants...")
                self._link_variants_and_update_cost(instance, template, shopify_product, headers)
                _logger.info("   ✅ Variants linked successfully")

            _logger.info(f"\n✅ PUSH_SINGLE_PRODUCT_TO_SHOPIFY COMPLETE for '{template.name}'")
            duration_ms = (fields.Datetime.now() - start_time).total_seconds() * 1000.0
            self._log_sync(
                operation="Export Product",
                message=f"Product '{template.name}' synced successfully.",
                status="success",
                level="info",
                channel="export",
                model_name="product.template",
                res_id=template.id,
                reference=template.shopify_product_id or template.name,
                instance_id=instance.id,
                duration_ms=duration_ms,
            )
            return True

        except requests.exceptions.Timeout:
            _logger.error(f"   ❌ TIMEOUT: Shopify took too long to respond (>30s)")
            self._log_sync(
                operation="Export Product",
                message=f"Timeout while syncing product '{template.name}'.",
                status="failed",
                level="error",
                channel="export",
                model_name="product.template",
                res_id=template.id,
                reference=template.name,
                instance_id=instance.id,
            )
            raise
        except requests.exceptions.RequestException as e:
            _logger.error(f"   ❌ API REQUEST FAILED: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    _logger.error(f"   ❌ Shopify Error Details: {error_details}")
                except:
                    _logger.error(f"   ❌ Shopify Response Text: {e.response.text}")
            self._log_sync(
                operation="Export Product",
                message=f"API error while syncing product '{template.name}': {e}",
                status="failed",
                level="error",
                channel="export",
                model_name="product.template",
                res_id=template.id,
                reference=template.name,
                instance_id=instance.id,
                response=getattr(e.response, "text", False) if hasattr(e, "response") else False,
            )
            raise

    def _find_shopify_product_by_sku(self, instance, template):
        """Find existing Shopify product by matching ALL variant SKUs"""
        _logger.info(f"   🔍 _find_shopify_product_by_sku: Searching for '{template.name}'")

        headers = {
            'X-Shopify-Access-Token': instance.shopify_token,
            'Content-Type': 'application/json',
        }

        odoo_skus = [v.default_code for v in template.product_variant_ids if v.default_code]
        _logger.info(f"   • Odoo SKUs to match: {odoo_skus}")

        if not odoo_skus:
            _logger.info("   • No SKUs found, cannot search")
            return None

        shopify_sku_map = {}
        try:
            # Fetch active + draft products (status=any may not be supported in all API versions)
            for status_filter in ['active', 'draft', 'archived']:
                url = f"https://{instance.shopify_domain}/admin/api/2023-10/products.json?limit=250&status={status_filter}&fields=id,variants"
                page_count = 0
                max_pages = 20

                _logger.info(f"   🌐 Fetching from Shopify (status={status_filter}): {url}")

                while url and page_count < max_pages:
                    page_count += 1
                    _logger.info(f"   📄 Page {page_count} (status={status_filter})...")

                    response = requests.get(url, headers=headers, timeout=30)
                    response.raise_for_status()

                    data = response.json()
                    products = data.get("products") or []
                    if not products:
                        _logger.warning(f"   • No products on page {page_count}, stopping")
                        _logger.warning(f"   • DEBUG: Response keys: {list(data.keys())}")
                        _logger.warning(f"   • DEBUG: Full response (first 800 chars): {str(data)[:800]}")
                        break

                    for prod in products:
                        for variant in prod.get("variants", []):
                            sku = variant.get("sku")
                            if sku:
                                shopify_sku_map[sku] = prod

                    link_header = response.headers.get('Link')
                    url = None
                    if link_header and 'rel="next"' in link_header:
                        links = link_header.split(',')
                        for link in links:
                            if 'rel="next"' in link:
                                url = link.split(';')[0].strip('<> ')
                                break

            _logger.info(f"   ✅ Cached {len(shopify_sku_map)} Shopify SKUs")

        except Exception as e:
            _logger.error(f"   ❌ Error fetching Shopify products: {str(e)}")
            return None

        # Match SKUs
        shopify_product_ids = set()
        matched_skus = []

        for sku in odoo_skus:
            if sku in shopify_sku_map:
                shopify_product_ids.add(shopify_sku_map[sku]['id'])
                matched_skus.append(sku)

        _logger.info(f"   • Matched SKUs: {matched_skus}")
        _logger.info(f"   • Unique Shopify Product IDs: {shopify_product_ids}")

        # All Odoo SKUs must be found in the same Shopify product
        if len(matched_skus) == len(odoo_skus) and len(shopify_product_ids) == 1:
            shopify_product = shopify_sku_map[matched_skus[0]]
            _logger.info(f"   ✅ Match found! Shopify Product ID: {shopify_product['id']}")
            return shopify_product

        _logger.info("   • No matching product found")
        return None

    def _link_variants_and_update_cost(self, instance, template, shopify_product, headers):
        """Link Odoo variants to Shopify variants and update cost + STOCK"""
        _logger.info("   🔗 Linking variants...")

        for odoo_variant in template.product_variant_ids:
            for shopify_variant in shopify_product.get('variants', []):
                # Match by SKU (primary) or barcode (fallback)
                sku_match = odoo_variant.default_code and odoo_variant.default_code == shopify_variant.get('sku')
                barcode_match = odoo_variant.barcode and str(odoo_variant.barcode) == str(
                    shopify_variant.get('barcode'))

                if sku_match or barcode_match:
                    # Save IDs
                    odoo_variant.shopify_variant_id = str(shopify_variant['id'])
                    inventory_item_id = shopify_variant.get('inventory_item_id')

                    if inventory_item_id:
                        odoo_variant.shopify_inventory_item_id = str(inventory_item_id)
                        _logger.info(f"     ✅ Linked: {odoo_variant.display_name}")
                        _logger.info(f"        → Variant ID: {shopify_variant['id']}")
                        _logger.info(f"        → Inventory Item ID: {inventory_item_id}")
                    else:
                        _logger.warning(f"     ⚠️ No inventory_item_id for {odoo_variant.display_name}")
                        break

                    # Update cost price
                    if odoo_variant.standard_price:
                        try:
                            cost_price = float(odoo_variant.standard_price)
                            inventory_url = f"https://{instance.shopify_domain}/admin/api/2023-10/inventory_items/{inventory_item_id}.json"
                            inventory_payload = {
                                "inventory_item": {
                                    "id": int(inventory_item_id),
                                    "cost": cost_price
                                }
                            }

                            cost_response = requests.put(inventory_url, headers=headers, json=inventory_payload,
                                                         timeout=10)
                            cost_response.raise_for_status()
                            _logger.info(f"     💰 Cost updated: ${cost_price}")
                        except Exception as e:
                            _logger.warning(f"     ⚠️ Cost update failed: {e}")

                    # Update stock quantity - CRITICAL FIX
                    if instance.shopify_location_id:
                        try:
                            # Get quantity from the configured stock location
                            if instance.stock_location_id:
                                qty = int(
                                    odoo_variant.with_context(location=instance.stock_location_id.id).qty_available)
                            else:
                                qty = int(odoo_variant.qty_available)

                            stock_url = f"https://{instance.shopify_domain}/admin/api/2023-10/inventory_levels/set.json"
                            stock_payload = {
                                "location_id": int(instance.shopify_location_id),
                                "inventory_item_id": int(inventory_item_id),
                                "available": qty
                            }

                            _logger.info(f"     📦 Updating stock:")
                            _logger.info(f"        → Location ID: {instance.shopify_location_id}")
                            _logger.info(f"        → Inventory Item ID: {inventory_item_id}")
                            _logger.info(f"        → Quantity: {qty}")

                            stock_response = requests.post(stock_url, headers=headers, json=stock_payload, timeout=10)
                            stock_response.raise_for_status()
                            _logger.info(f"     ✅ Stock updated: {qty} units")
                        except requests.exceptions.HTTPError as e:
                            _logger.error(f"     ❌ Stock update HTTP error: {e}")
                            if hasattr(e, 'response') and e.response is not None:
                                _logger.error(f"     ❌ Response: {e.response.text}")
                        except Exception as e:
                            _logger.error(f"     ❌ Stock update failed: {e}")
                    else:
                        _logger.warning(f"     ⚠️ No shopify_location_id configured - cannot update stock")

                    break

    def _upload_base64_image_to_shopify(self, instance, shopify_product_id, template):
        """Upload product image using base64 encoding"""
        if not template.image_1920:
            return

        headers = {
            'X-Shopify-Access-Token': instance.shopify_token,
            'Content-Type': 'application/json',
        }

        try:
            image_data = template.image_1920.decode('utf-8')

            image_payload = {
                "image": {
                    "attachment": image_data,
                    "filename": f"{template.name.replace(' ', '_')}.jpg"
                }
            }

            url = f"https://{instance.shopify_domain}/admin/api/2023-10/products/{shopify_product_id}/images.json"
            response = requests.post(url, headers=headers, json=image_payload, timeout=30)
            response.raise_for_status()

            image_result = response.json().get('image', {})
            _logger.info(f"   ✅ Image uploaded - Image ID: {image_result.get('id')}")

        except Exception as e:
            _logger.error(f"   ❌ Image upload failed: {e}")
            raise

    def _is_localhost(self, domain):
        """Check if running on localhost/development"""
        return (
                'localhost' in domain or
                '127.0.0.1' in domain or
                'ngrok' in domain or
                domain.startswith('http://') or
                not domain.startswith('https://')
        )
