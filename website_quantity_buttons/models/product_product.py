from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    website_qty_button_enabled = fields.Boolean(
        related="product_tmpl_id.website_qty_button_enabled",
        readonly=False,
    )
    website_qty_button_values = fields.Char(
        related="product_tmpl_id.website_qty_button_values",
        readonly=False,
    )
    website_qty_button_label = fields.Char(
        related="product_tmpl_id.website_qty_button_label",
        readonly=False,
    )

