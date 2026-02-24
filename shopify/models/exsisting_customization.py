from odoo import api, fields, models, _
import requests
import logging
from datetime import datetime
from odoo.exceptions import UserError

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    _inherit = 'product.product'
    shopify_product_id = fields.Char("Shopify Product ID", index=True)


class StockMove(models.Model):
    _inherit = 'stock.move'

    sale_line_id = fields.Many2one('sale.order.line', string="Sales Order Line", index=True)


class AccountMove(models.Model):
    _inherit = 'account.move'

    shopify_order_id = fields.Char("Shopify Order ID")
    tracking_number = fields.Char(string="Tracking Number")
    delivery_partner_id = fields.Many2one('res.partner', string="Delivery Partner")
    shopify_refund_id = fields.Char("Shopify Refund ID", index=True)

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._assign_shopify_links_from_source()
        return moves

    def _assign_shopify_links_from_source(self):
        sale_order_model = self.env["sale.order"]
        has_instance_field = "shopify_instance_id" in self._fields
        for move in self.filtered(
            lambda m: m.move_type in ("out_invoice", "out_refund") and not m.shopify_order_id
        ):
            shopify_order_id = False
            instance_id = False

            if move.reversed_entry_id and move.reversed_entry_id.shopify_order_id:
                shopify_order_id = move.reversed_entry_id.shopify_order_id
                if (
                    has_instance_field
                    and "shopify_instance_id" in move.reversed_entry_id._fields
                    and move.reversed_entry_id.shopify_instance_id
                ):
                    instance_id = move.reversed_entry_id.shopify_instance_id.id

            if not shopify_order_id:
                sale_order = move.invoice_line_ids.mapped("sale_line_ids.order_id").filtered("shopify_order_id")[:1]
                if not sale_order and move.invoice_origin:
                    sale_order = sale_order_model.search([
                        ("name", "=", move.invoice_origin),
                        ("shopify_order_id", "!=", False),
                    ], limit=1)
                if sale_order:
                    shopify_order_id = sale_order.shopify_order_id
                    if has_instance_field and sale_order.shopify_instance_id:
                        instance_id = sale_order.shopify_instance_id.id

            vals = {}
            if shopify_order_id:
                vals["shopify_order_id"] = str(shopify_order_id)
            if has_instance_field and instance_id and not getattr(move, "shopify_instance_id", False):
                vals["shopify_instance_id"] = instance_id
            if vals:
                move.write(vals)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'
    shopify_line_id = fields.Char(string='shopify_line_id', index=True)


class StockPicking(models.Model):
    _inherit = 'stock.picking'
    shopify_return_id = fields.Char('Shopify Return ID', index=True)



