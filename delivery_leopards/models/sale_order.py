# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Refresh Leopards delivery rate before confirmation so email/order show actual shipping cost."""
        for order in self:
            if order.carrier_id and order.carrier_id.delivery_type == 'leopards':
                order._set_delivery_method(order.carrier_id)
                order.invalidate_recordset(['amount_total', 'amount_untaxed', 'amount_tax'])
        return super().action_confirm()

    carrier_tracking_ref = fields.Char(
        string='Tracking Number',
        compute='_compute_carrier_tracking_ref',
        store=False,
        help='Tracking number(s) from delivery order(s). Use the Delivery button to open pickings and track.',
    )

    @api.depends('picking_ids', 'picking_ids.carrier_tracking_ref')
    def _compute_carrier_tracking_ref(self):
        for order in self:
            out = order.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'outgoing'
            )
            refs = [p.carrier_tracking_ref for p in out if p.carrier_tracking_ref]
            order.carrier_tracking_ref = ', '.join(refs) if refs else ''

    def action_view_tracking(self):
        """Open delivery pickings so user can track from stock.picking."""
        self.ensure_one()
        return self.action_view_delivery()
