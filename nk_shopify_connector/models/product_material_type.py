from odoo import models, fields, api


class ProductMaterialType(models.Model):
    _name = 'product.material.type'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Product Material Type'

    name = fields.Char(string="Material Type", required=True)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    material_type_id = fields.Many2one(
        'product.material.type',
        string="Material Type",
        ondelete='set null',
    )


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    accessory = fields.Float(string="Accessory")
    other_charges = fields.Float(string="Other Charges")

    @api.depends('product_qty', 'price_unit', 'tax_ids', 'discount', 'accessory', 'other_charges')
    def _compute_amount(self):
        """Extend Odoo's computation to include accessory + other_charges."""
        for line in self:
            # Include custom charges in unit price, then apply standard discount.
            effective_price = (line.price_unit + line.accessory + line.other_charges) * (1 - (line.discount or 0.0) / 100.0)

            # Standard tax computation
            taxes = line.tax_ids.compute_all(
                effective_price,
                line.order_id.currency_id,
                line.product_qty,
                product=line.product_id,
                partner=line.order_id.partner_id,
            )

            # Assign computed values
            line.update({
                'price_subtotal': taxes['total_excluded'],
                'price_total': taxes['total_included'],
                'price_tax': taxes['total_included'] - taxes['total_excluded'],
            })



