# -*- coding: utf-8 -*-

from odoo import models, api


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_pos_ui_pos_config(self, params):
        """Override to add user-specific setting for hiding Daily Sale button"""
        config = super()._get_pos_ui_pos_config(params)
        user = self.env.user
        config['hide_daily_sale_button'] = bool(user.hide_daily_sale_button)
        return config

    @api.model
    def get_user_hide_daily_sale_button_setting(self):
        """RPC method to get current user's hide_daily_sale_button setting"""
        return {
            'hide_daily_sale_button': bool(self.env.user.hide_daily_sale_button)
        }
