from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    website_qty_button_enabled = fields.Boolean(
        string="Website Quantity Buttons",
        help="Show quantity quick-select buttons on website product cards and product page.",
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
        parse_tokens = lambda raw: [
            int(token.strip())
            for token in (raw or "").split(",")
            if token.strip().isdigit() and int(token.strip()) > 0
        ]

        if self.website_qty_button_enabled:
            for qty in parse_tokens(self.website_qty_button_values):
                if qty in seen:
                    continue
                values.append(qty)
                seen.add(qty)
                if len(values) >= 6:
                    break
            return values

        website = self.env["website"].get_current_website()
        if website and website.website_qty_button_enabled:
            return website.website_get_qty_button_values()
        return []

    def website_get_qty_button_label(self):
        self.ensure_one()
        if self.website_qty_button_enabled:
            label = (self.website_qty_button_label or "").strip()
            return label or "Pack"

        website = self.env["website"].get_current_website()
        if website and website.website_qty_button_enabled:
            return website.website_get_qty_button_label()
        return "Pack"
