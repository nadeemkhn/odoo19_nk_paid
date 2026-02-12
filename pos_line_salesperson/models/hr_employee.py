from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    show_in_pos_salesperson = fields.Boolean(
        string="Show in POS Salesperson",
        default=False,
        help="If enabled, this employee appears in the POS Salesperson selection list.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if "show_in_pos_salesperson" not in fields_list:
            fields_list.append("show_in_pos_salesperson")
        return fields_list