class SaleOrders(models.Model):
    _inherit = 'sale.order'

    _order = 'origin desc'

    shopify_order_id = fields.Char(string="Shopify Order ID", index=True)
    tracking_number = fields.Char(string="Tracking Number")
    delivery_partner_id = fields.Many2one('res.partner', string="Delivery Partner")
    shopify_payment_status = fields.Char(string="Shopify Payment Status")
    shopify_order_url = fields.Char(string="Shopify Order URL")
    shopify_processed_return_ids = fields.Text(string="Shopify Processed Return IDs", default='[]', copy=False,
                                               help="JSON list of Shopify return IDs processed from orders/updated webhook.")

    def _auto_pay_refund_credit_note(self, refund_move):
        """Auto register payment so refund credit note moves to Paid."""
        refund_move.ensure_one()
        if refund_move.move_type != 'out_refund' or refund_move.state != 'posted':
            return
        if refund_move.payment_state == 'paid':
            return
        residual = abs(refund_move.amount_residual)
        if not residual:
            return

        journal = self.env['account.journal'].search([
            ('type', 'in', ['bank', 'cash']),
            ('company_id', '=', refund_move.company_id.id),
        ], limit=1)
        if not journal:
            _logger.warning(
                "No bank/cash journal found for auto-paying refund %s (company: %s).",
                refund_move.name, refund_move.company_id.display_name
            )
            return

        ctx = {
            'active_model': 'account.move',
            'active_ids': refund_move.ids,
            'active_id': refund_move.id,
        }
        pay_wizard = self.env['account.payment.register'].with_context(ctx).create({
            'journal_id': journal.id,
            'amount': residual,
            'payment_date': fields.Date.context_today(refund_move),
        })
        pay_wizard.action_create_payments()
        _logger.info("Auto payment registered for refund %s. New payment state: %s", refund_move.name, refund_move.payment_state)

    def action_update_shipping_from_shopify(self):
        """Action method to manually update shipping charges from Shopify"""
        for order in self:
            if order.shopify_order_id:
                _logger.info(f"🔄 Manually updating shipping charges for order {order.name}")
                result = self.env['shopify.sale.paid.order'].force_update_shipping_charges(order.shopify_order_id)
                if result:
                    _logger.info(f"✅ Successfully updated shipping charges for order {order.name}")
                else:
                    _logger.error(f"❌ Failed to update shipping charges for order {order.name}")
            else:
                _logger.warning(f"⚠️ Order {order.name} has no Shopify order ID")

    # def create_standard_return_picking(self, delivery_picking, returned_line_items):
    #     """
    #     Creates a standard Odoo return picking for the given delivered picking, for the given returned Shopify line items.
    #     Returns the new return picking (stock.picking recordset) or None.
    #     """
    #     move_lines = delivery_picking.move_ids_without_package or delivery_picking.move_ids
    #     to_return = []
    #     for item in returned_line_items:
    #         shopify_line_id = str(item.get('line_item_id'))
    #         qty = item.get('quantity', 0)
    #         move = move_lines.filtered(lambda m: m.sale_line_id and m.sale_line_id.shopify_line_id == shopify_line_id)
    #         if move:
    #             move = move[0]
    #             to_return.append({'move_id': move.id, 'quantity': qty})
    #
    #     if not to_return:
    #         _logger.warning(f"No moves found for return (Shopify).")
    #         return None
    #
    #     return_wiz = self.env['stock.return.picking'].with_context(
    #         active_id=delivery_picking.id, active_ids=[delivery_picking.id]
    #     ).create({'picking_id': delivery_picking.id})
    #
    #     # Set correct quantity for each line
    #     for wiz_line in return_wiz.product_return_moves:
    #         move_dict = next((x for x in to_return if x['move_id'] == wiz_line.move_id.id), None)
    #         if move_dict:
    #             wiz_line.quantity = move_dict['quantity']
    #         else:
    #             wiz_line.quantity = 0
    #
    #     # Create the return picking
    #     result = return_wiz.create_returns()
    #     new_picking = None
    #     if isinstance(result, dict) and result.get('res_id'):
    #         new_picking = self.env['stock.picking'].sudo().browse(result['res_id'])
    #     _logger.info(
    #         f"Standard return picking created: {getattr(new_picking, 'name', result)} for original: {delivery_picking.name}")
    #     return new_picking

    def create_odoo_refund(self, refund_amount, refund_id, refund_line_items=None):
        invoices = self.invoice_ids.filtered(lambda inv: inv.move_type == 'out_invoice' and inv.state == 'posted')
        for invoice in invoices:
            # Check for duplicate refund
            existing_refund = self.env['account.move'].sudo().search([
                ('shopify_refund_id', '=', refund_id),
                ('move_type', '=', 'out_refund'),
            ], limit=1)
            if existing_refund:
                _logger.info("Refund already exists for Shopify refund ID: %s", refund_id)
                return

            # Create Refund (credit note)
            defaults = {
                'ref': f"Shopify Refund ID: {refund_id}",
                'shopify_refund_id': refund_id,
            }
            refund_moves = invoice._reverse_moves(default_values_list=[defaults])

            refund = False
            if isinstance(refund_moves, dict):
                refund = refund_moves.get(invoice.id)
            elif hasattr(refund_moves, 'id'):
                refund = refund_moves
            if not refund:
                _logger.error("Could not create refund move for invoice %s", invoice.id)
                continue

            refund = refund.ensure_one()

            # --- Partial refund lines ---
            if refund_line_items:
                allowed_line_ids = [str(item.get('line_item_id')) for item in refund_line_items]
                for line in refund.invoice_line_ids:
                    if line.shopify_line_id not in allowed_line_ids:
                        line.unlink()
                    else:
                        item = next(
                            (x for x in refund_line_items if str(x.get('line_item_id')) == line.shopify_line_id), None)
                        if item:
                            line.quantity = item.get('quantity', 1)

            if refund.state != 'posted':
                refund.action_post()

            _logger.info("Refund created and posted in Odoo for Shopify refund ID: %s", refund_id)

            # Auto-pay refund credit note so payment state is "Paid".
            try:
                self._auto_pay_refund_credit_note(refund)
            except Exception as payment_err:
                _logger.error(
                    "Auto payment failed for refund %s (Shopify Refund ID: %s): %s",
                    refund.name, refund_id, payment_err, exc_info=True
                )

            # --- Stock Return Picking Creation ---
            # try:
            #     # 1. Find the relevant picking (delivery)
            #     delivery_picking = self.picking_ids.filtered(
            #         lambda p: p.picking_type_code == 'outgoing' and p.state == 'done')
            #     if not delivery_picking:
            #         _logger.warning(f"No delivered picking found for order {self.name}, skipping stock return.")
            #         continue
            #
            #     delivery_picking = delivery_picking[0].ensure_one()
            #
            #     # 2. Create the return wizard (standard Odoo way)
            #     return_wiz = self.env['stock.return.picking'].with_context(
            #         active_id=delivery_picking.id, active_ids=[delivery_picking.id]
            #     ).create({'picking_id': delivery_picking.id})
            #
            #     # 3. Adjust wizard lines for Shopify refund items
            #     for line in return_wiz.product_return_moves:
            #         # Find if this line matches any Shopify return line
            #         found = False
            #         for refund_item in refund_line_items or []:
            #             shopify_line_id = str(refund_item.get('line_item_id'))
            #             qty = refund_item.get('quantity', 1)
            #             if (line.move_id.sale_line_id and
            #                     line.move_id.sale_line_id.shopify_line_id == shopify_line_id):
            #                 line.quantity = qty  # Set quantity to refund quantity
            #                 found = True
            #                 break
            #         if not found:
            #             line.quantity = 0  # Do not return this line
            #
            #     # 4. Create the return picking using Odoo standard process
            #     result = return_wiz.create_returns()
            #     _logger.info(f"Odoo standard return picking created for Shopify refund ID: {refund_id}")
            #
            #     # 5. (Optional) Auto-validate new picking
            #     if result and isinstance(result, dict):
            #         new_picking_id = result.get('res_id')
            #         if new_picking_id:
            #             new_picking = self.env['stock.picking'].browse(new_picking_id)
            #             if new_picking.state not in ('done', 'cancel'):
            #                 new_picking.button_validate()
            #                 _logger.info(f"Auto-validated return picking {new_picking.name} for refund {refund_id}")
            #
            # except Exception as e:
            #     _logger.error(f"Stock return picking creation failed for refund {refund_id}: {str(e)}")


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    shopify_line_id = fields.Char(string="Shopify Line Item ID", index=True)
    is_returned = fields.Boolean("Returned (Shopify)")
    returned_qty = fields.Float("Returned Qty")

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        res['shopify_line_id'] = self.shopify_line_id
        return res
