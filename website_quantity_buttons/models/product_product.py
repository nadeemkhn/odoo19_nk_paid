from odoo import models


class ProductProduct(models.Model):
    _inherit = "product.product"

    def website_get_qty_button_values(self):
        self.ensure_one()
        return self.product_tmpl_id.website_get_qty_button_values()

    def website_get_qty_button_label(self):
        self.ensure_one()
        return self.product_tmpl_id.website_get_qty_button_label()
