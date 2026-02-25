# my_module/models/product_template.py

from odoo import models, api
import requests
import logging

_logger = logging.getLogger(__name__)

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def create(self, vals):
        rec = super().create(vals)
        try:
            base_url = rec.env['ir.config_parameter'].sudo().get_param('web.base.url')
            sync_url = f"{base_url}/shopify/sync_product/{rec.id}"
            requests.post(sync_url, json={}, headers={'Content-Type': 'application/json'})
        except Exception as e:
            _logger.warning(f"Shopify auto-sync failed: {e}")
        return rec
