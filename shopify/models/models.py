import requests
from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    _inherit = 'product.template'
    shopify_inventory_item_id = fields.Char("Shopify Inventory Item ID")


class ShopifyOrderImport(models.Model):
    _name = 'shopify.order.import'
    _description = 'Shopify Order Import'
    _order = 'name desc'

    name = fields.Char(string="Order Name")
    shopify_order_id = fields.Char(string="Shopify Order ID")
    total_amount = fields.Float(string="Total Amount")
    customer_name = fields.Char(string="Customer Name")
    payment_status = fields.Char(string="Payment Status")
    fulfillment_status = fields.Char(string="Fulfillment Status")
    last_import_date = fields.Datetime(string="Last Import Date", default=lambda self: datetime.now())
    tracking = fields.Char(string='Tracking Number')
    courier = fields.Char(string='Delivery Partner')

    @api.model
    def import_shopify_orders(self, *args, **kwargs):
        if not self.env.context.get("shopify_instance_id"):
            instances = self.env['shopify.instance']._get_target_instances()
            if len(instances) > 1:
                for instance in instances:
                    self.with_context(shopify_instance_id=instance.id).import_shopify_orders(*args, **kwargs)
                return

        instance = self.env['shopify.instance']._get_default_instance()
        if not instance:
            raise ValueError("No Shopify instance found")

        shopify_domain = instance.shopify_domain
        access_token = instance.shopify_token

        base_url = f"https://{shopify_domain}/admin/api/2024-01/orders.json"
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        }

        latest_import = self.env['shopify.order.import'].search(
            [('shopify_instance_id', '=', instance.id)],
            limit=1,
            order='last_import_date desc',
        )
        last_import_date = latest_import.last_import_date if latest_import else None

        since_date = False
        if last_import_date:
            since_date = (last_import_date - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
        params = instance.get_order_import_query_params(updated_since=since_date)

        # Try to fetch orders with shipping information
        response = requests.get(base_url, headers=headers, params=params)

        if response.status_code != 200:
            raise UserError(f"Failed to fetch orders from Shopify! Status Code: {response.status_code} - Response: {response.text}")

        orders = response.json().get('orders', [])
        _logger.info(f"Fetched {len(orders)} orders from Shopify.")
        
        # Check if orders have shipping data
        orders_with_shipping = [o for o in orders if o.get('shipping_lines')]
        _logger.info(f"📊 Orders with shipping data: {len(orders_with_shipping)} out of {len(orders)}")
        
        if not orders_with_shipping and orders:
            _logger.warning(f"⚠️ No orders have shipping_lines data. This might indicate an API version issue.")
            _logger.info(f"🔍 First order sample keys: {list(orders[0].keys()) if orders else 'No orders'}")
            # Try alternative API version if no shipping data
            alt_url = f"https://{shopify_domain}/admin/api/2023-10/orders.json"
            _logger.info(f"🔄 Trying alternative API version: 2023-10")
            alt_response = requests.get(alt_url, headers=headers, params=params)
            if alt_response.status_code == 200:
                alt_orders = alt_response.json().get('orders', [])
                alt_orders_with_shipping = [o for o in alt_orders if o.get('shipping_lines')]
                _logger.info(f"📊 Alternative API: Orders with shipping data: {len(alt_orders_with_shipping)} out of {len(alt_orders)}")
                if alt_orders_with_shipping:
                    _logger.info(f"✅ Alternative API version has shipping data. Consider updating to 2023-10")
                    orders = alt_orders  # Use the alternative version

        for order in orders:
            shopify_order_id = str(order.get('id'))
            existing_order = self.env['shopify.order.import'].search([
                ('shopify_order_id', '=', shopify_order_id),
                ('shopify_instance_id', '=', instance.id),
            ], limit=1)
            existing_sale = self.env['sale.order'].search([
                ('shopify_order_id', '=', shopify_order_id),
                ('shopify_instance_id', '=', instance.id),
            ], limit=1)
            if not existing_order and not existing_sale and not instance.is_order_allowed_for_import(order):
                _logger.info(
                    "Skipping Shopify order %s due to workflow filters (payment=%s, delivery=%s).",
                    shopify_order_id,
                    order.get("financial_status"),
                    order.get("fulfillment_status"),
                )
                continue

            # Debug shipping information
            _logger.info(f"🔍 DEBUG: Processing order {shopify_order_id}")
            if order.get('shipping_lines'):
                _logger.info(f"🔍 DEBUG: Order {shopify_order_id} has shipping_lines: {order.get('shipping_lines')}")
                for i, shipping in enumerate(order.get('shipping_lines', [])):
                    _logger.info(f"🔍 DEBUG: Shipping line {i}: {shipping}")
            else:
                _logger.warning(f"⚠️ Order {shopify_order_id} has NO shipping_lines data")
                _logger.info(f"🔍 DEBUG: Available order keys: {list(order.keys())}")
                
            # Debug complete order structure for first few orders
            if int(shopify_order_id) <= 3:  # Only debug first 3 orders to avoid log spam
                _logger.info(f"🔍 DEBUG: Complete order structure for {shopify_order_id}:")
                _logger.info(f"🔍 DEBUG: Order name: {order.get('name')}")
                _logger.info(f"🔍 DEBUG: Total price: {order.get('total_price')}")
                _logger.info(f"🔍 DEBUG: Subtotal price: {order.get('subtotal_price')}")
                _logger.info(f"🔍 DEBUG: Total tax: {order.get('total_tax')}")
                _logger.info(f"🔍 DEBUG: Financial status: {order.get('financial_status')}")
                _logger.info(f"🔍 DEBUG: Line items count: {len(order.get('line_items', []))}")
                if order.get('shipping_lines'):
                    _logger.info(f"🔍 DEBUG: Shipping lines count: {len(order.get('shipping_lines', []))}")
                else:
                    _logger.info(f"🔍 DEBUG: NO shipping_lines found!")

            customer_name = ""
            if order.get('customer'):
                customer_name = f"{order['customer'].get('first_name', '')} {order['customer'].get('last_name', '')}".strip()

            payment_status = order.get('financial_status') or 'unknown'
            fulfillment_status = order.get('fulfillment_status') or ''
            fulfillments = order.get('fulfillments', [])

            if not fulfillment_status and fulfillments and fulfillments[0].get('status'):
                fulfillment_status = fulfillments[0]['status']
            elif not fulfillment_status:
                fulfillment_status = 'unfulfilled'

            tracking_number = ""
            courier_name = ""

            for fulfillment in reversed(fulfillments):
                if fulfillment.get('status') != 'cancelled':
                    tracking_number = fulfillment.get('tracking_number', '') or ''
                    courier_name = fulfillment.get('tracking_company', '') or ''
                    break

            vals = {
                'name': order.get('name', ''),
                'shopify_order_id': shopify_order_id,
                'total_amount': float(order.get('total_price', 0.0)),
                'customer_name': customer_name,
                'payment_status': payment_status,
                'fulfillment_status': fulfillment_status,
                'last_import_date': datetime.now(),
                'tracking': tracking_number,
                'courier': courier_name,
                'shopify_instance_id': instance.id,
            }

            _logger.info(f"Processing Shopify Order {order.get('name')} - Payment: {payment_status}, Fulfillment: {fulfillment_status}")

            if existing_order:
                existing_order.write(vals)
            else:
                self.env['shopify.order.import'].create(vals)

            if existing_sale:
                updates_needed = {}

                if fulfillment_status == 'cancelled':
                    updates_needed['tracking_number'] = ''
                    updates_needed['delivery_partner'] = ''
                else:
                    if existing_sale.tracking_number != tracking_number:
                        updates_needed['tracking_number'] = tracking_number
                    if existing_sale.delivery_partner != courier_name:
                        updates_needed['delivery_partner'] = courier_name

                if existing_sale.amount_total != float(order.get('total_price', 0.0)):
                    updates_needed['amount_total'] = float(order.get('total_price', 0.0))

                if existing_sale.state != 'sale' and fulfillment_status == 'fulfilled':
                    updates_needed['state'] = 'sale'

                if updates_needed:
                    existing_sale.write(updates_needed)
                    _logger.info(f"Updated Sale Order {existing_sale.name} with changes: {updates_needed}")

                # Prepare order lines
                order_lines = []
                for line in order.get('line_items', []):
                    product = self._find_product_by_shopify_id(line.get('product_id'))
                    if product:
                        order_lines.append((0, 0, {
                            'product_id': product.id,
                            'product_uom_qty': line.get('quantity', 1),
                            'price_unit': float(line.get('price', 0.0)),
                            'shopify_line_id': str(line.get('id'))
                        }))

                # Add shipping line
                shipping_line = self._prepare_shipping_line(order)
                if shipping_line:
                    order_lines.append((0, 0, shipping_line))
                    _logger.info(f"🚚 Added shipping charge: Rs {shipping_line['price_unit']}")
                    _logger.info(f"🔍 DEBUG: Shipping line added to order_lines. Total lines now: {len(order_lines)}")
                else:
                    _logger.warning(f"⚠️ No shipping line created for order {shopify_order_id}")

                # Add discount line
                discount_line = self._prepare_discount_line(order)
                if discount_line:
                    order_lines.append((0, 0, discount_line))
                    _logger.info(f"💰 Added discount: Rs {abs(discount_line['price_unit'])}")
                    _logger.info(f"🔍 DEBUG: Discount line added to order_lines. Total lines now: {len(order_lines)}")
                else:
                    _logger.info(f"ℹ️ No discount line created for order {shopify_order_id}")

                # Add tax line - COMMENTED OUT
                # tax_line = self._prepare_tax_line(order)
                # if tax_line:
                #     order_lines.append((0, 0, tax_line))
                #     _logger.info(f"💰 Added tax charge: Rs {tax_line['price_unit']}")
                #     _logger.info(f"🔍 DEBUG: Tax line added to order_lines. Total lines now: {len(order_lines)}")
                # else:
                #     _logger.warning(f"⚠️ No tax line created for order {shopify_order_id}")

                _logger.info(f"🔍 DEBUG: Final order_lines structure: {order_lines}")
                _logger.info(f"🔍 DEBUG: Number of order lines: {len(order_lines)}")

                if not order_lines:
                    _logger.warning(f"Skipping order {shopify_order_id} - No valid order lines to create (barcode match only).")
                    continue

                # Existing sale order found: update lines instead of creating duplicate order.
                existing_sale.write({
                    'order_line': [(5, 0, 0)] + order_lines,
                })
                self.env['shopify.sale.paid.order']._apply_webhook_workflow(existing_sale, order, instance)
                _logger.info(f"✅ Updated existing order lines: {existing_sale.name} (Shopify ID: {shopify_order_id})")

            else:
                # Create new sale order when it doesn't exist
                _logger.info(f"🆕 Creating new sale order for Shopify order {shopify_order_id}")
                
                # Find or create customer
                customer = order.get('customer', {})
                if customer:
                    email = customer.get('email', '')
                    name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
                    
                    if email:
                        partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
                        if not partner:
                            partner = self.env['res.partner'].create({
                                'name': name or 'Shopify Customer',
                                'email': email
                            })
                    else:
                        partner = self.env['res.partner'].search([('name', '=', name)], limit=1)
                        if not partner:
                            partner = self.env['res.partner'].create({
                                'name': name or 'Shopify Customer'
                            })
                else:
                    # Create default customer if no customer data
                    partner = self.env['res.partner'].search([('name', '=', 'Shopify Customer')], limit=1)
                    if not partner:
                        partner = self.env['res.partner'].create({
                            'name': 'Shopify Customer'
                        })

                # Prepare order lines for new sale order
                order_lines = []
                for line in order.get('line_items', []):
                    product = self._find_product_by_shopify_id(line.get('product_id'))
                    if product:
                        order_lines.append((0, 0, {
                            'product_id': product.id,
                            'product_uom_qty': line.get('quantity', 1),
                            'price_unit': float(line.get('price', 0.0)),
                            'shopify_line_id': str(line.get('id'))
                        }))

                # Add shipping line
                shipping_line = self._prepare_shipping_line(order)
                if shipping_line:
                    order_lines.append((0, 0, shipping_line))
                    _logger.info(f"🚚 Added shipping charge: Rs {shipping_line['price_unit']}")

                # Add discount line
                discount_line = self._prepare_discount_line(order)
                if discount_line:
                    order_lines.append((0, 0, discount_line))
                    _logger.info(f"💰 Added discount: Rs {abs(discount_line['price_unit'])}")

                # Add tax line - COMMENTED OUT
                # tax_line = self._prepare_tax_line(order)
                # if tax_line:
                #     order_lines.append((0, 0, tax_line))
                #     _logger.info(f"💰 Added tax charge: Rs {tax_line['price_unit']}")

                if not order_lines:
                    _logger.warning(f"Skipping order {shopify_order_id} - No valid order lines to create.")
                    continue

                duplicate_sale = self.env['sale.order'].search([
                    ('shopify_order_id', '=', str(shopify_order_id)),
                    ('shopify_instance_id', '=', instance.id),
                ], limit=1)
                if duplicate_sale:
                    _logger.info(
                        "Duplicate guard: sale order already exists for Shopify order %s on instance %s (%s). Skipping create.",
                        shopify_order_id, instance.id, duplicate_sale.name
                    )
                    continue

                # Create new sale order
                sale_order = self.env['sale.order'].with_context(shopify_instance_id=instance.id).create({
                    'partner_id': partner.id,
                    'origin': f"Shopify-{shopify_order_id}",
                    'shopify_order_id': str(shopify_order_id),
                    'name': order.get('name', ''),
                    'date_order': self._parse_shopify_date(order.get('created_at')),
                    'tracking_number': tracking_number,
                    'shopify_payment_status': order.get('financial_status', ''),
                    'shopify_order_url': f"https://{instance.shopify_domain}/admin/orders/{shopify_order_id}",
                    'order_line': order_lines,
                })

                self.env['shopify.sale.paid.order']._apply_webhook_workflow(sale_order, order, instance)
                _logger.info(f"✅ Created new sale order: {sale_order.name} (Shopify ID: {shopify_order_id})")

    def _find_product_by_shopify_id(self, shopify_id):
        """Finds a product by its Shopify ID (product_id) in the Odoo database."""
        product = self.env['product.product'].search([('shopify_inventory_item_id', '=', shopify_id)], limit=1)
        if not product:
            _logger.warning(f"Product with Shopify ID '{shopify_id}' not found in Odoo. Attempting to find by default_code.")
            product = self.env['product.product'].search([('default_code', '=', shopify_id)], limit=1)
        return product

    def _parse_shopify_date(self, shopify_date_str):
        """Parses a Shopify date string into a datetime object."""
        try:
            return datetime.strptime(shopify_date_str, '%Y-%m-%dT%H:%M:%S%z')
        except ValueError:
            _logger.warning(f"Could not parse Shopify date string: {shopify_date_str}")
            return datetime.now()

    def _prepare_discount_line(self, order_data):
        """Prepare discount as a separate order line"""
        # Try different ways to get discount amount from Shopify
        total_discount = 0.0
        discount_codes = []
        
        # Method 1: Check total_discounts field
        if order_data.get('total_discounts'):
            total_discount = float(order_data.get('total_discounts', 0.0))
            _logger.info(f"💰 Found total_discounts: {total_discount}")
        
        # Method 2: Check discount_codes array for discount information
        if order_data.get('discount_codes'):
            discount_codes = order_data.get('discount_codes', [])
            for discount_code in discount_codes:
                code = discount_code.get('code', 'Unknown')
                amount = float(discount_code.get('amount', 0.0))
                discount_type = discount_code.get('type', 'Unknown')
                _logger.info(f"💰 Found discount_code: {code} - Amount: {amount}, Type: {discount_type}")
        
        # Method 3: Calculate from line items discounts
        if order_data.get('line_items'):
            line_discounts = 0.0
            for line_item in order_data.get('line_items', []):
                if line_item.get('discount_allocations'):
                    for discount_allocation in line_item.get('discount_allocations', []):
                        line_discounts += float(discount_allocation.get('amount', 0.0))
            if line_discounts > 0:
                total_discount = line_discounts
                _logger.info(f"💰 Calculated discount from line items: {total_discount}")
        
        # Method 4: Calculate from subtotal and total (if no shipping/tax)
        if total_discount == 0 and order_data.get('subtotal_price') and order_data.get('total_price'):
            subtotal = float(order_data.get('subtotal_price', 0.0))
            total = float(order_data.get('total_price', 0.0))
            shipping_amount = 0.0
            if order_data.get('shipping_lines'):
                shipping_amount = float(order_data.get('shipping_lines', [{}])[0].get('price', 0.0))
            tax_amount = float(order_data.get('total_tax', 0.0))
            calculated_discount = subtotal + shipping_amount + tax_amount - total
            if calculated_discount > 0:
                total_discount = calculated_discount
                _logger.info(f"💰 Calculated discount from prices: Subtotal: {subtotal}, Total: {total}, Shipping: {shipping_amount}, Tax: {tax_amount}, Discount: {total_discount}")
        
        if total_discount > 0:
            # Find or create a discount product
            discount_product = self.env['product.product'].search([
                ('name', 'ilike', 'discount'),
                ('type', '=', 'service')
            ], limit=1)
            
            if not discount_product:
                _logger.info(f"🆕 Creating new discount product")
                # Create a discount product if it doesn't exist
                discount_product = self.env['product.product'].create({
                    'name': 'Discount',
                    'type': 'service',
                    'sale_ok': True,
                    'purchase_ok': False,
                    'list_price': 0.0,
                    'default_code': 'DISCOUNT',
                    'barcode': 'DISCOUNT001'
                })
                _logger.info(f"✅ Created discount product: {discount_product.name} (ID: {discount_product.id})")
            else:
                _logger.info(f"✅ Found existing discount product: {discount_product.name} (ID: {discount_product.id})")
            
            # Create discount line name with codes if available
            discount_name = "Discount"
            if discount_codes:
                codes = [code.get('code', '') for code in discount_codes if code.get('code')]
                if codes:
                    discount_name = f"Discount ({', '.join(codes)})"
            
            # IMPORTANT: Set negative price for discount (Odoo will show as discount)
            discount_line_data = {
                'product_id': discount_product.id,
                'product_uom_qty': 1,
                'price_unit': -total_discount,  # Negative amount for discount
                'name': f"{discount_name} - Imported from Shopify",
                'shopify_line_id': f"discount_{order_data.get('id')}",
            }
            
            _logger.info(f"💰 Discount line data prepared: {discount_line_data}")
            return discount_line_data
        else:
            _logger.info(f"ℹ️ No discount found in Shopify order data")
            # Log the order data structure for debugging
            if order_data.get('discount_codes'):
                _logger.info(f"📋 Discount codes structure: {order_data.get('discount_codes')}")
            if order_data.get('total_discounts'):
                _logger.info(f"📋 Total discounts: {order_data.get('total_discounts')}")
        return None

    def _prepare_shipping_line(self, order_data):
        """Prepare shipping charge as a separate order line"""
        # Enhanced debugging for shipping charges
        _logger.info(f"🔍 DEBUG: Processing shipping for order {order_data.get('id')}")
        _logger.info(f"🔍 DEBUG: Order data keys: {list(order_data.keys())}")
        
        if order_data.get('shipping_lines'):
            _logger.info(f"🔍 DEBUG: Found shipping_lines: {order_data.get('shipping_lines')}")
            shipping_price = float(order_data.get('shipping_lines', [{}])[0].get('price', 0.0)) if order_data.get('shipping_lines') else 0.0
            _logger.info(f"🔍 DEBUG: Extracted shipping price: {shipping_price}")
        else:
            _logger.warning(f"⚠️ No shipping_lines found in order data")
            shipping_price = 0.0
            
        if shipping_price > 0:
            _logger.info(f"🚚 Processing shipping charge: Rs {shipping_price}")
            
            # Find or create a shipping product
            shipping_product = self.env['product.product'].search([
                ('name', 'ilike', 'shipping'),
                ('type', '=', 'service')
            ], limit=1)
            
            if not shipping_product:
                _logger.info(f"🆕 Creating new shipping product")
                # Create a shipping product if it doesn't exist
                shipping_product = self.env['product.product'].create({
                    'name': 'Shipping Charges',
                    'type': 'service',
                    'sale_ok': True,
                    'purchase_ok': False,
                    'list_price': 0.0,
                    'default_code': 'SHIPPING',
                    'barcode': 'SHIPPING001'
                })
                _logger.info(f"✅ Created shipping product: {shipping_product.name} (ID: {shipping_product.id})")
            else:
                _logger.info(f"✅ Found existing shipping product: {shipping_product.name} (ID: {shipping_product.id})")
            
            shipping_line_data = {
                'product_id': shipping_product.id,
                'product_uom_qty': 1,
                'price_unit': shipping_price,
                'name': f"Shipping: {order_data.get('shipping_lines', [{}])[0].get('title', 'Standard Shipping')}",
                'shopify_line_id': f"shipping_{order_data.get('id')}",
            }
            
            _logger.info(f"🚚 Shipping line data prepared: {shipping_line_data}")
            return shipping_line_data
        else:
            _logger.info(f"ℹ️ No shipping charge to process (price: {shipping_price})")
        return None

    def test_shipping_extraction(self, order_data):
        """Test method to debug shipping data extraction"""
        _logger.info("🧪 TESTING SHIPPING DATA EXTRACTION")
        _logger.info(f"🧪 Input order data: {order_data}")
        
        if order_data.get('shipping_lines'):
            _logger.info(f"🧪 Found shipping_lines: {order_data.get('shipping_lines')}")
            shipping_price = float(order_data.get('shipping_lines', [{}])[0].get('price', 0.0))
            _logger.info(f"🧪 Extracted shipping price: {shipping_price}")
            
            # Test the shipping line creation
            shipping_line = self._prepare_shipping_line(order_data)
            if shipping_line:
                _logger.info(f"🧪 Shipping line created successfully: {shipping_line}")
                return shipping_line
            else:
                _logger.error(f"🧪 Failed to create shipping line")
                return None
        else:
            _logger.warning(f"🧪 No shipping_lines found in test data")
            _logger.info(f"🧪 Available keys: {list(order_data.keys())}")
            return None

    def _prepare_tax_line(self, order_data):
        """Prepare tax charges as a separate order line"""
        # Try different ways to get tax amount from Shopify
        total_tax = 0.0
        tax_rate = "Unknown"
        
        # Method 1: Check total_tax field
        if order_data.get('total_tax'):
            total_tax = float(order_data.get('total_tax', 0.0))
            _logger.info(f"📊 Found total_tax: {total_tax}")
        
        # Method 2: Calculate from tax_lines array (PREFERRED METHOD)
        elif order_data.get('tax_lines'):
            tax_lines = order_data.get('tax_lines', [])
            for tax_line in tax_lines:
                tax_amount = float(tax_line.get('price', 0.0))
                total_tax += tax_amount
                # Get the actual tax rate from Shopify
                rate = tax_line.get('rate', 0.0)
                if rate:
                    tax_rate = f"{float(rate) * 100}%"
                _logger.info(f"📊 Found tax_line: {tax_line.get('title', 'Tax')} - Amount: {tax_amount}, Rate: {tax_rate}")
        
        # Method 3: Calculate from price and total_price
        elif order_data.get('subtotal_price') and order_data.get('total_price'):
            subtotal = float(order_data.get('subtotal_price', 0.0))
            total = float(order_data.get('total_price', 0.0))
            # Calculate tax as difference (excluding shipping)
            shipping_amount = 0.0
            if order_data.get('shipping_lines'):
                shipping_amount = float(order_data.get('shipping_lines', [{}])[0].get('price', 0.0))
            total_tax = total - subtotal - shipping_amount
            _logger.info(f"📊 Calculated tax from prices: Subtotal: {subtotal}, Total: {total}, Shipping: {shipping_amount}, Tax: {total_tax}")
        
        # Method 4: Check for specific tax fields
        elif order_data.get('total_tax_price'):
            total_tax = float(order_data.get('total_tax_price', 0.0))
            _logger.info(f"📊 Found total_tax_price: {total_tax}")
        
        if total_tax > 0:
            # Find or create a tax product
            tax_product = self.env['product.product'].search([
                ('name', 'ilike', 'tax'),
                ('type', '=', 'service')
            ], limit=1)
            
            if not tax_product:
                # Create a tax product if it doesn't exist
                tax_product = self.env['product.product'].create({
                    'name': 'Taxes & Duties',
                    'type': 'service',
                    'sale_ok': True,
                    'purchase_ok': False,
                    'list_price': 0.0,
                    'default_code': 'TAX',
                    'barcode': 'TAX001'
                })
            
            # IMPORTANT: Set the exact tax amount from Shopify, not calculated
            tax_line_data = {
                'product_id': tax_product.id,
                'product_uom_qty': 1,
                'price_unit': total_tax,  # Use exact amount from Shopify
                'name': f"Taxes & Duties ({tax_rate}) - Imported from Shopify",
                'shopify_line_id': f"tax_{order_data.get('id')}",
                'tax_id': False,  # Disable Odoo tax calculation
            }
            
            _logger.info(f"✅ Creating tax line with EXACT amount from Shopify: {total_tax} (Rate: {tax_rate})")
            return tax_line_data
        else:
            _logger.error(f"❌ No tax amount found in Shopify order data. Available fields: {list(order_data.keys())}")
            # Log the order data structure for debugging
            if order_data.get('tax_lines'):
                _logger.info(f"📋 Tax lines structure: {order_data.get('tax_lines')}")
            if order_data.get('subtotal_price'):
                _logger.info(f"📋 Subtotal: {order_data.get('subtotal_price')}")
            if order_data.get('total_price'):
                _logger.info(f"📋 Total: {order_data.get('total_price')}")
        return None
