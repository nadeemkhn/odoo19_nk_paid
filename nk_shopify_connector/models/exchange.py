import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    shopify_exchange_synced = fields.Boolean(string="Exchange Synced", default=False)

    def action_sync_shopify_exchange(self):
        self.ensure_one()

        if not self.shopify_order_id:
            raise UserError(_("No Shopify Order ID on this Sale Order."))

        instance = self.shopify_instance_id or self.env['shopify.instance']._resolve_instance(
            order_url=self.shopify_order_url
        )
        if not instance:
            raise UserError(_("No Shopify instance configured."))

        url = f"https://{instance.shopify_domain}/admin/api/2023-10/orders/{self.shopify_order_id}.json"
        headers = {
            "X-Shopify-Access-Token": instance.shopify_token,
            "Content-Type": "application/json"
        }
        response = requests.get(url, headers=headers)
        _logger.warning("Shopify Return Response Text: %s", response.text)  # 👈 ADD THIS
        returns_data = response.json().get("returns", [])

        if not returns_data:
            raise UserError(_("No exchange data found in Shopify."))

        pickings = self.picking_ids.filtered(lambda p: p.state == 'done' and p.picking_type_id.code == 'outgoing')
        if not pickings:
            raise UserError(_("No delivered picking found."))

        created = 0
        for return_data in returns_data:
            for line in return_data.get("return_line_items", {}).get("removals", []):
                delta_qty = line.get("delta")
                product_info = line.get("line_item", {})

                barcode = product_info.get("barcode")
                sku = product_info.get("sku")

                product = self.env['product.product'].search(
                    ['|', ('barcode', '=', barcode), ('default_code', '=', sku)], limit=1)
                if not product:
                    continue

                for picking in pickings:
                    move_line = picking.move_ids_without_package.filtered(lambda m: m.product_id == product)
                    if not move_line:
                        continue

                    # --- Create Return Picking ---
                    return_wizard = self.env['stock.return.picking'].with_context(active_id=picking.id).create({
                        'picking_id': picking.id,
                        'product_return_moves': [(0, 0, {
                            'product_id': product.id,
                            'quantity': delta_qty,
                            'move_id': move_line[0].id,
                        })]
                    })
                    result = return_wizard.create_returns()
                    return_picking = self.env['stock.picking'].browse(result.get('res_id'))
                    return_picking.action_confirm()
                    return_picking.action_assign()
                    for ml in return_picking.move_ids_without_package:
                        ml.quantity_done = ml.product_uom_qty
                    return_picking.button_validate()

                    # --- Create Exchange Picking ---
                    exchange_picking = self.env['stock.picking'].create({
                        'partner_id': self.partner_id.id,
                        'picking_type_id': picking.picking_type_id.id,
                        'origin': self.name + ' (Exchange)',
                        'location_id': picking.location_dest_id.id,
                        'location_dest_id': picking.location_id.id,
                        'move_ids_without_package': [(0, 0, {
                            'name': product.name,
                            'product_id': product.id,
                            'product_uom_qty': delta_qty,
                            'product_uom': product.uom_id.id,
                            'location_id': picking.location_dest_id.id,
                            'location_dest_id': picking.location_id.id,
                        })]
                    })
                    exchange_picking.action_confirm()
                    exchange_picking.action_assign()
                    for ml in exchange_picking.move_ids_without_package:
                        ml.quantity_done = ml.product_uom_qty
                    exchange_picking.button_validate()

                    # --- Create Invoice for Exchanged Item ---
                    invoice = self._create_exchange_invoice(product, delta_qty)
                    invoice.action_post()

                    created += 1

        if created:
            self.shopify_exchange_synced = True
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': _('Exchange and invoice synced successfully from Shopify!'),
                    'type': 'success'
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': _('No valid exchange found or already synced.'),
                    'type': 'warning'
                }
            }

    # def _create_exchange_invoice(self, product, quantity):
    #     invoice_vals = {
    #         'move_type': 'out_invoice',
    #         'partner_id': self.partner_id.id,
    #         'invoice_origin': self.name,
    #         'invoice_line_ids': [(0, 0, {
    #             'product_id': product.id,
    #             'quantity': quantity,
    #             'price_unit': product.lst_price,
    #             'name': f"Exchange for {self.name}",
    #         })],
    #     }
    #     return self.env['account.move'].create(invoice_vals)
