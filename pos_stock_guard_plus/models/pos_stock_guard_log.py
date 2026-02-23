from odoo import fields, models


class PosStockGuardLog(models.Model):
    _name = "pos.stock.guard.log"
    _description = "POS Stock Guard Event"
    _order = "create_date desc, id desc"

    action = fields.Selection(
        selection=[
            ("warn", "Warning"),
            ("block", "Blocked"),
        ],
        required=True,
        index=True,
    )
    user_id = fields.Many2one("res.users", string="User", required=True, index=True)
    config_id = fields.Many2one("pos.config", string="Point of Sale", required=True, index=True)
    session_id = fields.Many2one("pos.session", string="POS Session", index=True)
    product_id = fields.Many2one("product.product", string="Product", required=True, index=True)
    stock_basis = fields.Selection(
        selection=[
            ("free_qty", "Available Quantity"),
            ("forecast_qty", "Forecast Quantity"),
        ],
        string="Stock Basis",
        required=True,
    )
    available_qty = fields.Float(string="Available Qty", digits="Product Unit of Measure")
    reserve_qty = fields.Float(string="Reserve Qty", digits="Product Unit of Measure")
    order_qty = fields.Float(string="Qty In Current Order", digits="Product Unit of Measure")
    add_qty = fields.Float(string="Trying to Add", digits="Product Unit of Measure")
    required_qty = fields.Float(string="Requested Total Qty", digits="Product Unit of Measure")
    allowed_qty = fields.Float(string="Allowed Qty", digits="Product Unit of Measure")
    detail = fields.Text(string="Detail")
