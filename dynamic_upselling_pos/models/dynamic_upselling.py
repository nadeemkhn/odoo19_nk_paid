# -*- coding: utf-8 -*-

from odoo import api, fields, models


class AxsyncDynamicUpselling(models.Model):
    _name = 'axsync.dynamic.upselling'
    _description = 'Dynamic Upselling Rule'

    active = fields.Boolean(default=True)
    config_ids = fields.Many2many(
        'pos.config',
        'dynamic_upselling_pos_config_rel',
        'upselling_id',
        'config_id',
        string='Shops',
    )
    product_ids = fields.Many2many(
        'product.product',
        'axsync_dynamic_upselling_product_rel',
        'upselling_id',
        'product_id',
        string='Products',
    )

    @api.model
    def get_upsell_products_for_config(self, config_id):
        if not config_id:
            return []

        rules = self.sudo().search([
            ('active', '=', True),
            ('config_ids', 'in', [config_id]),
        ])
        product_ids = rules.mapped('product_ids').ids
        if not product_ids:
            return []

        return self.env['product.product'].sudo().search([
            ('id', 'in', product_ids),
            ('available_in_pos', '=', True),
            ('sale_ok', '=', True),
            ('active', '=', True),
        ]).ids
