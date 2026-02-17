from odoo import fields, models


class Website(models.Model):
    _inherit = "website"

    website_qty_button_enabled = fields.Boolean(
        string="Website Quantity Buttons",
        help="Show quantity quick-select buttons for all products on this website.",
    )
    website_qty_button_values = fields.Char(
        string="Website Quantity Values",
        default="1,2,3",
        help="Comma-separated positive quantities (max 6 buttons). Example: 1,2,3 or 1,6,12",
    )
    website_qty_button_label = fields.Char(
        string="Website Quantity Label",
        default="Pack",
        help="Text shown in quantity buttons. Example: Pack or Unit.",
    )

    def website_get_qty_button_values(self):
        self.ensure_one()
        values = []
        seen = set()
        for token in (self.website_qty_button_values or "").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                qty = int(token)
            except ValueError:
                continue
            if qty < 1 or qty in seen:
                continue
            values.append(qty)
            seen.add(qty)
            if len(values) >= 6:
                break
        return values

    def website_get_qty_button_label(self):
        self.ensure_one()
        label = (self.website_qty_button_label or "").strip()
        return label or "Pack"
