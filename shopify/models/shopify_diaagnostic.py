# -*- coding: utf-8 -*-
# models/shopify_diagnostics.py

import logging
import requests
import sys
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ShopifyDiagnostics(models.TransientModel):
    _name = 'shopify.diagnostics'
    _description = 'Comprehensive Shopify Integration Diagnostics'

    result = fields.Html('Diagnostic Results', readonly=True)

    def run_diagnostics(self):
        """Run comprehensive diagnostics on Shopify connection"""
        results = []
        results.append("""
            <style>
                .diag-header { background: #6f42c1; color: white; padding: 20px; border-radius: 5px; margin-bottom: 20px; }
                .diag-section { background: #f8f9fa; padding: 15px; margin: 15px 0; border-radius: 5px; border-left: 4px solid #6f42c1; }
                .success { color: #28a745; font-weight: bold; }
                .warning { color: #ffc107; font-weight: bold; }
                .error { color: #dc3545; font-weight: bold; }
                .info { color: #17a2b8; }
                .code-block { background: #f1f3f5; padding: 10px; border-radius: 3px; font-family: monospace; margin: 5px 0; }
                .stats-box { display: inline-block; background: white; padding: 15px; margin: 10px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); min-width: 200px; }
                .product-item { background: white; padding: 10px; margin: 5px 0; border-radius: 3px; border-left: 3px solid #dee2e6; }
                .product-item.synced { border-left-color: #28a745; }
                .product-item.not-synced { border-left-color: #dc3545; }
                .product-item.partial { border-left-color: #ffc107; }
            </style>
        """)

        results.append("<div class='diag-header'>")
        results.append("<h2>🔍 Shopify Integration - Complete Diagnostics Report</h2>")
        results.append(f"<p>Generated: {fields.Datetime.now()}</p>")
        results.append("</div>")

        instance = self.env['shopify.instance']._get_default_instance()

        # ========== TEST 1: Instance Configuration ==========
        results.append("<div class='diag-section'>")
        results.append("<h3>1️⃣ Shopify Instance Configuration</h3>")

        if not instance:
            results.append("<p class='error'>❌ CRITICAL: No Shopify instance found!</p>")
            results.append("<p>➡️ Go to Shopify → Configuration → Shopify Instance and create one.</p>")
            self.result = ''.join(results)
            return self._return_action()

        results.append(f"<p class='success'>✅ Instance found: <b>{instance.name}</b></p>")

        # Configuration details
        config_items = [
            ("Shopify Domain", instance.shopify_domain, instance.shopify_domain),
            ("Odoo Domain", instance.odoo_domain, instance.odoo_domain),
            ("Access Token", f"{'*' * 20}{instance.shopify_token[-4:] if instance.shopify_token else 'MISSING'}",
             instance.shopify_token),
            ("Location ID",
             instance.shopify_location_id if hasattr(instance, 'shopify_location_id') else "Not configured",
             hasattr(instance, 'shopify_location_id') and instance.shopify_location_id),
            ("Stock Location", instance.stock_location_id.complete_name if hasattr(instance,
                                                                                   'stock_location_id') and instance.stock_location_id else "Not configured",
             hasattr(instance, 'stock_location_id') and instance.stock_location_id),
        ]

        results.append("<table style='width:100%; border-collapse: collapse; margin-top: 10px;'>")
        results.append(
            "<tr style='background: #e9ecef;'><th style='padding: 8px; text-align: left;'>Setting</th><th style='padding: 8px; text-align: left;'>Value</th><th style='padding: 8px; text-align: left;'>Status</th></tr>")

        for label, display_value, check_value in config_items:
            status = "✅" if check_value else "❌"
            status_class = "success" if check_value else "error"
            results.append(
                f"<tr><td style='padding: 8px;'>{label}</td><td style='padding: 8px;'><code>{display_value}</code></td><td style='padding: 8px;' class='{status_class}'>{status}</td></tr>")

        results.append("</table>")
        results.append("</div>")

        # ========== TEST 2: API Connectivity ==========
        results.append("<div class='diag-section'>")
        results.append("<h3>2️⃣ Shopify API Connection Test</h3>")

        headers = {
            'X-Shopify-Access-Token': instance.shopify_token,
            'Content-Type': 'application/json',
        }

        api_success = False
        try:
            test_url = f"https://{instance.shopify_domain}/admin/api/2023-10/shop.json"
            response = requests.get(test_url, headers=headers, timeout=10)

            if response.status_code == 200:
                shop_data = response.json().get('shop', {})
                api_success = True
                results.append(f"<p class='success'>✅ API Connection Successful!</p>")
                results.append("<div class='code-block'>")
                results.append(f"<b>Shop Name:</b> {shop_data.get('name', 'N/A')}<br>")
                results.append(f"<b>Owner:</b> {shop_data.get('shop_owner', 'N/A')}<br>")
                results.append(f"<b>Email:</b> {shop_data.get('email', 'N/A')}<br>")
                results.append(f"<b>Currency:</b> {shop_data.get('currency', 'N/A')}<br>")
                results.append(f"<b>Domain:</b> {shop_data.get('domain', 'N/A')}")
                results.append("</div>")
            elif response.status_code == 401:
                results.append("<p class='error'>❌ Authentication Failed (401)</p>")
                results.append("<p>The Access Token is invalid or expired. Generate a new token in Shopify Admin.</p>")
            elif response.status_code == 403:
                results.append("<p class='error'>❌ Forbidden (403)</p>")
                results.append(
                    "<p>Missing API permissions. Required: read_products, write_products, read_inventory, write_inventory</p>")
            else:
                results.append(f"<p class='error'>❌ API Error: {response.status_code}</p>")
                results.append(f"<div class='code-block'>{response.text}</div>")
        except requests.exceptions.Timeout:
            results.append("<p class='error'>❌ Connection Timeout (>10 seconds)</p>")
        except requests.exceptions.ConnectionError as e:
            results.append("<p class='error'>❌ Connection Error</p>")
            results.append(f"<p>Cannot reach Shopify: {str(e)}</p>")
        except Exception as e:
            results.append(f"<p class='error'>❌ Unexpected Error: {str(e)}</p>")

        results.append("</div>")

        # ========== TEST 3: Location Configuration ==========
        if api_success:
            results.append("<div class='diag-section'>")
            results.append("<h3>3️⃣ Shopify Locations</h3>")

            try:
                locations_url = f"https://{instance.shopify_domain}/admin/api/2023-10/locations.json"
                loc_response = requests.get(locations_url, headers=headers, timeout=10)

                if loc_response.status_code == 200:
                    locations = loc_response.json().get('locations', [])
                    results.append(f"<p class='success'>✅ Found {len(locations)} location(s)</p>")

                    results.append("<table style='width:100%; border-collapse: collapse; margin-top: 10px;'>")
                    results.append(
                        "<tr style='background: #e9ecef;'><th style='padding: 8px; text-align: left;'>Location ID</th><th style='padding: 8px; text-align: left;'>Name</th><th style='padding: 8px; text-align: left;'>Status</th></tr>")

                    for loc in locations:
                        is_configured = hasattr(instance, 'shopify_location_id') and str(loc['id']) == str(
                            instance.shopify_location_id)
                        status = "✅ Configured in Odoo" if is_configured else "Not configured"
                        status_class = "success" if is_configured else "info"
                        results.append(
                            f"<tr><td style='padding: 8px;'><code>{loc['id']}</code></td><td style='padding: 8px;'>{loc.get('name', 'N/A')}</td><td style='padding: 8px;' class='{status_class}'>{status}</td></tr>")

                    results.append("</table>")
                else:
                    results.append(f"<p class='error'>❌ Failed to fetch locations: {loc_response.status_code}</p>")
            except Exception as e:
                results.append(f"<p class='error'>❌ Error fetching locations: {str(e)}</p>")

            results.append("</div>")

        # ========== TEST 4: Product Sync Status ==========
        results.append("<div class='diag-section'>")
        results.append("<h3>4️⃣ Product Sync Status</h3>")

        products = self.env['product.template'].search([('sale_ok', '=', True)])
        total_products = len(products)

        synced_count = 0
        not_synced_count = 0
        missing_data_count = 0
        stock_synced_count = 0

        synced_products = []
        not_synced_products = []
        missing_data_products = []

        for product in products:
            has_shopify_id = bool(product.shopify_product_id)
            missing_data = []

            # Check each variant
            for variant in product.product_variant_ids:
                if not variant.default_code:
                    missing_data.append(f"SKU missing for {variant.display_name}")
                if not variant.barcode:
                    missing_data.append(f"Barcode missing for {variant.display_name}")

            if missing_data:
                missing_data_count += 1
                missing_data_products.append({
                    'name': product.name,
                    'id': product.id,
                    'issues': missing_data
                })
            elif has_shopify_id:
                synced_count += 1
                # Check if variants are linked
                variants_linked = sum(1 for v in product.product_variant_ids if v.shopify_variant_id)
                total_variants = len(product.product_variant_ids)

                synced_products.append({
                    'name': product.name,
                    'id': product.id,
                    'shopify_id': product.shopify_product_id,
                    'variants_linked': f"{variants_linked}/{total_variants}",
                    'has_stock_ids': any(v.shopify_inventory_item_id for v in product.product_variant_ids)
                })

                if any(v.shopify_inventory_item_id for v in product.product_variant_ids):
                    stock_synced_count += 1
            else:
                not_synced_count += 1
                not_synced_products.append({
                    'name': product.name,
                    'id': product.id
                })

        # Statistics boxes
        results.append("<div style='text-align: center; margin: 20px 0;'>")
        results.append(
            f"<div class='stats-box'><h4 style='margin: 0; color: #6c757d;'>Total Products</h4><h2 style='margin: 5px 0;'>{total_products}</h2></div>")
        results.append(
            f"<div class='stats-box' style='border-top: 3px solid #28a745;'><h4 style='margin: 0; color: #28a745;'>Synced</h4><h2 style='margin: 5px 0;'>{synced_count}</h2></div>")
        results.append(
            f"<div class='stats-box' style='border-top: 3px solid #dc3545;'><h4 style='margin: 0; color: #dc3545;'>Not Synced</h4><h2 style='margin: 5px 0;'>{not_synced_count}</h2></div>")
        results.append(
            f"<div class='stats-box' style='border-top: 3px solid #ffc107;'><h4 style='margin: 0; color: #ffc107;'>Missing Data</h4><h2 style='margin: 5px 0;'>{missing_data_count}</h2></div>")
        results.append(
            f"<div class='stats-box' style='border-top: 3px solid #17a2b8;'><h4 style='margin: 0; color: #17a2b8;'>Stock Linked</h4><h2 style='margin: 5px 0;'>{stock_synced_count}</h2></div>")
        results.append("</div>")

        # Synced Products Details
        if synced_products:
            results.append("<h4 class='success'>✅ Synced Products ({}):</h4>".format(len(synced_products)))
            results.append(
                "<div style='max-height: 300px; overflow-y: auto; border: 1px solid #dee2e6; border-radius: 3px; padding: 10px;'>")
            for prod in synced_products[:20]:  # Show first 20
                stock_icon = "📦" if prod['has_stock_ids'] else "📭"
                results.append(f"<div class='product-item synced'>")
                results.append(f"<b>{prod['name']}</b> (ID: {prod['id']})<br>")
                results.append(
                    f"<small>Shopify ID: <code>{prod['shopify_id']}</code> | Variants: {prod['variants_linked']} | Stock: {stock_icon}</small>")
                results.append("</div>")
            if len(synced_products) > 20:
                results.append(f"<p class='info'>... and {len(synced_products) - 20} more</p>")
            results.append("</div>")

        # Not Synced Products
        if not_synced_products:
            results.append("<h4 class='error'>❌ Not Synced Products ({}):</h4>".format(len(not_synced_products)))
            results.append(
                "<div style='max-height: 300px; overflow-y: auto; border: 1px solid #dee2e6; border-radius: 3px; padding: 10px;'>")
            for prod in not_synced_products[:20]:
                results.append(f"<div class='product-item not-synced'>")
                results.append(f"<b>{prod['name']}</b> (ID: {prod['id']})")
                results.append("</div>")
            if len(not_synced_products) > 20:
                results.append(f"<p class='info'>... and {len(not_synced_products) - 20} more</p>")
            results.append("</div>")

        # Missing Data Products
        if missing_data_products:
            results.append(
                "<h4 class='warning'>⚠️ Products with Missing Data ({}):</h4>".format(len(missing_data_products)))
            results.append(
                "<div style='max-height: 300px; overflow-y: auto; border: 1px solid #dee2e6; border-radius: 3px; padding: 10px;'>")
            for prod in missing_data_products[:20]:
                results.append(f"<div class='product-item partial'>")
                results.append(f"<b>{prod['name']}</b> (ID: {prod['id']})<br>")
                results.append("<ul style='margin: 5px 0; padding-left: 20px;'>")
                for issue in prod['issues']:
                    results.append(f"<li><small>{issue}</small></li>")
                results.append("</ul>")
                results.append("</div>")
            if len(missing_data_products) > 20:
                results.append(f"<p class='info'>... and {len(missing_data_products) - 20} more</p>")
            results.append("</div>")

        results.append("</div>")

        # ========== TEST 5: Stock Sync Test ==========
        if api_success and synced_count > 0:
            results.append("<div class='diag-section'>")
            results.append("<h3>5️⃣ Stock Sync Test</h3>")

            # Find a product with inventory_item_id
            test_product_variant = None
            for product in products:
                if product.shopify_product_id:
                    for variant in product.product_variant_ids:
                        if variant.shopify_inventory_item_id and hasattr(instance,
                                                                         'shopify_location_id') and instance.shopify_location_id:
                            test_product_variant = variant
                            break
                if test_product_variant:
                    break

            if test_product_variant:
                try:
                    qty = int(test_product_variant.qty_available)
                    stock_url = f"https://{instance.shopify_domain}/admin/api/2023-10/inventory_levels.json"
                    params = {
                        'inventory_item_ids': test_product_variant.shopify_inventory_item_id,
                        'location_ids': instance.shopify_location_id
                    }

                    stock_response = requests.get(stock_url, headers=headers, params=params, timeout=10)

                    if stock_response.status_code == 200:
                        inventory_levels = stock_response.json().get('inventory_levels', [])
                        if inventory_levels:
                            shopify_qty = inventory_levels[0].get('available', 0)
                            results.append(f"<p class='success'>✅ Stock API Working</p>")
                            results.append("<div class='code-block'>")
                            results.append(f"<b>Test Product:</b> {test_product_variant.display_name}<br>")
                            results.append(f"<b>Odoo Quantity:</b> {qty}<br>")
                            results.append(f"<b>Shopify Quantity:</b> {shopify_qty}<br>")
                            results.append(f"<b>Match:</b> {'✅ Yes' if qty == shopify_qty else '❌ No (sync needed)'}")
                            results.append("</div>")
                        else:
                            results.append("<p class='warning'>⚠️ No inventory levels found for test product</p>")
                    else:
                        results.append(f"<p class='error'>❌ Stock API failed: {stock_response.status_code}</p>")
                except Exception as e:
                    results.append(f"<p class='error'>❌ Stock test error: {str(e)}</p>")
            else:
                results.append(
                    "<p class='info'>ℹ️ No suitable product found for stock test (need synced product with inventory_item_id)</p>")

            results.append("</div>")

        # ========== TEST 6: System Information ==========
        results.append("<div class='diag-section'>")
        results.append("<h3>6️⃣ System Information</h3>")

        results.append("<table style='width:100%;'>")
        results.append(f"<tr><td><b>Database:</b></td><td><code>{self.env.cr.dbname}</code></td></tr>")
        results.append(f"<tr><td><b>Python Version:</b></td><td>{sys.version.split()[0]}</td></tr>")
        results.append(f"<tr><td><b>Requests Library:</b></td><td>{requests.__version__}</td></tr>")
        results.append(f"<tr><td><b>Odoo User:</b></td><td>{self.env.user.name}</td></tr>")
        results.append("</table>")

        results.append("</div>")

        # ========== Final Recommendations ==========
        results.append("<div class='diag-section' style='border-left-color: #17a2b8;'>")
        results.append("<h3>📋 Recommendations</h3>")
        results.append("<ul>")

        if not_synced_count > 0:
            results.append(
                f"<li><b>{not_synced_count} products not synced:</b> Click 'Push Products to Shopify' to sync them</li>")

        if missing_data_count > 0:
            results.append(
                f"<li><b>{missing_data_count} products missing SKU/Barcode:</b> Add missing data before syncing</li>")

        if synced_count > stock_synced_count:
            results.append(
                f"<li><b>{synced_count - stock_synced_count} products without stock links:</b> Re-sync to link inventory items</li>")

        if not (hasattr(instance, 'shopify_location_id') and instance.shopify_location_id):
            results.append(
                "<li><b>Location ID not configured:</b> Set Shopify Location ID in instance settings for stock sync</li>")

        if not api_success:
            results.append("<li class='error'><b>API connection failed:</b> Fix authentication before proceeding</li>")
        else:
            results.append("<li class='success'><b>API connected successfully:</b> Your integration is working!</li>")

        results.append("</ul>")
        results.append("</div>")

        self.result = ''.join(results)
        return self._return_action()

    def _return_action(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'shopify.diagnostics',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
