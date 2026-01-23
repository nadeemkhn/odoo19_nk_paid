# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PosConfig(models.Model):
    _inherit = 'pos.config'

    show_daily_sales_button = fields.Boolean("Access To Download Daily Sales Report Button From POS")



class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    show_daily_sales_button = fields.Boolean(
        related="pos_config_id.show_daily_sales_button", readonly=False
    )









