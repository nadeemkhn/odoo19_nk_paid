from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    salesperson_name = fields.Char(string="Salesperson")
    salesperson_employee_id = fields.Many2one(
        "hr.employee",
        string="Salesperson Employee",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if "salesperson_name" not in fields_list:
            fields_list.append("salesperson_name")
        if "salesperson_employee_id" not in fields_list:
            fields_list.append("salesperson_employee_id")
        return fields_list
