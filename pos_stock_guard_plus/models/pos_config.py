from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = "pos.config"

    pos_stock_guard_enabled = fields.Boolean(
        string="Enable POS Stock Guard",
        help="Prevent selling storable products beyond available stock in this POS.",
    )
    pos_stock_guard_mode = fields.Selection(
        selection=[
            ("block", "Block Sale"),
            ("warn", "Warn But Allow"),
        ],
        string="Restriction Mode",
        default="block",
        required=True,
        help="Block Sale: do not add lines above stock. Warn But Allow: show warning and continue.",
    )
    pos_stock_guard_qty_basis = fields.Selection(
        selection=[
            ("free_qty", "Available Quantity"),
            ("forecast_qty", "Forecast Quantity"),
        ],
        string="Stock Basis",
        default="free_qty",
        required=True,
        help="Choose whether checks are based on available quantity or forecast quantity.",
    )
    pos_stock_guard_buffer_qty = fields.Float(
        string="Reserve Quantity",
        default=0.0,
        digits="Product Unit of Measure",
        help="Always keep this quantity as reserved. POS can only sell stock above this value.",
    )
    pos_stock_guard_allow_override = fields.Boolean(
        string="Allow Override Group",
        help="Users in the 'POS Stock Guard Override' group can continue after warning.",
    )
    pos_stock_guard_excluded_product_ids = fields.Many2many(
        "product.product",
        "pos_stock_guard_config_product_rel",
        "config_id",
        "product_id",
        string="Excluded Products",
        domain="[('available_in_pos', '=', True)]",
        help="These products are ignored by POS Stock Guard.",
    )
    pos_stock_guard_message = fields.Text(
        string="Custom Warning Message",
        help="Optional extra text shown to cashiers when stock guard warning/block appears.",
    )

    @api.constrains("pos_stock_guard_buffer_qty")
    def _check_pos_stock_guard_buffer_qty(self):
        for config in self:
            if config.pos_stock_guard_buffer_qty < 0:
                raise ValidationError("Reserve Quantity cannot be negative.")

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        if not read_records:
            return read_records

        pos_config = (records and records[0]) or config
        if not pos_config:
            return read_records

        read_records[0].update(
            {
                "pos_stock_guard_enabled": bool(pos_config.pos_stock_guard_enabled),
                "pos_stock_guard_mode": pos_config.pos_stock_guard_mode or "block",
                "pos_stock_guard_qty_basis": pos_config.pos_stock_guard_qty_basis or "free_qty",
                "pos_stock_guard_buffer_qty": pos_config.pos_stock_guard_buffer_qty or 0.0,
                "pos_stock_guard_allow_override": bool(pos_config.pos_stock_guard_allow_override),
                "pos_stock_guard_excluded_product_ids": pos_config.pos_stock_guard_excluded_product_ids.ids,
                "pos_stock_guard_message": pos_config.pos_stock_guard_message or False,
            }
        )
        return read_records
