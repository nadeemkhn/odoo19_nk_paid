# -*- coding: utf-8 -*-
import requests
import logging
import json
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime

_logger = logging.getLogger(__name__)


class ShopifyOrderEdit(models.Model):
    _name = 'shopify.order.edit'
    _description = 'Shopify Order Edit Processing'

    def process_order_edit(self, order_data):
        """
        Process all order edits from Shopify webhook in one method.
        Handles: Product changes, variant changes, price changes, shipping, discounts, quantities, delivery partner
        """
        try:
            shopify_order_id = str(order_data.get('id'))
            order_name = order_data.get('name', '')
            
            _logger.info(f"🔄 Processing order edit for Shopify Order ID: {shopify_order_id}, Name: {order_name}")
            
            # Find existing order in Odoo
            existing_order = self._find_existing_order(shopify_order_id, order_name, order_data)
            if not existing_order:
                return {'success': False, 'message': f'Order not found in Odoo. ID: {shopify_order_id}, Name: {order_name}'}
            
            _logger.info(f"✅ Found existing order: {existing_order.name} (ID: {existing_order.id})")
            
            # Process all changes in one go
            changes_made = self._process_all_changes(existing_order, order_data)
            
            if changes_made:
                _logger.info(f"✅ Order {existing_order.name} updated with changes: {', '.join(changes_made)}")
                return {
                    'success': True, 
                    'message': f'Order updated successfully. Changes: {", ".join(changes_made)}'
                }
            else:
                _logger.info(f"ℹ️ No changes detected for order {existing_order.name}")
                return {
                    'success': True, 
                    'message': 'No changes detected - order is already up to date'
                }
                
        except Exception as e:
            _logger.error(f"❌ Error processing order edit: {str(e)}")
            import traceback
            _logger.error(f"❌ Traceback: {traceback.format_exc()}")
            return {'success': False, 'message': f'Error processing order edit: {str(e)}'}

    def _find_existing_order(self, shopify_order_id, order_name, order_data):
        """Find existing order using multiple methods"""
        existing_order = None
        
        # Method 1: Search by origin (Shopify-{id})
        origin = f"Shopify-{shopify_order_id}"
        existing_order = self.env['sale.order'].search([('origin', '=', origin)], limit=1)
        if existing_order:
            _logger.info(f"✅ Found order by origin: {existing_order.name}")
            return existing_order
        
        # Method 2: Search by order name
        if order_name:
            existing_order = self.env['sale.order'].search([('name', '=', order_name)], limit=1)
            if existing_order:
                _logger.info(f"✅ Found order by name: {existing_order.name}")
                return existing_order
        
        # Method 3: Search by Shopify order ID field
        existing_order = self.env['sale.order'].search([('shopify_order_id', '=', shopify_order_id)], limit=1)
        if existing_order:
            _logger.info(f"✅ Found order by Shopify ID field: {existing_order.name}")
            return existing_order
        
        # Method 4: Search in order lines by Shopify line ID
        line_items = order_data.get('line_items', [])
        if line_items:
            first_line_id = str(line_items[0].get('id'))
            line_with_shopify_id = self.env['sale.order.line'].search([('shopify_line_id', '=', first_line_id)], limit=1)
            if line_with_shopify_id:
                existing_order = line_with_shopify_id.order_id
                _logger.info(f"✅ Found order by line ID: {existing_order.name}")
                return existing_order
        
        _logger.warning(f"⚠️ Order not found in Odoo. Searched for:")
        _logger.warning(f"   - Origin: {origin}")
        _logger.warning(f"   - Name: {order_name}")
        _logger.warning(f"   - Shopify ID: {shopify_order_id}")
        
        return None

    def _process_all_changes(self, order, order_data):
        """Process all order changes in one method"""
        changes_made = []
        
        # 1. Update basic order information
        changes_made.extend(self._update_basic_order_info(order, order_data))
        
        # 2. Update delivery partner and tracking
        changes_made.extend(self._update_delivery_info(order, order_data))
        
        # 3. Process line item changes (products, quantities, prices, variants)
        changes_made.extend(self._process_line_item_changes(order, order_data))
        
        # 4. Update shipping charges
        changes_made.extend(self._update_shipping_charges(order, order_data))
        
        # 5. Update discount information
        changes_made.extend(self._update_discount_info(order, order_data))
        
        return changes_made

    def _update_basic_order_info(self, order, order_data):
        """Update basic order information like payment status, order name, etc."""
        changes = []
        vals = {}
        
        # Update payment status
        new_payment_status = order_data.get('financial_status', '')
        if new_payment_status and order.shopify_payment_status != new_payment_status:
            vals['shopify_payment_status'] = new_payment_status
            changes.append(f"Payment status: {order.shopify_payment_status} → {new_payment_status}")
        
        # Update order name
        new_order_name = order_data.get('name', '')
        if new_order_name and order.name != new_order_name:
            vals['name'] = new_order_name
            changes.append(f"Order name: {order.name} → {new_order_name}")
        
        # Update order date
        new_order_date = order_data.get('created_at', '')
        if new_order_date:
            try:
                formatted_date = datetime.fromisoformat(new_order_date.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S')
                if order.date_order.strftime('%Y-%m-%d %H:%M:%S') != formatted_date:
                    vals['date_order'] = formatted_date
                    changes.append(f"Order date updated")
            except ValueError:
                _logger.warning(f"Invalid date format: {new_order_date}")
        
        if vals:
            order.write(vals)
            _logger.info(f"📝 Updated basic order info: {vals}")
        
        return changes

    def _update_delivery_info(self, order, order_data):
        """Update delivery partner and tracking information"""
        changes = []
        vals = {}
        
        # Extract tracking info from fulfillments
        fulfillments = order_data.get('fulfillments', [])
        tracking_number = ""
        delivery_partner_name = ""
        
        if fulfillments:
            # Get the latest fulfillment for tracking info
            latest_fulfillment = sorted(fulfillments, key=lambda f: f.get('created_at', ''), reverse=True)[0]
            tracking_number = latest_fulfillment.get('tracking_number', '')
            delivery_partner_name = latest_fulfillment.get('tracking_company', '')
        
        # Update tracking number
        if tracking_number and order.tracking_number != tracking_number:
            vals['tracking_number'] = tracking_number
            changes.append(f"Tracking number: {order.tracking_number} → {tracking_number}")
        
        # Update delivery partner
        if delivery_partner_name:
            delivery_partner_id = self._get_or_create_delivery_partner(delivery_partner_name)
            if delivery_partner_id and (not order.delivery_partner_id or order.delivery_partner_id.id != delivery_partner_id):
                vals['delivery_partner_id'] = delivery_partner_id
                old_name = order.delivery_partner_id.name if order.delivery_partner_id else "None"
                changes.append(f"Delivery partner: {old_name} → {delivery_partner_name}")
        
        if vals:
            order.write(vals)
            _logger.info(f"🚚 Updated delivery info: {vals}")
        
        return changes

    def _process_line_item_changes(self, order, order_data):
        """Process changes to line items: products, quantities, prices, variants"""
        changes = []
        
        # Get current line items from Shopify
        shopify_line_items = order_data.get('line_items', [])
        _logger.info(f"📦 Processing {len(shopify_line_items)} line items from Shopify")
        
        # Create a mapping of Shopify line IDs to current Odoo lines
        current_lines = {line.shopify_line_id: line for line in order.order_line if line.shopify_line_id}
        
        # Track which Shopify lines we've processed
        processed_shopify_lines = set()
        
        for shopify_line in shopify_line_items:
            shopify_line_id = str(shopify_line.get('id'))
            processed_shopify_lines.add(shopify_line_id)
            
            # Check if this line exists in Odoo
            if shopify_line_id in current_lines:
                # Update existing line
                odoo_line = current_lines[shopify_line_id]
                line_changes = self._update_existing_line_item(odoo_line, shopify_line)
                if line_changes:
                    changes.extend(line_changes)
            else:
                # Add new line item
                new_line_change = self._add_new_line_item(order, shopify_line)
                if new_line_change:
                    changes.append(new_line_change)
        
        # Remove lines that are no longer in Shopify
        removed_lines = []
        for shopify_line_id, odoo_line in current_lines.items():
            if shopify_line_id not in processed_shopify_lines:
                # Check if order is confirmed - if so, set quantity to 0 instead of deleting
                if order.state in ['sale', 'done']:
                    if odoo_line.product_uom_qty > 0:
                        odoo_line.write({'product_uom_qty': 0})
                        removed_lines.append(f"Set {odoo_line.product_id.name} quantity to 0")
                else:
                    odoo_line.unlink()
                    removed_lines.append(f"Removed {odoo_line.product_id.name}")
        
        if removed_lines:
            changes.extend(removed_lines)
        
        return changes

    def _update_existing_line_item(self, odoo_line, shopify_line):
        """Update an existing line item with new data from Shopify"""
        changes = []
        vals = {}
        
        # Check quantity changes
        new_quantity = float(shopify_line.get('quantity', 1))
        if odoo_line.product_uom_qty != new_quantity:
            vals['product_uom_qty'] = new_quantity
            changes.append(f"Quantity: {odoo_line.product_uom_qty} → {new_quantity}")
        
        # Check price changes
        new_price = float(shopify_line.get('price', 0.0))
        if odoo_line.price_unit != new_price:
            vals['price_unit'] = new_price
            changes.append(f"Price: {odoo_line.price_unit} → {new_price}")
        
        # Check product/variant changes
        new_product = self._find_product_by_shopify_data(shopify_line)
        if new_product and odoo_line.product_id.id != new_product.id:
            vals['product_id'] = new_product.id
            changes.append(f"Product: {odoo_line.product_id.name} → {new_product.name}")
        
        # Check line name changes (for variant changes)
        new_name = self._get_line_item_name(shopify_line)
        if odoo_line.name != new_name:
            vals['name'] = new_name
            changes.append(f"Line name: {odoo_line.name} → {new_name}")
        
        if vals:
            odoo_line.write(vals)
            _logger.info(f"📝 Updated line item {odoo_line.id}: {vals}")
        
        return changes

    def _add_new_line_item(self, order, shopify_line):
        """Add a new line item to the order"""
        try:
            # Find product in Odoo
            product = self._find_product_by_shopify_data(shopify_line)
            if not product:
                _logger.warning(f"⚠️ Product not found for Shopify line: {shopify_line.get('title')}")
                return None
            
            # Prepare line values
            line_vals = {
                'order_id': order.id,
                'product_id': product.id,
                'product_uom_qty': float(shopify_line.get('quantity', 1)),
                'price_unit': float(shopify_line.get('price', 0.0)),
                'name': self._get_line_item_name(shopify_line),
                'shopify_line_id': str(shopify_line.get('id')),
            }
            
            # Create the line
            new_line = self.env['sale.order.line'].create(line_vals)
            _logger.info(f"➕ Added new line item: {new_line.name}")
            
            return f"Added new product: {new_line.name}"
            
        except Exception as e:
            _logger.error(f"❌ Error adding new line item: {str(e)}")
            return None

    def _update_shipping_charges(self, order, order_data):
        """Update shipping charges - handles multiple shipping lines"""
        changes = []
        
        _logger.info(f"🚚 Processing shipping charges for order {order.name}")
        _logger.info(f"📋 Order data shipping_lines: {order_data.get('shipping_lines', [])}")
        
        # Get all shipping lines from Shopify
        shopify_shipping_lines = order_data.get('shipping_lines', [])
        _logger.info(f"📦 Found {len(shopify_shipping_lines)} shipping lines in Shopify data")
        
        # Get existing shipping lines in Odoo - check both shopify_line_id and product name
        existing_shipping_lines = order.order_line.filtered(lambda l: 
            (l.shopify_line_id and 'shipping' in l.shopify_line_id.lower()) or
            (l.name and ('shipping' in l.name.lower() or '[shipping]' in l.name.lower()))
        )
        _logger.info(f"📦 Found {len(existing_shipping_lines)} existing shipping lines in Odoo")
        
        # Log existing shipping lines details
        for line in existing_shipping_lines:
            _logger.info(f"   - Existing: {line.name} - {line.price_unit} - ID: {line.shopify_line_id}")
        
        # Create a mapping of existing shipping lines by their shopify_line_id
        existing_shipping_map = {line.shopify_line_id: line for line in existing_shipping_lines}
        
        # Track which Shopify shipping lines we've processed
        processed_shopify_shipping = set()
        
        # Process each shipping line from Shopify
        for i, shopify_shipping in enumerate(shopify_shipping_lines):
            shipping_price = float(shopify_shipping.get('price', 0.0))
            shipping_title = shopify_shipping.get('title', 'Standard Shipping')
            # Use the actual Shopify shipping line ID if available, otherwise generate one
            shopify_shipping_id = shopify_shipping.get('id')
            if shopify_shipping_id:
                shopify_line_id = f"shipping_{shopify_shipping_id}"
            else:
                # Fallback: use order ID and index
                shopify_line_id = f"shipping_{order_data.get('id')}_{i}"
            
            processed_shopify_shipping.add(shopify_line_id)
            
            _logger.info(f"🚚 Processing shipping line: {shipping_title} - Price: {shipping_price}")
            
            # Check if shipping line is marked as removed
            is_removed = shopify_shipping.get('is_removed', False)
            if is_removed:
                _logger.info(f"🗑️ Shipping line marked as removed: {shipping_title}")
                # Remove existing shipping line if it exists
                if shopify_line_id in existing_shipping_map:
                    existing_line = existing_shipping_map[shopify_line_id]
                    existing_line.unlink()
                    _logger.info(f" Removed shipping line: {shipping_title}")
                continue
            
            if shipping_price > 0:
                if shopify_line_id in existing_shipping_map:
                    # Update existing shipping line
                    existing_line = existing_shipping_map[shopify_line_id]
                    if existing_line.price_unit != shipping_price or existing_line.name != f"Shipping: {shipping_title}":
                        old_price = existing_line.price_unit
                        existing_line.write({
                            'price_unit': shipping_price,
                            'name': f"Shipping: {shipping_title}"
                        })
                        changes.append(f"Shipping charge updated: {old_price} → {shipping_price}")
                        _logger.info(f"✅ Updated shipping line: {old_price} → {shipping_price}")
                else:
                    # Create new shipping line
                    shipping_product = self._get_or_create_shipping_product()
                    line_vals = {
                        'order_id': order.id,
                        'product_id': shipping_product.id,
                        'product_uom_qty': 1,
                        'price_unit': shipping_price,
                        'name': f"Shipping: {shipping_title}",
                        'shopify_line_id': shopify_line_id,
                    }
                    new_line = self.env['sale.order.line'].create(line_vals)
                    changes.append(f"Added shipping charge: {shipping_title} - {shipping_price}")
                    _logger.info(f"✅ Created new shipping line: {shipping_title} - {shipping_price}")
            else:
                # Remove shipping line if price is 0
                if shopify_line_id in existing_shipping_map:
                    existing_line = existing_shipping_map[shopify_line_id]
                    existing_line.unlink()
                    changes.append(f"Removed shipping charge: {shipping_title}")
                    _logger.info(f"✅ Removed shipping line: {shipping_title}")
        
        # Remove shipping lines that are no longer in Shopify
        for shopify_line_id, existing_line in existing_shipping_map.items():
            if shopify_line_id not in processed_shopify_shipping:
                existing_line.unlink()
                changes.append(f"Removed old shipping charge: {existing_line.name}")
                _logger.info(f"✅ Removed old shipping line: {existing_line.name}")
        
        if changes:
            _logger.info(f"🚚 Shipping changes made: {', '.join(changes)}")
        else:
            _logger.info(f"ℹ️ No shipping changes detected")
        
        return changes

    def _update_discount_info(self, order, order_data):
        """Update discount information"""
        changes = []
        
        # Calculate total discount from Shopify
        total_discount = 0.0
        discount_codes = []
        
        # Method 1: Check total_discounts field
        if order_data.get('total_discounts'):
            total_discount = float(order_data.get('total_discounts', 0.0))
        
        # Method 2: Check discount_codes array
        if order_data.get('discount_codes'):
            discount_codes = order_data.get('discount_codes', [])
            for discount_code in discount_codes:
                total_discount += float(discount_code.get('amount', 0.0))
        
        # Find existing discount line
        discount_line_obj = order.order_line.filtered(lambda l: 'discount' in l.shopify_line_id.lower())
        
        if total_discount > 0:
            # Create discount name with codes
            discount_name = "Discount"
            if discount_codes:
                codes = [code.get('code', '') for code in discount_codes if code.get('code')]
                if codes:
                    discount_name = f"Discount ({', '.join(codes)})"
            
            if discount_line_obj:
                # Update existing discount line
                new_price = -total_discount
                if discount_line_obj.price_unit != new_price:
                    discount_line_obj.write({
                        'price_unit': new_price,
                        'name': f"{discount_name} - Imported from Shopify"
                    })
                    changes.append(f"Discount: {abs(discount_line_obj.price_unit)} → {total_discount}")
            else:
                # Create new discount line
                discount_product = self._get_or_create_discount_product()
                line_vals = {
                    'order_id': order.id,
                    'product_id': discount_product.id,
                    'product_uom_qty': 1,
                    'price_unit': -total_discount,
                    'name': f"{discount_name} - Imported from Shopify",
                    'shopify_line_id': f"discount_{order_data.get('id')}",
                }
                self.env['sale.order.line'].create(line_vals)
                changes.append(f"Added discount: {total_discount}")
        else:
            # Remove discount line if no discount
            if discount_line_obj:
                discount_line_obj.unlink()
                changes.append("Removed discount")
        
        return changes



    def _find_product_by_shopify_data(self, shopify_line):
        """Find product in Odoo by Shopify line data"""
        # Try barcode first
        barcode = shopify_line.get('barcode')
        if barcode:
            product = self.env['product.product'].search([('barcode', '=', barcode)], limit=1)
            if product:
                return product
        
        # Try SKU
        sku = shopify_line.get('sku')
        if sku:
            product = self.env['product.product'].search([('default_code', '=', sku)], limit=1)
            if product:
                return product
        
        # Try product name
        title = shopify_line.get('title', '')
        variant_title = shopify_line.get('variant_title', '')
        if variant_title:
            full_name = f"{title} ({variant_title})"
        else:
            full_name = title
        
        if full_name:
            product = self.env['product.product'].search([('name', '=', full_name)], limit=1)
            if product:
                return product
        
        return None

    def _get_line_item_name(self, shopify_line):
        """Get the display name for a line item"""
        title = shopify_line.get('title', '')
        variant_title = shopify_line.get('variant_title', '')
        
        if variant_title:
            return f"{title} ({variant_title})"
        else:
            return title

    def _get_or_create_delivery_partner(self, partner_name):
        """Find or create delivery partner"""
        if not partner_name:
            return None
        
        partner = self.env['res.partner'].search([('name', '=', partner_name)], limit=1)
        if not partner:
            partner = self.env['res.partner'].create({
                'name': partner_name,
                'is_company': True,
                'customer_rank': 0,
                'supplier_rank': 0,
            })
        
        return partner.id

    def _get_or_create_shipping_product(self):
        """Find or create shipping product"""
        shipping_product = self.env['product.product'].search([
            ('name', 'ilike', 'shipping'),
            ('type', '=', 'service')
        ], limit=1)
        
        if not shipping_product:
            shipping_product = self.env['product.product'].create({
                'name': 'Shipping Charges',
                'type': 'service',
                'sale_ok': True,
                'purchase_ok': False,
                'list_price': 0.0,
                'default_code': 'SHIPPING',
                'barcode': 'SHIPPING001'
            })
        
        return shipping_product

    def _get_or_create_discount_product(self):
        """Find or create discount product"""
        discount_product = self.env['product.product'].search([
            ('name', 'ilike', 'discount'),
            ('type', '=', 'service')
        ], limit=1)
        
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
        
        return discount_product
