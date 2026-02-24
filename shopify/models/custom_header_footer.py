from odoo import models, fields, api


class ResCompany(models.Model):
    _inherit = 'res.company'

    use_avant_garde_layout = fields.Boolean(
        string='Use Avant Garde Layout (Global)',
        default=False,
        help='Apply Avant Garde custom header and footer to all reports.'
    )
    custom_layout_model_ids = fields.Many2many(
        'ir.model',
        string='Models using Custom Layout',
        help='Select models (reports) that should use the custom layout.'
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    use_avant_garde_layout = fields.Boolean(
        related='company_id.use_avant_garde_layout',
        readonly=False,
        string='Use Avant Garde Custom Layout (Global)'
    )
    custom_layout_model_ids = fields.Many2many(
        related='company_id.custom_layout_model_ids',
        readonly=False,
        string='Specific Models'
    )
