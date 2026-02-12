from odoo import fields, models


class ReportPosOrder(models.Model):
    _inherit = "report.pos.order"

    salesperson_name = fields.Char(
        string="Salesperson",
        readonly=True,
        aggregator="count_distinct",
    )

    def _select(self):
        return super()._select() + ",NULLIF(l.salesperson_name, '') AS salesperson_name"
