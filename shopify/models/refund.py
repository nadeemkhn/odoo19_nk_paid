# -*- coding: utf-8 -*-
import requests
import logging
import base64
import time
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    shopify_order_id = fields.Char(string="Shopify Order ID")
    shopify_refund_id = fields.Char(string="Shopify Refund ID")
    is_shopify_refunded = fields.Boolean(string="Refunded in Shopify", default=False, readonly=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string="Shopify Instance")

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._assign_shopify_link_from_origin()
        return moves

    def _assign_shopify_link_from_origin(self):
        sale_order_model = self.env["sale.order"]
        for move in self.filtered(
            lambda m: m.move_type in ("out_invoice", "out_refund")
            and (not m.shopify_order_id or not m.shopify_instance_id)
        ):
            vals = {}
            if move.reversed_entry_id:
                if not move.shopify_order_id and move.reversed_entry_id.shopify_order_id:
                    vals["shopify_order_id"] = move.reversed_entry_id.shopify_order_id
                if not move.shopify_instance_id and move.reversed_entry_id.shopify_instance_id:
                    vals["shopify_instance_id"] = move.reversed_entry_id.shopify_instance_id.id

            sale_order = move.invoice_line_ids.mapped("sale_line_ids.order_id").filtered("shopify_order_id")[:1]
            if not sale_order and move.invoice_origin:
                sale_order = sale_order_model.search([
                    ("name", "=", move.invoice_origin),
                    ("shopify_order_id", "!=", False),
                ], limit=1)

            if sale_order:
                if not vals.get("shopify_order_id") and not move.shopify_order_id and sale_order.shopify_order_id:
                    vals["shopify_order_id"] = sale_order.shopify_order_id
                if not vals.get("shopify_instance_id") and not move.shopify_instance_id and sale_order.shopify_instance_id:
                    vals["shopify_instance_id"] = sale_order.shopify_instance_id.id

            if vals:
                move.write(vals)

    # def action_post(self):
    #     res = super().action_post()
    #     for move in self:
    #         if move.move_type == 'out_refund' and move.shopify_order_id:
    #             move.push_refund_to_shopify()
    #     return res

    def push_refund_to_shopify(self):
        self.ensure_one()

        if not self.shopify_order_id:
            _logger.warning(f"Refund {self.name} has no Shopify Order ID.")
            return

        sale_order = self.env['sale.order'].search([
            ('shopify_order_id', '=', str(self.shopify_order_id)),
        ], limit=1)
        instance = self.shopify_instance_id or sale_order.shopify_instance_id
        if not instance:
            instance = self.env['shopify.instance']._resolve_instance(
                order_url=sale_order.shopify_order_url if sale_order else False
            )
        if not instance:
            raise UserError(_("No Shopify instance configured."))

        domain = instance.shopify_domain
        token = instance.shopify_token
        headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        }

        # Step 1: Fetch transactions with retry
        transaction_url = f"https://{domain}/admin/api/2023-01/orders/{self.shopify_order_id}/transactions.json"
        max_retries = 3
        retry_delay = 2
        for attempt in range(max_retries):
            response = requests.get(transaction_url, headers=headers)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', retry_delay))
                _logger.warning(f"⚠️ Rate limit on transaction fetch. Retrying after {retry_after}s (attempt {attempt+1}/{max_retries})")
                time.sleep(retry_after)
                continue
            else:
                break

        if response.status_code != 200:
            raise UserError(_("Failed to fetch Shopify transactions:\n%s" % response.text))

        transactions = response.json().get('transactions', [])
        valid_tx = next((tx for tx in transactions if tx.get('kind') == 'sale' and tx.get('status') == 'success'), None)
        if not valid_tx:
            raise UserError(_("No valid successful transaction found for refund."))

        parent_id = valid_tx.get('id')
        gateway = valid_tx.get('gateway')
        tx_currency = valid_tx.get('currency')

        # Step 2: Get location_id from Shopify
        location_url = f"https://{domain}/admin/api/2023-01/locations.json"
        loc_response = requests.get(location_url, headers=headers)
        if loc_response.status_code != 200:
            raise UserError(_("Failed to fetch Shopify locations:\n%s" % loc_response.text))

        locations = loc_response.json().get("locations", [])
        if not locations:
            raise UserError("No Shopify locations available for restocking.")

        location_id = locations[0]["id"]

        # Step 3: Prepare refund line items (ensuring not over-refunding)
        refund_lines = []
        line_items_url = f"https://{domain}/admin/api/2023-01/orders/{self.shopify_order_id}.json?fields=line_items"
        item_resp = requests.get(line_items_url, headers=headers)
        shopify_line_quantities = {}
        if item_resp.status_code == 200:
            for item in item_resp.json().get("order", {}).get("line_items", []):
                shopify_line_quantities[item["id"]] = item["quantity"]

        for line in self.invoice_line_ids:
            for sale_line in line.sale_line_ids:
                raw_id = sale_line.shopify_line_id
                if not raw_id:
                    continue
                if 'LineItem' not in raw_id:
                    try:
                        raw_id = base64.b64decode(raw_id).decode()
                    except Exception:
                        continue
                if not raw_id.startswith("gid://shopify/LineItem/"):
                    continue
                try:
                    shopify_numeric_id = int(raw_id.split('/')[-1])
                except Exception:
                    continue
                qty = int(abs(line.quantity))
                max_qty = shopify_line_quantities.get(shopify_numeric_id)
                if max_qty is not None and qty > max_qty:
                    qty = max_qty  # prevent over-refund
                if qty == 0:
                    continue
                refund_lines.append({
                    "line_item_id": shopify_numeric_id,
                    "quantity": qty,
                    "restock_type": "return",
                    "location_id": location_id
                })

        if not refund_lines:
            raise UserError("No Shopify line items found to refund.")

        # Step 4: Submit refund with retry logic
        refund_url = f"https://{domain}/admin/api/2023-01/orders/{self.shopify_order_id}/refunds.json"
        payload = {
            "refund": {
                "notify": True,
                "refund_line_items": refund_lines,
                "transactions": [{
                    "kind": "refund",
                    "amount": str(self.amount_total),
                    "currency": tx_currency,
                    "gateway": gateway,
                    "parent_id": parent_id
                }]
            }
        }

        _logger.info(f"Pushing refund to Shopify for order {self.shopify_order_id} with amount {self.amount_total}")

        for attempt in range(max_retries):
            response = requests.post(refund_url, json=payload, headers=headers)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', retry_delay))
                _logger.warning(f"⚠️ Shopify rate limit hit. Retrying after {retry_after}s (attempt {attempt+1}/{max_retries})")
                time.sleep(retry_after)
                continue
            elif response.status_code == 201:
                break
            else:
                break

        if response.status_code == 201:
            data = response.json()
            refund_id = data.get("refund", {}).get("id")
            if refund_id:
                self.shopify_refund_id = str(refund_id)
                self.is_shopify_refunded = True
                _logger.info(f"Refund successful in Shopify for {self.name}, Refund ID: {refund_id}")
            else:
                raise UserError(_("Refund succeeded but no refund ID returned."))
        else:
            raise UserError(_("Shopify Refund Failed:\n%s") % response.text)

    def get_shopify_inventory_item_id(self, domain, token, sku):
        headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        }
        product_url = f"https://{domain}/admin/api/2023-01/products.json?fields=id,variants&limit=250"
        response = requests.get(product_url, headers=headers)
        if response.status_code != 200:
            return None
        products = response.json().get("products", [])
        for product in products:
            for variant in product.get("variants", []):
                if variant.get("sku") == sku:
                    return variant.get("inventory_item_id")
        return None
