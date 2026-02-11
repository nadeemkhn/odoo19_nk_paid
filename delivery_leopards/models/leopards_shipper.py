# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class LeopardsShipper(models.Model):
    _name = 'leopards.shipper'
    _description = 'Leopards Shipper'
    _order = 'name'

    # Basic info (matching Leopards Manage Shipper form)
    name = fields.Char(
        string='Shipper Name',
        required=True,
        help='Name sent to Leopards (appears in Leopards portal)'
    )
    email = fields.Char(string='Shipper Email')
    phone = fields.Char(string='Shipper Phone')
    cnic = fields.Char(
        string='Shipper CNIC',
        help='Pakistan National ID (e.g. 8220332749219)'
    )

    # Location (matching Leopards Select City / Area)
    city = fields.Char(string='City', help='e.g. Lahore')
    area = fields.Char(string='Area', help='e.g. Iqbal Town')
    block_sub_area = fields.Char(
        string='Block / Sub Area',
        help='Block or sub-area (optional)'
    )

    # Return address
    return_city = fields.Char(string='Return City', help='City for returns')
    return_address = fields.Text(
        string='Return Address',
        help='Full address for return shipments'
    )

    # Shipper address (full address sent to Leopards)
    shipper_address = fields.Text(
        string='Shipper Address',
        help='Full address (e.g. Karim block allama Iqbal town 251-A, Lahore Pakistan)'
    )

    # Settlement (Settle Your Shipper)
    settle_shipper = fields.Boolean(
        string='Settle Your Shipper',
        help='If you intend to settle your shippers separately, add IBAN and A/C details below.'
    )
    iban = fields.Char(string='IBAN #')
    ac_no = fields.Char(string='A/C No #')

    # Legacy / internal
    street = fields.Char(string='Street')
    street2 = fields.Char(string='Street 2')
    state_id = fields.Many2one(
        'res.country.state',
        string='State',
        domain="[('country_id', '=', country_id)]"
    )
    zip = fields.Char(string='ZIP')
    country_id = fields.Many2one('res.country', string='Country')
    active = fields.Boolean(default=True)

    def _build_address(self):
        """Build full address string for Leopards API."""
        if self.shipper_address and self.shipper_address.strip():
            return self.shipper_address.strip()
        parts = [
            self.street, self.street2, self.city,
            self.state_id.name if self.state_id else '',
            self.zip
        ]
        return ', '.join(p for p in parts if p).strip() or 'self'
