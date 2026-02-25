# -*- coding: utf-8 -*-
import requests
import logging
import time
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class ShopifyStockExport(models.Model):
    _name = 'shopify.stock.export'
    _description = 'Export Odoo Product Stock to Shopify'

    def _log_sync(self, **vals):
        try:
            self.env['shopify.sync.log'].log_event(**vals)
        except Exception:
            _logger.debug("Failed to persist sync log", exc_info=True)

    def _get_target_instances(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            instance = self.env["shopify.instance"].browse(instance_id).exists()
            return instance
        instances = self.env["shopify.instance"].search([("state", "=", "confirm")])
        if not instances:
            instances = self.env["shopify.instance"].search([])
        return instances

    def export_stock_to_shopify(self):
        """Export stock levels to Shopify - IMMEDIATE EXECUTION"""
        instances = self._get_target_instances()
        if not instances:
            _logger.error("❌ No Shopify instance configured.")
            self._log_sync(
                operation="Export Stock",
                message="Stock export aborted. No Shopify instance configured.",
                status="failed",
                level="error",
                channel="export",
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': 'No Shopify instance configured.',
                    'type': 'danger',
                    'sticky': False,
                }
            }

        grand_total_candidates = 0
        grand_total = 0
        grand_skipped_unlinked = 0
        grand_success = 0
        grand_failed = 0

        for instance in instances:
            headers = {
                "X-Shopify-Access-Token": instance.shopify_token,
                "Content-Type": "application/json"
            }

            candidate_products = self.env['product.product'].search([
                ('default_code', '!=', False),
                ('is_storable', '=', True),
                ('shopify_instance_id', '=', instance.id),
            ])
            products = candidate_products.filtered(lambda p: bool(p.shopify_inventory_item_id))

            total_candidates = len(candidate_products)
            total = len(products)
            skipped_unlinked = total_candidates - total
            success = 0
            failed = 0

            grand_total_candidates += total_candidates
            grand_total += total
            grand_skipped_unlinked += skipped_unlinked

            _logger.info(
                "🚀 Starting stock export for '%s'. Candidates=%s, Linked=%s, Skipped(unlinked)=%s",
                instance.name, total_candidates, total, skipped_unlinked
            )

            for idx, product in enumerate(products, 1):
                try:
                    _logger.info(f"📦 Processing stock {idx}/{total}: '{product.display_name}'")

                    qty = product.with_context(location=instance.stock_location_id.id).qty_available
                    payload = {
                        "location_id": instance.shopify_location_id,
                        "inventory_item_id": int(product.shopify_inventory_item_id),
                        "available": int(qty)
                    }
                    url = f"https://{instance.shopify_domain}/admin/api/2023-10/inventory_levels/set.json"
                    retry_count = 0
                    max_retries = 3

                    while retry_count < max_retries:
                        try:
                            response = requests.post(url, headers=headers, json=payload, timeout=10)

                            if response.status_code == 200:
                                _logger.info(f"✅ Stock updated for '{product.display_name}': {qty} units")
                                success += 1
                                break
                            elif response.status_code == 429:
                                retry_count += 1
                                wait_time = 2 * retry_count
                                _logger.warning(
                                    f"⚠️ Rate limit hit for '{product.display_name}'. Waiting {wait_time}s..."
                                )
                                time.sleep(wait_time)
                            else:
                                _logger.error(f"❌ Failed to update stock for '{product.display_name}': {response.text}")
                                failed += 1
                                break

                        except requests.exceptions.Timeout:
                            retry_count += 1
                            if retry_count < max_retries:
                                _logger.warning(
                                    f"⚠️ Timeout for '{product.display_name}'. Retry {retry_count}/{max_retries}..."
                                )
                                time.sleep(1)
                            else:
                                _logger.error(f"❌ Timeout after {max_retries} retries for '{product.display_name}'")
                                failed += 1
                                break
                        except requests.exceptions.RequestException as e:
                            _logger.error(f"❌ Request error for '{product.display_name}': {e}")
                            failed += 1
                            break
                    time.sleep(0.5)

                except Exception as e:
                    failed += 1
                    _logger.exception(f"❌ Unexpected error for '{product.display_name}': {e}")

            grand_success += success
            grand_failed += failed

            self._log_sync(
                operation="Export Stock",
                message=(
                    f"[{instance.name}] Stock export finished. Candidates={total_candidates}, Linked={total}, "
                    f"Skipped(unlinked)={skipped_unlinked}, Success={success}, Failed={failed}"
                ),
                status="success" if failed == 0 else "warning",
                level="info" if failed == 0 else "warning",
                channel="export",
                instance_id=instance.id,
                payload={
                    "candidates": total_candidates,
                    "linked": total,
                    "skipped_unlinked": skipped_unlinked,
                    "success": success,
                    "failed": failed,
                },
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Stock Export Complete',
                'message': (
                    f'Updated: {grand_success}, Failed: {grand_failed}, '
                    f'Skipped (not linked): {grand_skipped_unlinked}'
                ),
                'type': 'success' if grand_failed == 0 else 'warning',
                'sticky': False,
            }
        }


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        """Auto-update stock in Shopify when qty_available changes"""
        result = super(ProductProduct, self).write(vals)

        # Check if quantity changed
        if 'qty_available' in vals or any(field in vals for field in ['stock_move_ids', 'stock_quant_ids']):
            for product in self:
                if product.shopify_inventory_item_id and product.default_code:
                    try:
                        self._update_shopify_stock_single(product)
                    except Exception as e:
                        _logger.warning(f"⚠️ Auto-stock update failed for '{product.display_name}': {e}")

        return result

    def _update_shopify_stock_single(self, product):
        """Update stock for a single product in Shopify"""
        instance = product.shopify_instance_id or self.env['shopify.instance']._get_default_instance()
        if not instance:
            return

        headers = {
            "X-Shopify-Access-Token": instance.shopify_token,
            "Content-Type": "application/json"
        }

        qty = product.with_context(location=instance.stock_location_id.id).qty_available

        payload = {
            "location_id": instance.shopify_location_id,
            "inventory_item_id": int(product.shopify_inventory_item_id),
            "available": int(qty)
        }

        url = f"https://{instance.shopify_domain}/admin/api/2023-10/inventory_levels/set.json"

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            _logger.info(f"✅ Auto-updated stock for '{product.display_name}': {qty} units")
        except Exception as e:
            _logger.warning(f"⚠️ Stock update failed for '{product.display_name}': {e}")
            raise
