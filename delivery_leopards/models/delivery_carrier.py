import requests
import logging
import base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('leopards', 'Leopards Courier')],
        ondelete={'leopards': lambda recs: recs.write({'delivery_type': 'fixed', 'fixed_price': 0})}
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Set default invoicing policy and allow COD for Leopards carriers."""
        for vals in vals_list:
            if vals.get('delivery_type') == 'leopards':
                # Use 'estimated' so shipping fee shows at website checkout. 'real' sets price to 0.
                if 'invoice_policy' not in vals:
                    vals['invoice_policy'] = 'estimated'
                if 'allow_cash_on_delivery' in self._fields and 'allow_cash_on_delivery' not in vals:
                    vals['allow_cash_on_delivery'] = True
        return super().create(vals_list)

    def _leopards_cod_enabled(self):
        """Return True only when COD is enabled and the field exists in this DB/version."""
        self.ensure_one()
        if 'allow_cash_on_delivery' not in self._fields:
            return False
        return bool(self.allow_cash_on_delivery)

    # Leopards Courier API Configuration
    leopards_api_key = fields.Char(
        string='API Key',
        help='Leopards Courier API Key',
        groups='base.group_system'
    )
    leopards_api_secret = fields.Char(
        string='API Secret',
        help='Must match "API Password" from Leopards E-Com Portal → API Management exactly (including special chars like @).',
        groups='base.group_system'
    )
    leopards_api_url = fields.Char(
        string='API URL',
        default='https://merchantapi.leopardscourier.com/api',
        help='Production: merchantapi.leopardscourier.com | Staging: merchantapistaging.leopardscourier.com. Switch via Test/Production button.',
        groups='base.group_system'
    )
    leopards_account_id = fields.Char(
        string='Account ID',
        help='Leopards Courier Account ID',
        groups='base.group_system'
    )
    leopards_shipper_id = fields.Many2one(
        'leopards.shipper',
        string='Shipper',
        domain=[('active', '=', True)],
        help='Shipper sent to Leopards. If set, this name/phone/address is used instead of company. Leave empty to use company.'
    )
    
    def action_leopards_test_connection(self):
        """
        Test API credentials by calling getTariffDetails.
        Shows success with sample rate or detailed error message.
        """
        self.ensure_one()
        if not self.leopards_api_key or not self.leopards_api_secret:
            raise UserError(_(
                'Please enter API Key and API Secret before testing.\n\n'
                'Use the exact values from Leopards E-Com Portal → API Management:\n'
                '• API Key: 40-character key (no spaces, no newlines)\n'
                '• API Password: The value shown as "API Password" in the portal '
                '(Odoo field is named "API Secret" but must match "API Password" exactly)'
            ))
        rate_result = self._leopards_get_rate(weight=1.0, city='self', country='PK')
        rate, api_error = rate_result if isinstance(rate_result, tuple) else (rate_result, None)
        if rate and rate > 0:
            env_label = _('Production') if self.prod_environment else _('Test (Staging)')
            raise UserError(_(
                'Connection successful!\n\n'
                'Environment: %s\n'
                'Sample rate for 1 kg: Rs. %.2f\n\n'
                'Your credentials are valid. You can now use "Get rate" when adding delivery.'
            ) % (env_label, rate))
        msg = api_error or _('Unknown error')
        hint = _(
            'Check:\n'
            '• API Key: Copy exactly from Leopards Portal (40 chars, no extra text)\n'
            '• API Password: Must match the portal\'s "API Password" exactly '
            '(including special chars like @). Current length: %s chars.\n'
            '• Environment: If Test fails, try Production (button top-right). '
            'Portal keys often work only with Production.'
        ) % len((self.leopards_api_secret or '').strip())
        raise UserError(_('Connection failed: %s\n\n%s') % (msg, hint))

    def leopards_rate_shipment(self, order):
        """
        Calculate shipping rate for Leopards Courier
        """
        self.ensure_one()
        
        # When API is not configured, still return fixed price so website shows an amount
        if not self.leopards_api_key or not self.leopards_api_secret:
            if self.fixed_price:
                return {
                    'success': True,
                    'price': self.fixed_price,
                    'error_message': None,
                    'warning_message': _('Using fixed price. Configure API in delivery method for live rates.')
                }
            return {
                'success': False,
                'price': 0.0,
                'error_message': _('Leopards Courier API credentials not configured'),
                'warning_message': None
            }

        try:
            # Weight: context order_weight (delivery wizard), then order.shipping_weight, then from lines
            weight = self.env.context.get('order_weight')
            if weight is None or weight == 0.0:
                weight = order.shipping_weight or 0.0
            if not weight or weight == 0.0:
                weight = sum(
                    (line.product_id.weight or 0.0) * (line.product_uom_qty or 0.0)
                    for line in order.order_line.filtered(lambda l: not l.is_delivery and not l.display_type)
                )
            if not weight or weight == 0.0:
                weight = 1.0  # Default 1 kg
            
            # Get destination address
            partner = order.partner_id
            shipping_address = order.partner_shipping_id or partner
            
            # If address is incomplete (no city and no country), still return fixed price when set
            # so the website checkout shows a delivery amount and creates the delivery line
            if not shipping_address.city and not shipping_address.country_id:
                if self.fixed_price:
                    _logger.info(
                        "Shipping address incomplete (no city/country), using fixed price: %s",
                        self.fixed_price
                    )
                    return {
                        'success': True,
                        'price': self.fixed_price,
                        'error_message': None,
                        'warning_message': _(
                            'Using fixed price. Add city or country for accurate rates.'
                        ),
                    }
                return {
                    'success': False,
                    'price': 0.0,
                    'error_message': _('Shipping address is incomplete. Please provide at least city or country.'),
                    'warning_message': None
                }

            # Call Leopards API to get rate (fetched when customer selects Leopards on checkout)
            cod_amount = order.amount_total if (self._leopards_cod_enabled() and order.amount_total) else 0
            rate_result = self._leopards_get_rate(
                weight=weight,
                city=shipping_address.city or '',
                country=shipping_address.country_id.code or 'PK',
                cod_amount=cod_amount
            )
            
            # _leopards_get_rate returns a tuple (rate, error_message) or (None, error_message)
            if isinstance(rate_result, tuple):
                rate, api_error = rate_result
            else:
                rate = rate_result
                api_error = None

            if rate and rate > 0:
                return {
                    'success': True,
                    'price': rate,
                    'error_message': None,
                    'warning_message': None
                }
            else:
                # If API rate calculation fails, check if fixed price is set
                # This allows using manual/fixed rates when API is not available
                if self.fixed_price:
                    _logger.info("Leopards API rate unavailable, using fixed price: %s", self.fixed_price)
                    warning_msg = _('Using fixed price. API rate calculation unavailable.')
                    if api_error:
                        suf = api_error if api_error.strip().lower().startswith('api error') else f'API Error: {api_error}'
                        warning_msg += f' {suf}'
                    return {
                        'success': True,
                        'price': self.fixed_price,
                        'error_message': None,
                        'warning_message': warning_msg
                    }
                
                # Return a helpful error message with logging info
                error_msg = _('Unable to get shipping rate from Leopards Courier.')
                if api_error:
                    error_msg += f' Error: {api_error}'
                else:
                    error_msg += _(' Please either: 1) Set a fixed price in delivery method settings, '
                                 '2) Check API credentials and URL, 3) Check Odoo logs for details.')
                
                _logger.warning("Rate calculation failed for order %s. API response logged above.", 
                               order.name)
                return {
                    'success': False,
                    'price': 0.0,
                    'error_message': error_msg,
                    'warning_message': _('Tip: Set a fixed shipping price in delivery method settings '
                                        'to bypass API rate calculation.')
                }

        except Exception as e:
            _logger.error("Leopards Courier rate calculation error: %s", str(e))
            return {
                'success': False,
                'price': 0.0,
                'error_message': _('Error calculating shipping rate: %s') % str(e),
                'warning_message': None
            }

    def leopards_send_shipping(self, pickings):
        """
        Send shipment to Leopards Courier and create shipping label
        """
        self.ensure_one()
        
        if not self.leopards_api_key or not self.leopards_api_secret:
            raise UserError(_('Leopards Courier API credentials not configured'))

        res = []
        for picking in pickings:
            try:
                # Prepare shipment data
                shipment_data = self._leopards_prepare_shipment_data(picking)
                
                # Create shipment via API
                shipment_response = self._leopards_create_shipment(shipment_data)
                
                if shipment_response and shipment_response.get('success'):
                    tracking_number = shipment_response.get('tracking_number')
                    label_url = shipment_response.get('label_url')
                    if not tracking_number:
                        raise UserError(_(
                            'Leopards returned success but no tracking number. '
                            'Check Odoo server logs for the bookPacket response.'
                        ))
                    picking.carrier_tracking_ref = tracking_number
                    picking.leopards_last_status = 'Booked (Pickup Request not Send)'
                    if label_url:
                        self._leopards_attach_label(picking, label_url)
                    # Get actual Leopards tariff (same API/params as checkout for consistency)
                    actual_price = shipment_response.get('price')
                    if actual_price is None or actual_price == 0:
                        weight_kg = sum(
                            (m.product_id.weight or 0) * (m.product_uom_qty or 0)
                            for m in picking.move_ids
                        ) or 1.0
                        partner = picking.partner_id
                        cod = int(picking.sale_id.amount_total or 0) if (picking.sale_id and self._leopards_cod_enabled()) else 0
                        rate_result = self._leopards_get_rate(
                            weight=weight_kg,
                            city=partner.city or 'self',
                            country=partner.country_id.code or 'PK',
                            cod_amount=cod,
                        )
                        actual_price = rate_result[0] if isinstance(rate_result, tuple) else rate_result
                    res.append({
                        'exact_price': float(actual_price or 0.0),
                        'tracking_number': tracking_number,
                    })
                else:
                    error_msg = shipment_response.get('error', 'Unknown error')
                    if 'invalid api key' in str(error_msg).lower() or 'invalid api' in str(error_msg).lower():
                        raise UserError(_(
                            'Leopards API rejected the request: %s\n\n'
                            'To fix:\n'
                            '• Go to Delivery → Configuration → Delivery Methods → open "Leopards Courier".\n'
                            '• API Secret must match API Password in Leopards E-Com Portal exactly (e.g. "bionext").\n'
                            '• Check API Key and API Secret (no leading/trailing spaces).\n'
                            '• If using staging (Test environment) and key is rejected, try Production environment '
                            'API URL https://merchantapi.leopardscourier.com/api — portal keys often work only with production.\n'
                            '• Staging: merchantapistaging.leopardscourier.com — Production: merchantapi.leopardscourier.com'
                        ) % error_msg)
                    raise UserError(_('Failed to create shipment: %s') % error_msg)

            except UserError:
                raise
            except Exception as e:
                _logger.error("Leopards Courier shipment creation error: %s", str(e))
                raise UserError(_('Error creating shipment: %s') % str(e))

        return res

    def leopards_get_tracking_link(self, picking):
        """
        Get tracking link for Leopards Courier shipment
        """
        self.ensure_one()
        tracking_number = picking.carrier_tracking_ref
        if not tracking_number:
            return ''
        
        # Official Leopards tracking page (Pakistan)
        return f'https://www.leopardscourier.com/tracking?cn={tracking_number}'

    def leopards_cancel_shipment(self, picking):
        """
        Cancel shipment with Leopards Courier
        """
        self.ensure_one()
        
        if not picking.carrier_tracking_ref:
            raise UserError(_('No tracking number found for this shipment'))
        
        try:
            result = self._leopards_cancel_shipment_api(picking.carrier_tracking_ref)
            if result and result.get('success'):
                picking.carrier_tracking_ref = False
                return True
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No response'
                raise UserError(_('Failed to cancel shipment: %s') % error_msg)
        except Exception as e:
            _logger.error("Leopards Courier cancellation error: %s", str(e))
            raise UserError(_('Error canceling shipment: %s') % str(e))

    # Private helper methods

    def _leopards_get_api_headers(self):
        """Get API headers for Leopards Courier requests"""
        headers = {
            'Content-Type': 'application/json',
        }
        return headers
    
    def _leopards_get_auth_params(self):
        """Get authentication parameters for Leopards API requests"""
        return {
            'api_key': self.leopards_api_key or '',
            'api_password': self.leopards_api_secret or ''
        }

    def _leopards_mask_key(self, s):
        """Return masked API key for logging (first 4 + ... + last 4)."""
        s = (s or '').strip()
        if len(s) <= 8:
            return '***' if s else '(empty)'
        return f"{s[:4]}...{s[-4:]}"

    def _leopards_normalize_credential(self, s):
        """Normalize API key/secret: first line, strip, fix duplicate paste or extra text."""
        s = (s or '').strip().replace('\r', '')
        # First line only (handles paste with newlines)
        first = s.split('\n')[0].strip() if '\n' in s else s
        # First word/token (handles paste with spaces)
        if ' ' in first:
            first = first.split()[0]
        # Leopards API keys are ~40–43 chars; if 50+, likely duplicate paste
        if len(first) > 50:
            half = len(first) // 2
            if first[:half] == first[half:half * 2]:
                first = first[:half]  # Duplicate paste
            else:
                first = first[:43]  # Take first valid-length key
        return first

    def _leopards_base_url(self):
        """Return base API URL only (no endpoint path). Strips getTariffDetails/bookPacket/etc. if user pasted full URL."""
        base = (self.leopards_api_url or '').rstrip('/')
        for path in ('/getTariffDetails', '/bookPacket', '/cancelBookedPackets', '/trackBookedPacket'):
            if path in base:
                base = base.split(path)[0].rstrip('/')
        # Leopards expects paths under /api/; ensure base ends with /api
        if base and 'leopardscourier.com' in base and not base.endswith('/api'):
            base = base.rstrip('/') + '/api'
        return base

    def _leopards_get_rate(self, weight, city='', country='PK', cod_amount=0):
        """
        Get shipping rate from Leopards Courier API using getTariffDetails endpoint
        Based on Leopards API Documentation v2.0
        Returns: (rate, error_message) tuple or (None, error_message)
        """
        try:
            # Use getTariffDetails endpoint for rate calculation
            # Weight should be in grams, but we have it in kg, so convert
            weight_grams = int(weight * 1000) if weight else 1000
            
            # Build URL with query parameters (GET request)
            base_url = self._leopards_base_url()
            if not self.prod_environment and 'merchantapistaging' not in base_url:
                # Use staging URL when not in production (only when URL is still production)
                base_url = base_url.replace('merchantapi', 'merchantapistaging')
            
            url = f"{base_url}/getTariffDetails/format/json/"
            
            # Origin = shipper (company), destination = customer city or 'self' for default
            origin_city = 'self'
            destination_city = (city or '').strip() if city else 'self'
            if not destination_city:
                destination_city = 'self'
            
            # Build query parameters
            # Note: shipment_type is required for production API
            # Common values: 'overnight', 'express', 'standard', etc.
            # If empty, production API returns error, so we use 'overnight' as default
            shipment_type = 'overnight'  # Default shipment type
            
            api_key = self._leopards_normalize_credential(self.leopards_api_key)
            api_secret = self._leopards_normalize_credential(self.leopards_api_secret)
            if len(api_key) > 60:
                _logger.warning("Leopards API key length=%s (expected ~40). Check for newlines or duplicate paste in API Key field.", len(api_key))
            params = {
                'api_key': api_key,
                'api_password': api_secret,
                'packet_weight': weight_grams,
                'shipment_type': shipment_type,
                'origin_city': origin_city,
                'destination_city': destination_city,
                'cod_amount': int(cod_amount or 0)
            }
            
            headers = self._leopards_get_api_headers()
            
            _logger.info(
                "Leopards rate API: url=%s | api_key=%s | api_password len=%s | params=%s",
                url, self._leopards_mask_key(api_key), len(api_secret),
                {k: (self._leopards_mask_key(v) if k == 'api_key' else '***' if k == 'api_password' else v) for k, v in params.items()}
            )
            
            # Use GET request as per documentation
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            _logger.info("Leopards API response status: %s, body: %s", 
                        response.status_code, response.text[:500])
            
            response.raise_for_status()
            
            result = response.json()
            if result.get('status') == 1:
                # Extract total charges
                packet_charges = result.get('packet_charges', {})
                shipment_charges = float(packet_charges.get('shipment_charges', 0) or 0)
                cash_handling = float(packet_charges.get('cash_handling', 0) or 0)
                insurance_charges = float(packet_charges.get('insurance_charges', 0) or 0)
                gst_amount = float(packet_charges.get('gst_amount', 0) or 0)
                fuel_surcharge_amount = float(packet_charges.get('fuel_surcharge_amount', 0) or 0)
                
                # Calculate total rate
                total_rate = shipment_charges + cash_handling + insurance_charges + gst_amount + fuel_surcharge_amount
                
                _logger.info("Leopards rate calculated: %s (breakdown: shipment=%s, cash_handling=%s, insurance=%s, gst=%s, fuel=%s)", 
                           total_rate, shipment_charges, cash_handling, insurance_charges, gst_amount, fuel_surcharge_amount)
                
                return (total_rate, None)
            else:
                error_msg = result.get('error', 'Unknown error')
                _logger.warning("Leopards API returned error: %s", error_msg)
                return (None, f"API Error: {error_msg}")
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 'N/A'
            response_text = e.response.text[:500] if e.response else 'No response'
            error_msg = f"HTTP {status_code}: {response_text}"
            _logger.error("Leopards API HTTP error [%s]: %s - Response: %s", 
                         status_code, str(e), response_text)
            return (None, error_msg)
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection error: Unable to reach {self.leopards_api_url}. Check URL and network."
            _logger.error("Leopards API connection error: %s", str(e))
            return (None, error_msg)
        except requests.exceptions.Timeout as e:
            error_msg = "Request timeout: API did not respond in time."
            _logger.error("Leopards API timeout error: %s", str(e))
            return (None, error_msg)
        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            _logger.error("Leopards API request error: %s", str(e))
            return (None, error_msg)
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            _logger.error("Leopards rate calculation error: %s", str(e), exc_info=True)
            return (None, error_msg)

    def _leopards_prepare_shipment_data(self, picking):
        """
        Prepare shipment data for Leopards Courier API - Book a Packet (single packet, flat JSON).
        Based on Leopards API Documentation v2.0 - GET & POST Book a Packet.
        All required fields must be non-empty; use placeholders when data is missing.
        """
        partner = picking.partner_id
        company = picking.company_id or self.env.company
        # Shipper: picking override > carrier default > company
        shipper = picking.leopards_shipper_id or self.leopards_shipper_id

        # Shipper: use selected shipper or fallback to company
        if shipper:
            shipment_name = shipper.name or 'self'
            shipment_email = (shipper.email or '').strip() or 'self'
            shipment_phone = (shipper.phone or '').strip() or 'self'
            shipment_address = shipper._build_address() or 'self'
        else:
            shipment_name = company.name or 'self'
            shipment_email = company.email or 'self'
            shipment_phone = ((company.phone or (getattr(company.partner_id, 'mobile', None) if company.partner_id else None)) or 'self').strip() or 'self'
            shipment_address = (company.street or 'self').strip() or 'self'

        # Calculate total weight in grams (required by API)
        weight_grams = 0.0
        for move in picking.move_ids:
            product_weight = move.product_id.weight or 0.0  # Weight in kg
            quantity = move.product_uom_qty or 0.0
            weight_grams += (product_weight * quantity) * 1000  # Convert to grams
        
        if not weight_grams or weight_grams == 0.0:
            weight_grams = 1000  # Default 1 kg in grams (API minimum)
        
        # COD amount - required, use 0 for prepaid
        cod_amount = 0
        if picking.sale_id:
            cod_amount = int(picking.sale_id.amount_total or 0)
        
        # Build address from partner (consignment = receiver)
        addr_parts = [p for p in (partner.street, partner.street2, partner.city, partner.state_id.name, partner.zip) if p]
        consignment_address = ', '.join(addr_parts) if addr_parts else (partner.street or 'Address not provided')
        
        # Special instructions - required by API, cannot be empty
        product_names = picking.move_ids.mapped('product_id.name')
        special_instructions = ', '.join(product_names)[:200] if product_names else 'Shipment from Odoo'
        
        api_key = self._leopards_normalize_credential(self.leopards_api_key)
        api_secret = self._leopards_normalize_credential(self.leopards_api_secret)
        if len(api_key) > 60:
            _logger.warning("Leopards API key length=%s (expected ~40). Remove newlines or extra text from API Key.", len(api_key))
        
        # bookPacket expects flat JSON (no 'packets' array)
        shipment_data = {
            'api_key': api_key,
            'api_password': api_secret,
            'booked_packet_weight': int(weight_grams),
            'booked_packet_no_piece': max(1, len(picking.move_ids) or 1),
            'booked_packet_collect_amount': cod_amount,
            'booked_packet_order_id': (picking.name or picking.origin or '')[:50],
            'origin_city': 'self',
            'destination_city': 'self',
            'shipment_name_eng': shipment_name,
            'shipment_email': shipment_email,
            'shipment_phone': shipment_phone,
            'shipment_address': shipment_address,
            'consignment_name_eng': (partner.name or 'Consignee').strip(),
            'consignment_email': (partner.email or '').strip(),
            'consignment_phone': (partner.phone or getattr(partner, 'mobile', None) or '0000000000').strip(),
            'consignment_phone_two': '',
            'consignment_phone_three': '',
            'consignment_address': consignment_address.strip(),
            'special_instructions': special_instructions.strip(),
        }
        # Add optional shipment_id only if configured
        if self.leopards_account_id:
            shipment_data['shipment_id'] = self.leopards_account_id.strip()
        shipment_data['shipment_type'] = ''  # Optional, API uses default overnight
        
        return shipment_data

    def _leopards_create_shipment(self, shipment_data):
        """
        Create shipment via Leopards Courier API
        Based on Leopards API Documentation v2.0 - Book a Packet
        """
        try:
            base_url = self._leopards_base_url()
            if not self.prod_environment and 'merchantapistaging' not in base_url:
                base_url = base_url.replace('merchantapi', 'merchantapistaging')
            
            url = f"{base_url}/bookPacket/format/json/"
            # Leopards bookPacket expects JSON body (verified via Postman)
            headers_json = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            _api_key = (shipment_data.get('api_key') or '').strip()
            _api_secret = (shipment_data.get('api_password') or '').strip()

            _logger.info(
                "Leopards bookPacket API: url=%s | api_key=%s | api_password len=%s (JSON)",
                url, self._leopards_mask_key(_api_key), len(_api_secret)
            )
            _logger.info(
                "Leopards bookPacket payload: %s",
                {k: (self._leopards_mask_key(v) if k == 'api_key' else '***' if k == 'api_password' else v) for k, v in shipment_data.items()}
            )

            response = requests.post(url, json=shipment_data, headers=headers_json, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            _logger.info("Leopards API bookPacket response: %s", result)
            
            ok = result.get('status') in (1, '1')
            if ok:
                # API returns track_number; some docs use cn_number for same value
                raw = result.get('track_number') or result.get('cn_number')
                track_number = None
                if raw is not None:
                    track_number = str(raw).strip()
                slip_link = result.get('slip_link') or result.get('label_url')
                slip_link = (slip_link or '').strip() if isinstance(slip_link, str) else ''
                if not track_number:
                    _logger.error("Leopards bookPacket status=1 but no track_number/cn_number: %s", result)
                    return {
                        'success': False,
                        'error': _('Leopards returned success but no tracking number (CN). Check API response in logs.'),
                    }
                return {
                    'success': True,
                    'tracking_number': track_number,
                    'label_url': slip_link or None,
                    'price': 0.0,  # Price from getTariffDetails
                }
            else:
                error_msg = result.get('error', 'Unknown error')
                api_key = (shipment_data.get('api_key') or '')
                api_secret = (shipment_data.get('api_password') or '')
                _logger.error(
                    "Leopards API booking failed: %s (api_key len=%s, ends=%s; api_password len=%s)",
                    error_msg, len(api_key),
                    api_key[-4:] if len(api_key) >= 4 else '(none)',
                    len(api_secret)
                )
                return {
                    'success': False,
                    'error': error_msg,
                }
                
        except requests.exceptions.RequestException as e:
            _logger.error("Leopards API shipment creation error: %s", str(e))
            return {
                'success': False,
                'error': str(e)
            }
        except Exception as e:
            _logger.error("Leopards shipment creation error: %s", str(e))
            return {
                'success': False,
                'error': str(e)
            }

    def _leopards_attach_label(self, picking, label_url):
        """Download and attach shipping label to picking."""
        if not label_url or not picking.carrier_tracking_ref:
            return
        try:
            headers = self._leopards_get_api_headers()
            response = requests.get(label_url, headers=headers, timeout=30)
            response.raise_for_status()
            name = f'Leopards_Label_{picking.carrier_tracking_ref}.pdf'
            self.env['ir.attachment'].create({
                'name': name,
                'type': 'binary',
                'datas': base64.b64encode(response.content).decode('utf-8'),
                'res_model': 'stock.picking',
                'res_id': picking.id,
                'mimetype': 'application/pdf',
            })
        except Exception as e:
            _logger.warning("Failed to attach Leopards label for %s: %s", picking.carrier_tracking_ref, e)

    def _leopards_cancel_shipment_api(self, tracking_number):
        """
        Cancel shipment via Leopards Courier API
        Based on Leopards API Documentation v2.0 - Cancel Booked Packets
        """
        try:
            base_url = self._leopards_base_url()
            if not self.prod_environment and 'merchantapistaging' not in base_url:
                base_url = base_url.replace('merchantapi', 'merchantapistaging')
            
            url = f"{base_url}/cancelBookedPackets/format/json/"
            headers = self._leopards_get_api_headers()
            
            api_key = self._leopards_normalize_credential(self.leopards_api_key)
            api_secret = self._leopards_normalize_credential(self.leopards_api_secret)
            if len(api_key) > 60:
                _logger.warning("Leopards API key length=%s (expected ~40). Check for newlines or duplicate paste in API Key field.", len(api_key))
            data = {
                'api_key': api_key,
                'api_password': api_secret,
                'cn_numbers': tracking_number
            }

            _logger.info(
                "Leopards cancelBookedPackets API: url=%s | api_key=%s | api_password len=%s | cn=%s",
                url, self._leopards_mask_key(api_key), len(api_secret), tracking_number
            )
            
            response = requests.post(url, json=data, headers=headers, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            _logger.info("Leopards cancel API response: %s", result)
            
            if result.get('status') == 1 or result.get('status') == '1':
                return {
                    'success': True,
                    'error': None
                }
            else:
                error_info = result.get('error', {})
                if isinstance(error_info, dict):
                    error_msg = error_info.get(tracking_number, 'Unknown error')
                else:
                    error_msg = str(error_info) if error_info else 'Unknown error'
                
                _logger.error("Leopards API cancellation failed: %s", error_msg)
                return {
                    'success': False,
                    'error': error_msg
                }
            
        except requests.exceptions.RequestException as e:
            _logger.error("Leopards API cancellation error: %s", str(e))
            return {
                'success': False,
                'error': str(e)
            }
        except Exception as e:
            _logger.error("Leopards cancellation error: %s", str(e))
            return {
                'success': False,
                'error': str(e)
            }

    def _leopards_track_shipment_api(self, tracking_number):
        """
        Fetch tracking status via Leopards trackBookedPacket API.
        Uses GET with query params (per PHP library).
        Returns: {'success': True, 'packet_list': [...], 'is_cancelled': bool} or {'success': False, 'error': '...', 'is_cancelled': bool}
        """
        try:
            base_url = self._leopards_base_url()
            if not self.prod_environment and 'merchantapistaging' not in base_url:
                base_url = base_url.replace('merchantapi', 'merchantapistaging')
            url = f"{base_url}/trackBookedPacket/format/json/"
            api_key = self._leopards_normalize_credential(self.leopards_api_key)
            api_secret = self._leopards_normalize_credential(self.leopards_api_secret)
            params = {
                'api_key': api_key,
                'api_password': api_secret,
                'track_numbers': tracking_number,
            }
            _logger.info(
                "Leopards trackBookedPacket API: url=%s | cn=%s",
                url, tracking_number
            )
            response = requests.get(url, params=params, headers=self._leopards_get_api_headers(), timeout=30)
            response.raise_for_status()
            result = response.json()
            _logger.info("Leopards track API full response: %s", result)
            
            # Check for cancellation indicators in response
            is_cancelled = False
            error_msg = result.get('error', '') or ''
            cancel_keywords = ['cancel', 'cancelled', 'canceled', 'cn cancelled', 'packet cancelled', 'shipment cancelled']
            if any(kw in error_msg.lower() for kw in cancel_keywords):
                is_cancelled = True
            
            if result.get('status') == 1 or result.get('status') == '1':
                packet_list = result.get('packet_list') or result.get('packets') or result.get('tracking') or []
                if isinstance(packet_list, dict):
                    packet_list = list(packet_list.values()) if packet_list else []
                # Also check packet_list for cancelled status
                for pkt in (packet_list if isinstance(packet_list, list) else [packet_list]):
                    if isinstance(pkt, dict):
                        for key in ('booked_packet_status', 'consignment_status', 'current_status', 'status', 'status_code', 'Status'):
                            val = str(pkt.get(key, '')).strip().upper()
                            if val in ('CN', 'CL', 'CANCELLED', 'CANCELED'):
                                is_cancelled = True
                                break
                return {'success': True, 'packet_list': packet_list, 'error': None, 'is_cancelled': is_cancelled}
            return {
                'success': False,
                'packet_list': [],
                'error': error_msg or 'Unknown error',
                'is_cancelled': is_cancelled,
            }
        except requests.exceptions.RequestException as e:
            _logger.error("Leopards track API error: %s", str(e))
            return {'success': False, 'packet_list': [], 'error': str(e), 'is_cancelled': False}
        except Exception as e:
            _logger.error("Leopards track error: %s", str(e))
            return {'success': False, 'packet_list': [], 'error': str(e), 'is_cancelled': False}
