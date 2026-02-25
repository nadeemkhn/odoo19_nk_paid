import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import time
import json

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    x_shopify_refund_id = fields.Char(string='Shopify Refund ID', index=True)
    shopify_order_id = fields.Char(string="Shopify Order ID", index=True)
    return_type = fields.Selection([
        ('refund', 'Return for Refund'),
        ('exchange', 'Return for Exchange')
    ], string='Return Type', help="Specify if this return is for refund or exchange")

    def action_confirm(self):
        res = super().action_confirm()
        for picking in self:
            # Ensure shopify_order_id is set on confirm, fallback from sale.order
            if picking.sale_id and picking.sale_id.shopify_order_id and not picking.shopify_order_id:
                picking.shopify_order_id = picking.sale_id.shopify_order_id
                _logger.info(f"Set shopify_order_id {picking.shopify_order_id} on picking {picking.name}")
        return res


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _make_shopify_api_request(self, url, headers, max_retries=3, base_delay=1):
        """
        Make a Shopify API request with rate limiting and exponential backoff
        """
        for attempt in range(max_retries):
            try:
                _logger.info(f"🔄 Making Shopify API request (attempt {attempt + 1}/{max_retries}): {url}")
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code == 429:  # Rate limit exceeded
                    retry_after = int(response.headers.get('Retry-After', base_delay * (2 ** attempt)))
                    _logger.warning(f"⏳ Rate limit hit! Waiting {retry_after} seconds before retry...")
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()  # Raise HTTPError for other bad responses
                return response
                
            except requests.exceptions.Timeout:
                _logger.warning(f"⏰ Request timeout on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                else:
                    raise
            
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    _logger.warning(f"🔄 Request failed on attempt {attempt + 1}, retrying in {delay}s: {str(e)}")
                    time.sleep(delay)
                    continue
                else:
                    raise
        
        raise UserError(_("Failed to complete Shopify API request after all retries"))

    def debug_return_sync_status(self):
        """
        Debug method to check return sync status for this order
        """
        self.ensure_one()
        
        if not self.shopify_order_id:
            _logger.warning(f"❌ No Shopify Order ID on Sale Order {self.name}")
            return
            
        _logger.info(f"🔍 DEBUG: Return Sync Status for Sale Order {self.name}")
        _logger.info(f"   • Shopify Order ID: {self.shopify_order_id}")
        _logger.info(f"   • Delivery Status: {self.delivery_status}")
        _logger.info(f"   • Is Approved: {getattr(self, 'is_approved', 'Not Set')}")
        
        # Check outgoing pickings (deliveries)
        outgoing_pickings = self.picking_ids.filtered(lambda p: p.picking_type_id.code == 'outgoing')
        _logger.info(f"   • Outgoing Pickings: {len(outgoing_pickings)}")
        for pick in outgoing_pickings:
            _logger.info(f"     - {pick.name} (State: {pick.state})")
            
        # Check existing return pickings
        return_pickings = self.env['stock.picking'].search([
            ('shopify_order_id', '=', self.shopify_order_id),
            ('picking_type_id.code', '=', 'incoming')
        ])
        _logger.info(f"   • Return Pickings: {len(return_pickings)}")
        for ret in return_pickings:
            _logger.info(f"     - {ret.name} (State: {ret.state}, Refund ID: {ret.x_shopify_refund_id})")
            
        # Try to fetch refunds from Shopify
        try:
            instance = self.shopify_instance_id or self.env['shopify.instance']._resolve_instance(
                order_url=self.shopify_order_url
            )
            if instance:
                refunds_url = f"https://{instance.shopify_domain}/admin/api/2023-10/orders/{self.shopify_order_id}/refunds.json"
                headers = {
                    "X-Shopify-Access-Token": instance.shopify_token,
                    "Content-Type": "application/json"
                }
                response = self._make_shopify_api_request(refunds_url, headers, max_retries=3, base_delay=1)
                refunds = response.json().get("refunds", [])
                _logger.info(f"   • Shopify Refunds Found: {len(refunds)}")
                
                for refund in refunds:
                    refund_id = refund.get('id')
                    created_at = refund.get('created_at')
                    line_items = refund.get('refund_line_items', [])
                    _logger.info(f"     - Refund ID: {refund_id}, Created: {created_at}, Items: {len(line_items)}")
                    
        except Exception as e:
            _logger.error(f"❌ Error fetching Shopify refunds for debugging: {e}")
            
        _logger.info(f"🔍 DEBUG: End of return sync status check")
    is_approved = fields.Boolean(string='Is Approved') # This field seems unused in the provided code for filtering.

    def cron_sync_shopify_returns(self):
        """
        Cron job to periodically sync Shopify returns for confirmed sale orders.
        """
        _logger.info("Starting cron job: Sync Shopify Returns.")
        orders = self.search([
            ('shopify_order_id', '!=', False),
            ('state', 'in', ['sale', 'done']),
            # Uncomment the line below if you want to only sync orders that haven't been approved yet
            # ('is_approved', '=', False)
        ])
        if not orders:
            _logger.info("No Shopify orders found in Odoo for return sync.")
            return

        for order in orders:
            _logger.info(f"Attempting to sync returns for Sale Order: {order.name} (Shopify Order ID: {order.shopify_order_id})")
            try:
                order.action_sync_shopify_return()
            except UserError as e:
                # Corrected: UserError objects do not have a 'name' attribute. Use e.args[0] for the message.
                _logger.warning(f"Return sync skipped for Sale Order {order.name}: {e.args[0]}")
            except Exception as e:
                _logger.error(f"Return sync failed for Sale Order {order.name}: {e}", exc_info=True)
        _logger.info("Finished cron job: Sync Shopify Returns.")

    def action_sync_shopify_return(self):
        """
        Syncs returns from Shopify for a specific Sale Order.
        It fetches refund data from Shopify, identifies returned items,
        and creates corresponding incoming stock pickings (returns) in Odoo.
        """
        self.ensure_one()  # Ensures the method is called on a single record

        _logger.info(
            f"Initiating Shopify return sync for Sale Order '{self.name}' (Odoo ID: {self.id}, Shopify Order ID: {self.shopify_order_id}).")

        if not self.shopify_order_id:
            _logger.warning(f"No Shopify Order ID found on Sale Order {self.name}. Cannot sync returns.")
            raise UserError(_("No Shopify Order ID found on this Sale Order."))

        instance = self.shopify_instance_id or self.env['shopify.instance']._resolve_instance(
            order_url=self.shopify_order_url
        )
        if not instance:
            _logger.error("No Shopify instance configured in Odoo. Please set up Shopify integration.")
            raise UserError(_("No Shopify instance configured."))

        refunds_url = f"https://{instance.shopify_domain}/admin/api/2023-10/orders/{self.shopify_order_id}/refunds.json"
        headers = {
            "X-Shopify-Access-Token": instance.shopify_token,
            "Content-Type": "application/json"
        }

        _logger.info(f"Fetching refunds from Shopify API: {refunds_url}")
        try:
            # Use the rate-limited API request method
            response = self._make_shopify_api_request(refunds_url, headers, max_retries=5, base_delay=2)
            refunds = response.json().get("refunds", [])
            _logger.info(f"✅ Successfully received {len(refunds)} refunds from Shopify for order {self.shopify_order_id}.")
            
            # Log details about each refund found
            for i, refund in enumerate(refunds):
                refund_id = refund.get('id', 'Unknown')
                created_at = refund.get('created_at', 'Unknown')
                refund_line_items = refund.get('refund_line_items', [])
                _logger.info(f"  📋 Refund {i+1}: ID={refund_id}, Created={created_at}, Items={len(refund_line_items)}")
                
                for j, item in enumerate(refund_line_items):
                    line_item = item.get('line_item', {})
                    quantity = item.get('quantity', 0)
                    product_title = line_item.get('title', 'Unknown Product')
                    sku = line_item.get('sku', 'No SKU')
                    _logger.info(f"    🔹 Item {j+1}: {product_title} (SKU: {sku}, Qty: {quantity})")
        except requests.exceptions.RequestException as e:
            _logger.error(f"❌ Error fetching Shopify refunds for order {self.shopify_order_id}: {e}")
            raise UserError(_(f"Error fetching Shopify refunds: {e}"))
        except Exception as e:
            _logger.error(
                f"❌ An unexpected error occurred while fetching Shopify refunds for order {self.shopify_order_id}: {e}",
                exc_info=True)
            raise UserError(_(f"An unexpected error occurred: {e}"))

        if not refunds:
            _logger.info(f"No refunds found in Shopify for order {self.shopify_order_id}. No action taken.")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': _('No returns found in Shopify for this order.'),
                    'type': 'info'
                }
            }

        # Find delivered outgoing pickings for this sale order
        pickings = self.picking_ids.filtered(lambda p: p.state == 'done' and p.picking_type_id.code == 'outgoing')
        if not pickings:
            _logger.warning(f"No 'done' outgoing pickings found for Sale Order {self.name}. Cannot process returns.")
            raise UserError(_("No delivered picking found for this order."))
        move_field = 'move_ids_without_package' if 'move_ids_without_package' in self.env['stock.picking']._fields else 'move_ids'
        move_done_field = 'quantity_done' if 'quantity_done' in self.env['stock.move']._fields else 'quantity'

        created_returns_count = 0
        for refund in refunds:
            refund_id = str(refund.get("id"))
            refund_line_items = refund.get("refund_line_items", [])
            _logger.info(f"🔄 Processing Shopify Refund ID: {refund_id} for Order {self.shopify_order_id} with {len(refund_line_items)} line items")

            # Check if this refund_id is already synced in an incoming picking for this shopify order
            already_synced_return = self.env['stock.picking'].search([
                ('shopify_order_id', '=', self.shopify_order_id),
                ('x_shopify_refund_id', '=', refund_id),
                ('picking_type_id.code', '=', 'incoming'),
                ('state', 'not in', ['cancel'])  # Consider 'done' only if you want to be strict
            ], limit=1)

            if already_synced_return:
                _logger.info(
                    f"✅ Shopify Refund ID {refund_id} already synced as picking {already_synced_return.name}. Skipping.")
                continue
                
            # Log refund line items for debugging
            for i, refund_line_item in enumerate(refund_line_items):
                line_item = refund_line_item.get("line_item", {})
                quantity = refund_line_item.get("quantity", 0)
                product_title = line_item.get("title", "Unknown")
                sku = line_item.get("sku", "No SKU")
                barcode = line_item.get("barcode", "No Barcode")
                _logger.info(f"  📦 Refund Item {i+1}: {product_title} (SKU: {sku}, Barcode: {barcode}, Qty: {quantity})")

            # Shopify REST refunds often do not include top-level `return`.
            # Treat as return when at least one line is marked as restock_type=return.
            has_return_lines = any(
                (line.get("restock_type") or "").lower() == "return"
                for line in refund_line_items
            )
            if not has_return_lines:
                _logger.info(
                    "Shopify Refund ID %s has no return-restock lines. "
                    "Skipping stock return creation.", refund_id
                )
                continue

            # Iterate through each delivered picking to find the corresponding moves
            for picking in pickings:
                _logger.info(f"Checking delivered picking: {picking.name} (ID: {picking.id}) for refund items.")
                product_return_moves_data = []  # List to hold data for stock.return.picking wizard

                for item in refund.get('refund_line_items', []):
                    line_item_id = item.get('line_item', {}).get('id')
                    _logger.info(
                        f"Processing refund line item (Shopify Line Item ID: {line_item_id}): {item.get('line_item', {}).get('title')}")

                    # Ensure the item is marked for restock as 'return'
                    if item.get('restock_type') != 'return':
                        _logger.info(
                            f"Refund line item {line_item_id} has restock_type '{item.get('restock_type')}', not 'return'. Skipping.")
                        continue

                    line_item = item.get("line_item") or {}
                    shopify_product_title = line_item.get('title')
                    shopify_barcode = line_item.get('barcode')
                    shopify_sku = line_item.get('sku')
                    quantity_to_return = item.get('quantity', 0)

                    _logger.info(
                        f"Attempting to match product for return: Title='{shopify_product_title}', Barcode='{shopify_barcode}', SKU='{shopify_sku}', Quantity='{quantity_to_return}'")

                    odoo_product = self.env['product.product']
                    # Prioritize matching by barcode
                    if shopify_barcode:
                        odoo_product = self.env['product.product'].search([('barcode', '=', shopify_barcode)], limit=1)
                        if odoo_product:
                            _logger.info(f"Found Odoo product '{odoo_product.name}' by barcode '{shopify_barcode}'.")

                    # Fallback to SKU if barcode not found or empty
                    if not odoo_product and shopify_sku:
                        odoo_product = self.env['product.product'].search([('default_code', '=', shopify_sku)], limit=1)
                        if odoo_product:
                            _logger.info(f"Found Odoo product '{odoo_product.name}' by SKU '{shopify_sku}'.")

                    # Fallback to product title if neither barcode nor SKU found
                    if not odoo_product and shopify_product_title:
                        odoo_product = self.env['product.product'].search([('name', '=', shopify_product_title)],
                                                                          limit=1)
                        if odoo_product:
                            _logger.info(
                                f"Found Odoo product '{odoo_product.name}' by title '{shopify_product_title}'.")

                    if not odoo_product:
                        _logger.error(
                            f"❌ Could not find Odoo product for Shopify item '{shopify_product_title}' (Barcode: '{shopify_barcode}', SKU: '{shopify_sku}'). Skipping this return line item.")
                        
                        # Search for similar products for debugging
                        similar_products = self.env['product.product'].search([
                            '|', '|', 
                            ('name', 'ilike', shopify_product_title),
                            ('barcode', '!=', False),
                            ('default_code', '!=', False)
                        ], limit=5)
                        _logger.info(f"🔍 Similar products found in Odoo:")
                        for prod in similar_products:
                            _logger.info(f"   - {prod.name} (Barcode: {prod.barcode}, SKU: {prod.default_code})")
                        continue

                    # Find the original outgoing move for this product in the delivered picking
                    picking_moves = getattr(picking, move_field)
                    move_line = picking_moves.filtered(
                        lambda m: m.product_id == odoo_product and m.state == 'done' and m.product_uom_qty >= quantity_to_return
                    )

                    if not move_line:
                        _logger.error(
                            f"❌ No suitable 'done' outgoing move found for product '{odoo_product.name}' (ID: {odoo_product.id}) in picking {picking.name} with enough quantity ({quantity_to_return}). Skipping this return line item.")
                        
                        # Debug: Show all moves for this product in the picking
                        all_moves = picking_moves.filtered(lambda m: m.product_id == odoo_product)
                        _logger.info(f"🔍 All moves for product '{odoo_product.name}' in picking {picking.name}:")
                        for move in all_moves:
                            _logger.info(
                                f"   - Move: {move.name}, State: {move.state}, Qty: {move.product_uom_qty}, "
                                f"Done Qty: {getattr(move, move_done_field, 0.0)}"
                            )
                        
                        if not all_moves:
                            _logger.warning(f"❌ Product '{odoo_product.name}' was not found in any moves of picking {picking.name}")
                        continue

                    # Append data for the return wizard
                    product_return_moves_data.append((0, 0, {
                        'product_id': odoo_product.id,
                        'quantity': quantity_to_return,
                        'move_id': move_line[0].id,  # Use the first suitable move
                    }))
                    _logger.info(
                        f"Prepared return move for product '{odoo_product.name}' (Quantity: {quantity_to_return}).")

                if not product_return_moves_data:
                    _logger.error(
                        f"❌ No valid returnable items found for refund {refund_id} in picking {picking.name}. This means products couldn't be matched or moves couldn't be found. Skipping return creation for this picking.")
                    continue

                # Create the return picking using stock.return.picking wizard
                _logger.info(
                    f"Creating return wizard for picking {picking.name} with {len(product_return_moves_data)} items.")
                return_wizard = self.env['stock.return.picking'].with_context(active_id=picking.id).create({
                    'picking_id': picking.id,
                    'product_return_moves': product_return_moves_data
                })

                if hasattr(return_wizard, 'action_create_returns'):
                    result = return_wizard.action_create_returns()
                else:
                    result = return_wizard.create_returns()
                if isinstance(result, dict) and result.get('res_id'):
                    new_picking = self.env['stock.picking'].browse(result['res_id'])
                    new_picking.x_shopify_refund_id = refund_id
                    new_picking.shopify_order_id = self.shopify_order_id
                    new_picking_moves = getattr(new_picking, move_field)
                    
                    # Auto-process the return picking to generate stock
                    try:
                        _logger.info(f"🚀 Starting auto-processing of return picking: {new_picking.name}")
                        _logger.info(f"   📊 Initial state: {new_picking.state}")
                        
                        # Confirm the return picking
                        if new_picking.state == 'draft':
                            _logger.info(f"🔄 Confirming return picking...")
                            new_picking.action_confirm()
                            _logger.info(f"✅ Confirmed return picking: {new_picking.name} (New state: {new_picking.state})")
                        
                        # Assign stock if available
                        if new_picking.state in ['confirmed', 'waiting']:
                            _logger.info(f"🔄 Assigning stock to return picking...")
                            new_picking.action_assign()
                            _logger.info(f"📦 Assigned stock to return picking: {new_picking.name} (New state: {new_picking.state})")
                        
                        # Force assign if still waiting
                        if new_picking.state == 'waiting':
                            _logger.info(f"⚠️ Picking still in waiting state, trying force assign...")
                            new_picking.action_assign()
                            if new_picking.state == 'waiting':
                                _logger.info(f"🔧 Force assigning by setting move quantities...")
                                for move in new_picking_moves:
                                    if move.state not in ['done', 'cancel']:
                                        setattr(move, move_done_field, move.product_uom_qty)
                        
                        # Set quantities and validate the picking to generate stock
                        if new_picking.state == 'assigned':
                            # Set done quantities for all moves
                            _logger.info(f"📦 Setting quantities for {len(new_picking_moves)} moves...")
                            for move in new_picking_moves:
                                move_label = getattr(move, "name", False) or getattr(move, "display_name", False) or move.product_id.display_name
                                _logger.info(f"   📝 Move: {move_label}, Product: {move.product_id.name}")
                                _logger.info(
                                    f"   📝 Before - Qty: {move.product_uom_qty}, "
                                    f"Done: {getattr(move, move_done_field, 0.0)}"
                                )
                                setattr(move, move_done_field, move.product_uom_qty)
                                _logger.info(
                                    f"   📝 After - Qty: {move.product_uom_qty}, "
                                    f"Done: {getattr(move, move_done_field, 0.0)}"
                                )
                            
                            # Check stock levels before validation
                            _logger.info(f"🔍 Stock levels BEFORE validation:")
                            for move in new_picking_moves:
                                product = move.product_id
                                stock_qty = product.qty_available
                                _logger.info(f"   📊 {product.name}: {stock_qty} units available")
                            
                            # Validate the picking to complete the return and generate stock
                            _logger.info(f"🔄 Validating return picking {new_picking.name}...")
                            new_picking.button_validate()
                            _logger.info(f"✅ Validated return picking: {new_picking.name}")
                            
                            # Check stock levels after validation
                            _logger.info(f"🔍 Stock levels AFTER validation:")
                            for move in new_picking_moves:
                                product = move.product_id
                                stock_qty = product.qty_available
                                _logger.info(f"   📊 {product.name}: {stock_qty} units available")
                                
                            _logger.info(f"🎉 Stock generation completed for return picking: {new_picking.name}")
                        else:
                            # Fallback: Try to validate even if not in 'assigned' state
                            _logger.warning(f"⚠️ Return picking {new_picking.name} is in state '{new_picking.state}' instead of 'assigned'")
                            _logger.info(f"🔧 Attempting fallback validation...")
                            
                            # Set quantities for all moves regardless of state
                            for move in new_picking_moves:
                                if move.state not in ['done', 'cancel']:
                                    _logger.info(f"   📝 Setting quantity_done for {move.product_id.name}: {move.product_uom_qty}")
                                    setattr(move, move_done_field, move.product_uom_qty)
                            
                            # Try to validate
                            try:
                                new_picking.button_validate()
                                _logger.info(f"✅ Fallback validation successful for {new_picking.name}")
                            except Exception as validate_error:
                                _logger.error(f"❌ Fallback validation failed for {new_picking.name}: {str(validate_error)}")
                        
                    except Exception as e:
                        _logger.error(f"❌ Error auto-processing return picking {new_picking.name}: {str(e)}")
                        import traceback
                        _logger.error(f"❌ Full traceback: {traceback.format_exc()}")
                    
                    created_returns_count += 1
                    _logger.info(
                        f"Successfully created new return picking: {new_picking.name} (ID: {new_picking.id}) for Shopify Refund ID {refund_id}.")

                    # CLEANUP: Delete all other draft/ready/etc. returns for this shopify order except this one
                    # This logic is a bit aggressive. Ensure it's desired.
                    # It cancels and unlinks any other *incoming* pickings for the same Shopify order
                    # that are not yet done and are not the newly created one.
                    duplicate_returns = self.env['stock.picking'].search([
                        ('shopify_order_id', '=', self.shopify_order_id),
                        ('picking_type_id.code', '=', 'incoming'),
                        ('id', '!=', new_picking.id),
                        ('state', 'in', ['draft', 'waiting', 'confirmed', 'assigned', 'ready'])
                    ])
                    if duplicate_returns:
                        _logger.info(
                            f"Found {len(duplicate_returns)} duplicate incoming returns for Shopify Order {self.shopify_order_id}. Attempting to cancel and unlink.")
                        for dup in duplicate_returns:
                            _logger.info(f"Cancelling and unlinking duplicate return: {dup.name} (ID: {dup.id})")
                            try:
                                dup.action_cancel()
                                dup.unlink()
                            except Exception as e:
                                _logger.error(f"Error cancelling/unlinking duplicate return {dup.name}: {e}",
                                              exc_info=True)
                else:
                    _logger.error(
                        f"Failed to create return picking for Shopify Refund ID {refund_id}. Wizard returned: {result}")

        # Clean up any auto-created draft outgoing pickings (unwanted 3rd delivery)
        # This logic is also aggressive. Ensure it's desired.
        auto_out_picks = self.env['stock.picking'].search([
            ('shopify_order_id', '=', self.shopify_order_id),
            ('picking_type_id.code', '=', 'outgoing'),
            ('state', '=', 'draft'),
            ('x_shopify_refund_id', '=', False),  # Ensure it's not a return picking that got miscoded
        ])
        if auto_out_picks:
            _logger.info(
                f"Found {len(auto_out_picks)} auto-created draft outgoing pickings for Shopify Order {self.shopify_order_id}. Attempting to cancel and unlink.")
            for auto_pick in auto_out_picks:
                _logger.info(
                    f"Cancelling and unlinking auto-created outgoing picking: {auto_pick.name} (ID: {auto_pick.id})")
                try:
                    auto_pick.action_cancel()
                    auto_pick.unlink()
                except Exception as e:
                    _logger.error(f"Error cancelling/unlinking auto-created outgoing picking {auto_pick.name}: {e}",
                                  exc_info=True)

        if not created_returns_count:
            _logger.info(f"📊 RETURN SYNC SUMMARY for order {self.shopify_order_id}:")
            _logger.info(f"   • Total refunds found in Shopify: {len(refunds)}")
            _logger.info(f"   • Returns created in this sync: {created_returns_count}")
            _logger.info(f"   • Possible reasons: Already synced, no returnable items, or processing errors")
            
            # Check existing return pickings for this order
            existing_returns = self.env['stock.picking'].search([
                ('shopify_order_id', '=', self.shopify_order_id),
                ('picking_type_id.code', '=', 'incoming')
            ])
            _logger.info(f"   • Existing return pickings in Odoo: {len(existing_returns)}")
            for ret in existing_returns:
                _logger.info(f"     - {ret.name} (State: {ret.state}, Refund ID: {ret.x_shopify_refund_id})")
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Shopify Return Sync Complete'),
                    'message': _(f'Found {len(refunds)} refund(s) in Shopify, but no new returns were created.\n\n'
                               f'Possible reasons:\n'
                               f'• Returns already synced ({len(existing_returns)} existing return pickings)\n'
                               f'• No returnable items found\n'
                               f'• Items already returned\n\n'
                               f'Check the logs for detailed information.'),
                    'type': 'info',
                    'sticky': True
                }
            }

        # Align with actual selection values on the current sale.order model.
        if "delivery_status" in self._fields:
            delivery_values = {value for value, _label in (self._fields["delivery_status"].selection or [])}
            if "restocked" in delivery_values:
                self.delivery_status = "restocked"
        self.is_approved = True  # Mark the sale order as approved for return sync

        _logger.info(
            f"Successfully synced {created_returns_count} Shopify return(s) for order {self.shopify_order_id}.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('Shopify return(s) synced!'),
                'type': 'success'
            }
        }
