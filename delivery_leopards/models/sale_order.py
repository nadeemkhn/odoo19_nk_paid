# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Refresh Leopards delivery rate before confirmation so email/order show actual shipping cost."""
        for order in self:
            if order.carrier_id and order.carrier_id.delivery_type == 'leopards':
                try:
                    vals = order.carrier_id.rate_shipment(order)
                    if vals.get('success'):
                        order.set_delivery_line(order.carrier_id, vals.get('price', 0.0))
                        write_vals = {}
                        if 'recompute_delivery_price' in order._fields:
                            write_vals['recompute_delivery_price'] = False
                        if 'delivery_message' in order._fields:
                            write_vals['delivery_message'] = vals.get('warning_message') or False
                        if write_vals:
                            order.write(write_vals)
                except Exception:
                    _logger.exception("Leopards delivery rate refresh failed during confirmation for %s", order.name)
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
