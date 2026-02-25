from odoo import models, fields
import logging
from datetime import datetime

_logger = logging.getLogger(__name__)

class ShopifyWebhookOrderImport(models.Model):
    _name = 'shopify.webhook.order.import'
    _description = 'Import Shopify Orders via Webhook (Barcode Match Only)'

    def import_order_from_webhook(self, order):
        """
        Imports a single Shopify order into Odoo via webhook.
        Only products with a matching barcode in Odoo are imported as order lines.
        """
        instance = self.env['shopify.instance']._resolve_instance(
            order_url=order.get('order_status_url') or order.get('status_url') or False
        )
        if not instance:
            _logger.error("❌ No Shopify instance found in the system. Please configure one.")
            return

        shopify_domain = instance.shopify_domain
        shopify_id = order.get('id')
        origin = f"Shopify-{shopify_id}"
        payment_status = order.get('financial_status', 'pending')
        shopify_order_url = f"https://{shopify_domain}/admin/orders/{shopify_id}"

        # Extract tracking info from fulfillments
        fulfillments = order.get('fulfillments', [])
        tracking_number = ""
        delivery_partner_name = ""
        if fulfillments:
            latest_fulfillment = sorted(fulfillments, key=lambda f: f.get('created_at', ''), reverse=True)[0]
            tracking_number = latest_fulfillment.get('tracking_number', '')
            delivery_partner_name = latest_fulfillment.get('tracking_company', '')

        # Check if order already exists in Odoo (per instance + Shopify ID)
        existing_order = self.env['sale.order'].search([
            ('shopify_order_id', '=', str(shopify_id)),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)
        if not existing_order:
            existing_order = self.env['sale.order'].search([
                ('origin', '=', origin),
                ('shopify_instance_id', '=', instance.id),
            ], limit=1)
        if not existing_order and not instance.is_order_allowed_for_import(order):
            _logger.info(
                "Skipping webhook order %s due to workflow filters (payment=%s, delivery=%s).",
                shopify_id,
                order.get("financial_status"),
                order.get("fulfillment_status"),
            )
            return

        def prepare_barcode_order_line(line):
            shopify_barcode = line.get('barcode')
            if not shopify_barcode:
                return None
            product = self.env['product.product'].search([('barcode', '=', shopify_barcode)], limit=1)
            if not product:
                _logger.error(f"❌ Product for '{line.get('title')}' (Shopify Barcode: '{shopify_barcode}') not found in Odoo by barcode. Skipping this line item.")
                return None
            quantity = float(line.get('quantity') or 1)
            price = float(line.get('price', 0.0))
            shopify_line_id = str(line.get('id'))
            return {
                'product_id': product.id,
                'product_uom_qty': quantity,
                'price_unit': price,
                'name': line.get('title', ''),
                'shopify_line_id': shopify_line_id,
            }

        def prepare_shipping_line(order_data):
            """Prepare shipping charge as a separate order line"""
            shipping_price = float(order_data.get('shipping_lines', [{}])[0].get('price', 0.0)) if order_data.get('shipping_lines') else 0.0
            if shipping_price > 0:
                # Find or create a shipping product
                shipping_product = self.env['product.product'].search([
                    ('name', 'ilike', 'shipping'),
                    ('type', '=', 'service')
                ], limit=1)
                
                if not shipping_product:
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
                
                return {
                    'product_id': shipping_product.id,
                    'product_uom_qty': 1,
                    'price_unit': shipping_price,
                    'name': f"Shipping: {order_data.get('shipping_lines', [{}])[0].get('title', 'Standard Shipping')}",
                    'shopify_line_id': f"shipping_{order_data.get('id')}",
                }
            return None

        def prepare_discount_line(order_data):
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
                
                _logger.info(f"✅ Creating discount line with amount from Shopify: {total_discount}")
                return discount_line_data
            else:
                _logger.info(f"ℹ️ No discount found in Shopify order data")
                # Log the order data structure for debugging
                if order_data.get('discount_codes'):
                    _logger.info(f"📋 Discount codes structure: {order_data.get('discount_codes')}")
                if order_data.get('total_discounts'):
                    _logger.info(f"📋 Total discounts: {order_data.get('total_discounts')}")
            return None

        def prepare_tax_line(order_data):
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
                _logger.warning(f"⚠️ No tax amount found in Shopify order data. Available fields: {list(order_data.keys())}")
                # Log the order data structure for debugging
                if order_data.get('tax_lines'):
                    _logger.info(f"📋 Tax lines structure: {order_data.get('tax_lines')}")
                if order_data.get('subtotal_price'):
                    _logger.info(f"📋 Subtotal: {order_data.get('subtotal_price')}")
                if order_data.get('total_price'):
                    _logger.info(f"📋 Total: {order_data.get('total_price')}")
            return None

        def debug_order_structure(order_data):
            """Debug function to log complete order structure"""
            _logger.info("🔍 === SHOPIFY ORDER STRUCTURE DEBUG ===")
            _logger.info(f"📋 Order ID: {order_data.get('id')}")
            _logger.info(f"📋 Order Name: {order_data.get('name')}")
            _logger.info(f"📋 Financial Status: {order_data.get('financial_status')}")
            _logger.info(f"📋 Fulfillment Status: {order_data.get('fulfillment_status')}")
            
            # Price information
            _logger.info(f"💰 Subtotal Price: {order_data.get('subtotal_price')}")
            _logger.info(f"💰 Total Price: {order_data.get('total_price')}")
            _logger.info(f"💰 Total Tax: {order_data.get('total_tax')}")
            _logger.info(f"💰 Total Tax Price: {order_data.get('total_tax_price')}")
            _logger.info(f"💰 Total Discounts: {order_data.get('total_discounts')}")
            _logger.info(f"💰 Currency: {order_data.get('currency')}")
            
            # Discount information
            if order_data.get('discount_codes'):
                _logger.info(f"💰 Discount Codes: {len(order_data.get('discount_codes'))}")
                for i, discount in enumerate(order_data.get('discount_codes', [])):
                    _logger.info(f"   💰 Discount {i+1}: Code: {discount.get('code')}, Amount: {discount.get('amount')}, Type: {discount.get('type')}")
            else:
                _logger.info("💰 No discount codes found")
            
            # Shipping information
            if order_data.get('shipping_lines'):
                _logger.info(f"🚚 Shipping Lines: {len(order_data.get('shipping_lines'))}")
                for i, shipping in enumerate(order_data.get('shipping_lines', [])):
                    _logger.info(f"   🚚 Shipping {i+1}: Title: {shipping.get('title')}, Price: {shipping.get('price')}")
            else:
                _logger.info("🚚 No shipping lines found")
            
            # Tax information
            if order_data.get('tax_lines'):
                _logger.info(f"📊 Tax Lines: {len(order_data.get('tax_lines'))}")
                for i, tax in enumerate(order_data.get('tax_lines', [])):
                    _logger.info(f"   📊 Tax {i+1}: Title: {tax.get('title')}, Price: {tax.get('price')}, Rate: {tax.get('rate')}")
            else:
                _logger.info("📊 No tax lines found")
            
            # Line items
            if order_data.get('line_items'):
                _logger.info(f"📦 Line Items: {len(order_data.get('line_items'))}")
                for i, item in enumerate(order_data.get('line_items', [])):
                    _logger.info(f"   📦 Item {i+1}: Title: {item.get('title')}, Price: {item.get('price')}, Quantity: {item.get('quantity')}, Barcode: {item.get('barcode')}")
                    # Check for discount allocations on line items
                    if item.get('discount_allocations'):
                        _logger.info(f"      💰 Discount Allocations: {len(item.get('discount_allocations'))}")
                        for j, discount_alloc in enumerate(item.get('discount_allocations', [])):
                            _logger.info(f"         💰 Discount {j+1}: Amount: {discount_alloc.get('amount')}")
            else:
                _logger.info("📦 No line items found")
            
            _logger.info("🔍 === END DEBUG ===")

        if existing_order:
            vals_to_update = {}
            if existing_order.tracking_number != tracking_number:
                vals_to_update['tracking_number'] = tracking_number
            
            # Handle delivery partner as Many2one field
            delivery_partner_id = None
            if delivery_partner_name:
                delivery_partner_id = existing_order._get_or_create_delivery_partner(delivery_partner_name)
            
            if delivery_partner_id and (not existing_order.delivery_partner_id or existing_order.delivery_partner_id.id != delivery_partner_id):
                vals_to_update['delivery_partner_id'] = delivery_partner_id
            if existing_order.shopify_payment_status != payment_status:
                vals_to_update['shopify_payment_status'] = payment_status
            if existing_order.shopify_order_url != shopify_order_url:
                vals_to_update['shopify_order_url'] = shopify_order_url
            if vals_to_update:
                existing_order.write(vals_to_update)
                _logger.info(f"✅ Updated info for existing order: {existing_order.name}")
            
            # Update or add order lines for existing order (barcode match only)
            for line in order.get('line_items', []):
                line_vals = prepare_barcode_order_line(line)
                if not line_vals:
                    continue
                product_id_from_line = line_vals['product_id']
                order_lines = existing_order.order_line.filtered(lambda l: l.product_id.id == product_id_from_line)
                if order_lines:
                    for ol in order_lines:
                        need_update = (
                            ol.product_uom_qty != line_vals['product_uom_qty'] or
                            ol.price_unit != line_vals['price_unit'] or
                            ol.shopify_line_id != line_vals['shopify_line_id']
                        )
                        if need_update:
                            ol.write({
                                'product_uom_qty': line_vals['product_uom_qty'],
                                'price_unit': line_vals['price_unit'],
                                'shopify_line_id': line_vals['shopify_line_id'],
                            })
                            _logger.info(f"🔁 Updated product '{line.get('title')}' in existing order {existing_order.name}")
                else:
                    line_vals['order_id'] = existing_order.id
                    self.env['sale.order.line'].create(line_vals)
                    _logger.info(f"➕ Added new product '{line.get('title')}' to existing order {existing_order.name}")
            
            # Update shipping, discount and tax lines if they exist
            self._update_shipping_discount_tax_lines(existing_order, order)
            return

        # If order does not exist, create a new one
        customer = order.get('customer')
        if not customer:
            _logger.warning(f"No customer data found for Shopify order {shopify_id}.")
            return
        email = customer.get('email')
        name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
        if not partner:
            partner = self.env['res.partner'].create({'name': name or 'Shopify Customer', 'email': email})
        created_at = order.get('created_at')
        formatted_created_at = fields.Datetime.now()
        if created_at:
            try:
                created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                formatted_created_at = created_at_dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
        
        # Debug: Log the complete order structure
        debug_order_structure(order)
        
        # Prepare all order lines including shipping and taxes
        order_lines_vals = []
        
        # Add product lines
        for line in order.get('line_items', []):
            line_vals = prepare_barcode_order_line(line)
            if line_vals:
                order_lines_vals.append((0, 0, line_vals))
        
        # Add shipping line
        shipping_line = prepare_shipping_line(order)
        if shipping_line:
            order_lines_vals.append((0, 0, shipping_line))
            _logger.info(f"🚚 Added shipping charge: Rs {shipping_line['price_unit']}")
        
        # Add discount line
        discount_line = prepare_discount_line(order)
        if discount_line:
            order_lines_vals.append((0, 0, discount_line))
            _logger.info(f"💰 Added discount: Rs {abs(discount_line['price_unit'])}")
        
        # Add tax line - COMMENTED OUT
        # tax_line = prepare_tax_line(order)
        # if tax_line:
        #     order_lines_vals.append((0, 0, tax_line))
        #     _logger.info(f"💰 Added tax charge: Rs {tax_line['price_unit']}")
        
        if not order_lines_vals:
            _logger.warning(f"Skipping order {shopify_id} - No valid order lines to create (barcode match only).")
            return
        
        sale_order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'origin': origin,
            'shopify_order_id': str(shopify_id),
            'name': order.get('name', ''),
            'date_order': formatted_created_at,
            'tracking_number': tracking_number,
            'delivery_partner_id': self._get_or_create_delivery_partner(delivery_partner_name) if delivery_partner_name else None,
            'shopify_payment_status': payment_status,
            'shopify_order_url': shopify_order_url,
            'order_line': order_lines_vals,
        })
        sale_order.action_confirm()
        _logger.info(f"🆕 Created and confirmed Sale Order: {sale_order.name} (Shopify ID: {shopify_id}) with status: {payment_status}")
        
        return sale_order

    def _update_shipping_discount_tax_lines(self, existing_order, order_data):
        """Update shipping, discount and tax lines for existing orders"""
        try:
            # Update shipping line
            shipping_price = float(order_data.get('shipping_lines', [{}])[0].get('price', 0.0)) if order_data.get('shipping_lines') else 0.0
            if shipping_price > 0:
                shipping_line = existing_order.order_line.filtered(lambda l: 'shipping' in l.shopify_line_id.lower())
                if shipping_line:
                    shipping_line.write({'price_unit': shipping_price})
                else:
                    # Create new shipping line
                    shipping_product = self.env['product.product'].search([('name', 'ilike', 'shipping')], limit=1)
                    if shipping_product:
                        self.env['sale.order.line'].create({
                            'order_id': existing_order.id,
                            'product_id': shipping_product.id,
                            'product_uom_qty': 1,
                            'price_unit': shipping_price,
                            'name': f"Shipping: {order_data.get('shipping_lines', [{}])[0].get('title', 'Standard Shipping')}",
                            'shopify_line_id': f"shipping_{order_data.get('id')}",
                        })
            
            # Update discount line
            total_discount = 0.0
            discount_codes = []
            
            # Get discount amount from various sources
            if order_data.get('total_discounts'):
                total_discount = float(order_data.get('total_discounts', 0.0))
            elif order_data.get('discount_codes'):
                discount_codes = order_data.get('discount_codes', [])
                for discount_code in discount_codes:
                    total_discount += float(discount_code.get('amount', 0.0))
            
            if total_discount > 0:
                discount_line = existing_order.order_line.filtered(lambda l: 'discount' in l.shopify_line_id.lower())
                if discount_line:
                    discount_line.write({'price_unit': -total_discount})
                else:
                    # Create new discount line
                    discount_product = self.env['product.product'].search([('name', 'ilike', 'discount')], limit=1)
                    if not discount_product:
                        discount_product = self.env['product.product'].create({
                            'name': 'Discount',
                            'type': 'service',
                            'sale_ok': True,
                            'purchase_ok': False,
                            'list_price': 0.0,
                            'default_code': 'DISCOUNT',
                            'barcode': 'DISCOUNT001'
                        })
                    
                    # Create discount line name with codes if available
                    discount_name = "Discount"
                    if discount_codes:
                        codes = [code.get('code', '') for code in discount_codes if code.get('code')]
                        if codes:
                            discount_name = f"Discount ({', '.join(codes)})"
                    
                    self.env['sale.order.line'].create({
                        'order_id': existing_order.id,
                        'product_id': discount_product.id,
                        'product_uom_qty': 1,
                        'price_unit': -total_discount,  # Negative amount for discount
                        'name': f"{discount_name} - Imported from Shopify",
                        'shopify_line_id': f"discount_{order_data.get('id')}",
                    })
            
            # Update tax line - COMMENTED OUT
            # total_tax = float(order_data.get('total_tax', 0.0))
            # if total_tax > 0:
            #     tax_line = existing_order.order_line.filtered(lambda l: 'tax' in l.shopify_line_id.lower())
            #     if tax_line:
            #         tax_line.write({'price_unit': total_tax})
            #     else:
            #         # Create new tax line
            #         tax_product = self.env['product.product'].search([('name', 'ilike', 'tax')], limit=1)
            #         if tax_product:
            #             self.env['sale.order.line'].create({
            #                 'order_id': existing_order.id,
            #                 'product_id': tax_product.id,
            #                 'product_uom_qty': 1,
            #                 'price_unit': total_tax,
            #                 'name': f"Taxes & Duties (GST {order_data.get('tax_lines', [{}])[0].get('rate', 0.0)}%)",
            #                 'shopify_line_id': f"tax_{order_data.get('id')}",
            #             })
                        
        except Exception as e:
            _logger.error(f"Error updating shipping/discount/tax lines for order {existing_order.name}: {e}")

    def _get_or_create_delivery_partner(self, partner_name):
        """Find or create delivery partner in res.partner"""
        if not partner_name:
            return None
        
        # Search for existing partner
        partner = self.env['res.partner'].search([('name', '=', partner_name)], limit=1)
        
        if not partner:
            # Create new partner
            partner = self.env['res.partner'].create({
                'name': partner_name,
                'is_company': True,
                'customer_rank': 0,
                'supplier_rank': 0,
            })
        
        return partner.id 
