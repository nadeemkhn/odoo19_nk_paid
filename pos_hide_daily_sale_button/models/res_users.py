# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResUsers(models.Model):
    _inherit = 'res.users'

    hide_daily_sale_button = fields.Boolean(
        string='Show Daily Sale Button',
        default=False,
        help='If checked, the Daily Sale button will be visible in POS closing popup for this user. If unchecked, the button will be hidden for this user.'
    )
