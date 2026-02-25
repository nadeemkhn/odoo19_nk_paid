# -*- coding: utf-8 -*-
from odoo import models, fields, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'


    def action_cancel(self):
        """Override to cancel order in Shopify if linked."""
        res = super().action_cancel()
        for order in self:
            if order.shopify_order_id:
                order.cancel_shopify_order_in_shopify()
        return res

    def cancel_shopify_order_in_shopify(self):
        """Send cancel request to Shopify using REST API."""
        self.ensure_one()

        instance = self.shopify_instance_id or self.env['shopify.instance']._resolve_instance(
            order_url=self.shopify_order_url
        )
        if not instance:
            raise UserError(_("❌ No Shopify instance configured."))

        shopify_domain = instance.shopify_domain
        access_token = instance.shopify_token

        url = f"https://{shopify_domain}/admin/api/2024-01/orders/{self.shopify_order_id}/cancel.json"
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token
        }

        try:
            response = requests.post(url, headers=headers, timeout=30)
            if response.status_code != 200:
                raise UserError(_("❌ Shopify returned error:\n%s") % response.text)
            _logger.info("✅ Shopify order %s cancelled successfully.", self.shopify_order_id)
        except Exception as e:
            raise UserError(_("❌ Error cancelling order in Shopify:\n%s") % str(e))
