from odoo import models, fields
import requests
import logging
from datetime import datetime
import time

_logger = logging.getLogger(__name__)


class ShopifySaleImport(models.Model):
    _name = 'shopify.sale.paid.order'
    _description = 'Import Shopify Paid/Partially Paid Orders into Odoo'

    _SHOPIFY_INVOICE_STATUSES = {"paid", "partially_paid"}
    _SHOPIFY_FULLY_PAID_STATUSES = {"paid"}
    _SHOPIFY_FULFILLED_STATUSES = {"fulfilled"}

    def _log_sync(self, **vals):
        try:
            self.env['shopify.sync.log'].log_event(**vals)
        except Exception:
            _logger.debug("Failed to persist order sync log", exc_info=True)

    def _get_target_instances(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            instance = self.env["shopify.instance"].browse(instance_id).exists()
            return instance
        instances = self.env["shopify.instance"].search([("state", "=", "confirm")])
        if not instances:
            instances = self.env["shopify.instance"].search([])
        return instances

    def _find_existing_sale_order(self, instance, shopify_id, origin=None):
        domain = [
            ("shopify_order_id", "=", str(shopify_id)),
            ("shopify_instance_id", "=", instance.id),
        ]
        existing = self.env["sale.order"].search(domain, limit=1)
        if existing:
            return existing
        if origin:
            existing = self.env["sale.order"].search([
                ("origin", "=", origin),
                ("shopify_instance_id", "=", instance.id),
            ], limit=1)
        return existing

    def _is_shopify_paid(self, order_data):
        status = (order_data.get("financial_status") or "").lower()
        return status in self._SHOPIFY_INVOICE_STATUSES

    def _is_shopify_fully_paid(self, order_data):
        status = (order_data.get("financial_status") or "").lower()
        return status in self._SHOPIFY_FULLY_PAID_STATUSES

    def _is_shopify_fulfilled(self, order_data):
        status = (order_data.get("fulfillment_status") or "").lower()
        return status in self._SHOPIFY_FULFILLED_STATUSES

    def _register_full_payment(self, invoice):
        if invoice.amount_residual <= 0:
            return
        journal = self.env["account.journal"].search([
            ("type", "in", ["bank", "cash"]),
            ("company_id", "=", invoice.company_id.id),
        ], limit=1)
        if not journal:
            _logger.warning("No bank/cash journal found to auto-pay invoice %s", invoice.name)
            return
        ctx = {
            "active_model": "account.move",
            "active_ids": invoice.ids,
            "active_id": invoice.id,
        }
        wizard = self.env["account.payment.register"].with_context(ctx).create({
            "journal_id": journal.id,
            "amount": invoice.amount_residual,
            "payment_date": fields.Date.context_today(invoice),
        })
        wizard.action_create_payments()

    def _link_shopify_invoices(self, sale_order, invoices):
        if not sale_order:
            return
        shopify_order_id = sale_order.shopify_order_id
        instance = sale_order.shopify_instance_id
        if not shopify_order_id and not instance:
            return
        has_instance_field = "shopify_instance_id" in self.env["account.move"]._fields
        for invoice in invoices:
            vals = {}
            if shopify_order_id and not invoice.shopify_order_id:
                vals["shopify_order_id"] = str(shopify_order_id)
            if has_instance_field and instance and not getattr(invoice, "shopify_instance_id", False):
                vals["shopify_instance_id"] = instance.id
            if vals:
                invoice.write(vals)

    def _validate_delivery_pickings(self, sale_order):
        move_line_model = self.env["stock.move.line"]
        ml_has_quantity = "quantity" in move_line_model._fields
        ml_has_qty_done = "qty_done" in move_line_model._fields
        move_has_quantity = "quantity" in self.env["stock.move"]._fields
        move_has_quantity_done = "quantity_done" in self.env["stock.move"]._fields
        picking_has_move_ids_without_package = "move_ids_without_package" in self.env["stock.picking"]._fields

        pickings = sale_order.picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing" and p.state not in ("done", "cancel")
        )
        for picking in pickings:
            stock_moves = (
                picking.move_ids_without_package
                if picking_has_move_ids_without_package
                else picking.move_ids
            )
            if picking.state == "draft":
                picking.action_confirm()
            if picking.state in ("confirmed", "waiting", "partially_available"):
                picking.action_assign()
            for move in stock_moves:
                qty_to_set = move.product_uom_qty
                if move_has_quantity:
                    move.quantity = qty_to_set
                elif move_has_quantity_done:
                    move.quantity_done = qty_to_set

                # Ensure at least one move line exists so validation can complete even
                # when stock is not reserved automatically.
                if not move.move_line_ids:
                    ml_vals = {
                        "move_id": move.id,
                        "picking_id": picking.id,
                        "product_id": move.product_id.id,
                        "location_id": move.location_id.id,
                        "location_dest_id": move.location_dest_id.id,
                    }
                    if "product_uom_id" in move_line_model._fields:
                        ml_vals["product_uom_id"] = move.product_uom.id
                    if ml_has_quantity:
                        ml_vals["quantity"] = qty_to_set
                    elif ml_has_qty_done:
                        ml_vals["qty_done"] = qty_to_set
                    move_line_model.create(ml_vals)
                else:
                    first = True
                    for move_line in move.move_line_ids:
                        line_qty = qty_to_set if first else 0.0
                        if ml_has_quantity:
                            move_line.quantity = line_qty
                        elif ml_has_qty_done:
                            move_line.qty_done = line_qty
                        first = False

            if picking.state in ("assigned", "confirmed", "waiting", "partially_available"):
                validate_result = picking.with_context(skip_immediate=True, skip_backorder=True).button_validate()
                if isinstance(validate_result, dict):
                    wizard_model = validate_result.get("res_model")
                    wizard_id = validate_result.get("res_id")
                    if wizard_model and wizard_id:
                        wizard = self.env[wizard_model].browse(wizard_id).exists()
                        if wizard and hasattr(wizard, "process"):
                            wizard.process()

    def _resolve_workflow_rule(self, instance, order_data):
        active_rules = instance.webhook_workflow_configuration_ids.filtered("active")
        if not active_rules:
            return self.env["shopify.webhook.workflow.configuration"]
        return instance._get_matching_webhook_workflow_rule(order_data)

    def _apply_webhook_workflow(self, sale_order, order_data, instance, workflow_rule=None):
        active_rules = instance.webhook_workflow_configuration_ids.filtered("active")
        if active_rules and not workflow_rule:
            workflow_rule = self._resolve_workflow_rule(instance, order_data)
        if active_rules and not workflow_rule:
            _logger.info(
                "No webhook workflow rule matched for order %s on instance %s. Keeping order without workflow actions.",
                order_data.get("id"), instance.name
            )
            return

        if workflow_rule:
            should_confirm = bool(workflow_rule.auto_confirm_order or workflow_rule.create_invoice)
            should_create_invoice = bool(workflow_rule.create_invoice)
            should_register_payment = bool(workflow_rule.register_payment)
            should_validate_delivery = bool(workflow_rule.validate_delivery)
        else:
            should_confirm = bool(instance.auto_confirm_imported_orders or instance.auto_confirm_and_create_invoice)
            should_create_invoice = bool(instance.auto_confirm_and_create_invoice)
            should_register_payment = self._is_shopify_fully_paid(order_data)
            should_validate_delivery = self._is_shopify_fulfilled(order_data)

        if should_confirm and sale_order.state in ("draft", "sent"):
            sale_order.action_confirm()

        if sale_order.state not in ("sale", "done"):
            return

        invoices = sale_order.invoice_ids.filtered(lambda inv: inv.move_type == "out_invoice")
        if should_create_invoice and not invoices:
            invoices = sale_order._create_invoices()
        self._link_shopify_invoices(sale_order, invoices)

        allow_paid_invoice_flow = self._is_shopify_paid(order_data) and (bool(not workflow_rule) or should_create_invoice)
        if allow_paid_invoice_flow:
            if not invoices:
                invoices = sale_order._create_invoices()
            self._link_shopify_invoices(sale_order, invoices)
            draft_invoices = invoices.filtered(lambda inv: inv.state == "draft")
            if draft_invoices:
                draft_invoices.action_post()

        if should_register_payment and self._is_shopify_fully_paid(order_data):
            invoices = sale_order.invoice_ids.filtered(lambda inv: inv.move_type == "out_invoice")
            for invoice in invoices.filtered(lambda inv: inv.state == "posted"):
                try:
                    self._register_full_payment(invoice)
                except Exception as err:
                    _logger.warning("Auto payment failed for invoice %s: %s", invoice.name, err)

        if should_validate_delivery:
            try:
                self._validate_delivery_pickings(sale_order)
            except Exception as err:
                _logger.warning("Auto delivery validation failed for %s: %s", sale_order.name, err)

    def import_paid_shopify_sale_orders(self):
        """
        Imports paid or partially paid orders from Shopify into Odoo.
        It fetches orders, updates existing ones, or creates new sale orders
        along with customers and products if they don't exist in Odoo.
        """
        if not self.env.context.get("shopify_instance_id"):
            instances = self._get_target_instances()
            if len(instances) > 1:
                for instance in instances:
                    self.with_context(shopify_instance_id=instance.id).import_paid_shopify_sale_orders()
                return

        instance = self._get_target_instances()[:1]
        if not instance:
            _logger.error(" No Shopify instance found in the system. Please configure one.")
            self._log_sync(
                operation="Import Paid Orders",
                message="Import aborted. No Shopify instance configured.",
                status="failed",
                level="error",
                channel="import",
            )
            return

        shopify_domain = instance.shopify_domain
        access_token = instance.shopify_token

        # Shopify API endpoint for orders
        url = f"https://{shopify_domain}/admin/api/2023-04/orders.json"
        headers = {
            'X-Shopify-Access-Token': access_token,
            'Content-Type': 'application/json',
        }

        params = instance.get_order_import_query_params()
        if "financial_status" not in params:
            params["financial_status"] = "paid,partially_paid,unpaid"

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
        except requests.exceptions.RequestException as e:
            _logger.error(f" Error fetching Shopify Paid Orders: {e}")
            self._log_sync(
                operation="Import Paid Orders",
                message=f"Shopify API request failed: {e}",
                status="failed",
                level="error",
                channel="import",
                instance_id=instance.id,
            )
            return
        except Exception as e:
            _logger.error(f" An unexpected error occurred while fetching Shopify Paid Orders: {e}")
            self._log_sync(
                operation="Import Paid Orders",
                message=f"Unexpected error while fetching paid orders: {e}",
                status="failed",
                level="error",
                channel="import",
                instance_id=instance.id,
            )
            return

        orders_data = response.json().get('orders', [])
        _logger.info(f"📦 Fetched {len(orders_data)} paid/partially_paid orders from Shopify.")
        self._log_sync(
            operation="Import Paid Orders",
            message=f"Fetched {len(orders_data)} paid/partially paid orders from Shopify.",
            status="info",
            level="info",
            channel="import",
            instance_id=instance.id,
            payload={"count": len(orders_data)},
        )
        
        # Check if orders have shipping data
        orders_with_shipping = [o for o in orders_data if o.get('shipping_lines')]
        _logger.info(f"📊 Orders with shipping data: {len(orders_with_shipping)} out of {len(orders_data)}")
        
        if not orders_with_shipping and orders_data:
            _logger.warning(f"⚠️ No orders have shipping_lines data. This might indicate an API version issue.")
            _logger.info(f"🔍 First order sample keys: {list(orders_data[0].keys()) if orders_data else 'No orders'}")
            # Try alternative API version if no shipping data
            alt_url = f"https://{shopify_domain}/admin/api/2023-10/orders.json"
            _logger.info(f"🔄 Trying alternative API version: 2023-10")
            alt_response = requests.get(alt_url, headers=headers, params=params)
            if alt_response.status_code == 200:
                alt_orders = alt_response.json().get('orders', [])
                alt_orders_with_shipping = [o for o in alt_orders if o.get('shipping_lines')]
                _logger.info(f"📊 Alternative API: Orders with shipping data: {len(alt_orders_with_shipping)} out of {len(alt_orders)}")
                if alt_orders_with_shipping:
                    _logger.info(f" Alternative API version has shipping data. Consider updating to 2023-10")
                    orders_data = alt_orders  # Use the alternative version

        for order in orders_data:
            workflow_rule = self._resolve_workflow_rule(instance, order)
            if instance.webhook_workflow_configuration_ids.filtered("active") and not workflow_rule:
                _logger.info(
                    "Skipping Shopify order %s because no webhook workflow rule matched (payment=%s, delivery=%s).",
                    order.get("id"),
                    order.get("financial_status"),
                    order.get("fulfillment_status"),
                )
                continue

            shopify_id = order.get('id')
            origin = f"Shopify-{shopify_id}"
            payment_status = order.get('financial_status', 'pending')
            shopify_order_url = f"https://{shopify_domain}/admin/orders/{shopify_id}"

            # Debug shipping information
            _logger.info(f"🔍 DEBUG: Processing order {shopify_id}")
            if order.get('shipping_lines'):
                _logger.info(f"🔍 DEBUG: Order {shopify_id} has shipping_lines: {order.get('shipping_lines')}")
                for i, shipping in enumerate(order.get('shipping_lines', [])):
                    _logger.info(f"🔍 DEBUG: Shipping line {i}: {shipping}")
            else:
                _logger.warning(f"⚠️ Order {shopify_id} has NO shipping_lines data")
                _logger.info(f"🔍 DEBUG: Available order keys: {list(order.keys())}")

            # Extract tracking info from fulfillments
            fulfillments = order.get('fulfillments', [])
            tracking_number = ""
            delivery_partner_name = ""
            if fulfillments:
                # Get the latest fulfillment for tracking info
                latest_fulfillment = sorted(fulfillments, key=lambda f: f.get('created_at', ''), reverse=True)[0]
                tracking_number = latest_fulfillment.get('tracking_number', '')
                delivery_partner_name = latest_fulfillment.get('tracking_company', '')

            # Check if order already exists in Odoo
            existing_order = self._find_existing_sale_order(instance, shopify_id, origin=origin)
            if (
                not existing_order
                and not instance.webhook_workflow_configuration_ids.filtered("active")
                and not instance.is_order_allowed_for_import(order)
            ):
                _logger.info(
                    "Skipping Shopify order %s due to workflow filters (payment=%s, delivery=%s).",
                    shopify_id,
                    order.get("financial_status"),
                    order.get("fulfillment_status"),
                )
                continue

            if existing_order:
                # Update existing order details if they have changed
                vals_to_update = {}
                if existing_order.tracking_number != tracking_number:
                    vals_to_update['tracking_number'] = tracking_number
                
                # Handle delivery partner as Many2one field
                delivery_partner_id = None
                if delivery_partner_name:
                    delivery_partner_id = self._get_or_create_delivery_partner(delivery_partner_name)
                
                if delivery_partner_id and (not existing_order.delivery_partner_id or existing_order.delivery_partner_id.id != delivery_partner_id):
                    vals_to_update['delivery_partner_id'] = delivery_partner_id
                if existing_order.shopify_payment_status != payment_status:
                    vals_to_update['shopify_payment_status'] = payment_status
                if existing_order.shopify_order_url != shopify_order_url:
                    vals_to_update['shopify_order_url'] = shopify_order_url

                if vals_to_update:
                    existing_order.write(vals_to_update)
                    _logger.info(f" Updated info for existing order: {existing_order.name}")
                    self._log_sync(
                        operation="Import Order",
                        message=f"Updated existing order {existing_order.name} from Shopify.",
                        status="success",
                        level="info",
                        channel="import",
                        model_name="sale.order",
                        res_id=existing_order.id,
                        reference=str(shopify_id),
                        instance_id=instance.id,
                    )

                # Update or add order lines for existing order
                for line in order.get('line_items', []):
                    # Prepare line values, including product search logic
                    updated_vals = self._prepare_order_line(line)
                    product_id_from_line = updated_vals.get('product_id')

                    if not product_id_from_line:
                        # Log an error if product was not found for an existing order update
                        _logger.error(
                            f" Skipping line item '{line.get('title')}' (Shopify Barcode: {line.get('barcode')}) for existing order {existing_order.name} - Product not found in Odoo."
                        )
                        continue

                    # Filter existing order lines for the product
                    order_lines = existing_order.order_line.filtered(lambda l: l.product_id.id == product_id_from_line)

                    if order_lines:
                        # Update existing order line
                        for ol in order_lines:
                            need_update = (
                                    ol.product_uom_qty != updated_vals['product_uom_qty'] or
                                    ol.price_unit != updated_vals['price_unit'] or
                                    ol.shopify_line_id != updated_vals['shopify_line_id']
                            )
                            if need_update:
                                ol.write({
                                    'product_uom_qty': updated_vals['product_uom_qty'],
                                    'price_unit': updated_vals['price_unit'],
                                    'shopify_line_id': updated_vals['shopify_line_id'],
                                })
                                _logger.info(
                                    f"🔁 Updated product '{line.get('title')}' in existing order {existing_order.name}")
                    else:
                        # Add new product line to existing order
                        updated_vals['order_id'] = existing_order.id
                        self.env['sale.order.line'].create(updated_vals)
                        _logger.info(
                            f"➕ Added new product '{line.get('title')}' to existing order {existing_order.name}")
                self._apply_webhook_workflow(existing_order, order, instance, workflow_rule=workflow_rule)
                continue  # Move to the next Shopify order

            # If order does not exist, create a new one
            partner_id = self._get_or_create_customer(order)
            if not partner_id:
                _logger.warning(f"Skipping order {shopify_id} - No valid customer found or created.")
                continue

            # Format order creation date
            created_at = order.get('created_at')
            formatted_created_at = fields.Datetime.now()
            if created_at:
                try:
                    created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    formatted_created_at = created_at_dt.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    _logger.warning(
                        f"Invalid date format for order {shopify_id}: {created_at}. Using current datetime.")
                    pass  # Fallback to current datetime if parsing fails

            # Prepare order lines for new sale order
            order_lines_vals = []
            for line in order.get('line_items', []):
                line_vals = self._prepare_order_line(line)
                if line_vals.get('product_id'):  # Only add if product was found
                    order_lines_vals.append((0, 0, line_vals))
                else:
                    # _prepare_order_line already logs the error if product is not found.
                    # This block ensures the line is skipped if product_id is missing.
                    pass

            # Add shipping line
            shipping_line = self._prepare_shipping_line(order)
            if shipping_line:
                order_lines_vals.append((0, 0, shipping_line))
                _logger.info(f"🚚 Added shipping charge: Rs {shipping_line['price_unit']}")
                _logger.info(f"🔍 DEBUG: Shipping line added to order_lines_vals. Total lines now: {len(order_lines_vals)}")
            else:
                _logger.warning(f"⚠️ No shipping line created for order {shopify_id}")

            # Add discount line
            discount_line = self._prepare_discount_line(order)
            if discount_line:
                order_lines_vals.append((0, 0, discount_line))
                _logger.info(f"💰 Added discount: Rs {abs(discount_line['price_unit'])}")
                _logger.info(f"🔍 DEBUG: Discount line added to order_lines_vals. Total lines now: {len(order_lines_vals)}")
            else:
                _logger.info(f"ℹ️ No discount line created for order {shopify_id}")

            # Add tax line - COMMENTED OUT
            # tax_line = self._prepare_tax_line(order)
            # if tax_line:
            #     order_lines_vals.append((0, 0, tax_line))
            #     _logger.info(f"💰 Added tax charge: Rs {tax_line['price_unit']}")
            #     _logger.info(f"🔍 DEBUG: Tax line added to order_lines_vals. Total lines now: {len(order_lines_vals)}")
            # else:
            #     _logger.warning(f"⚠️ No tax line created for order {shopify_id}")

            _logger.info(f"🔍 DEBUG: Final order_lines_vals structure: {order_lines_vals}")
            _logger.info(f"🔍 DEBUG: Number of order lines: {len(order_lines_vals)}")

            if not order_lines_vals:
                _logger.warning(f"Skipping order {shopify_id} - No valid order lines to create (barcode match only).")
                continue

            duplicate_sale = self._find_existing_sale_order(instance, shopify_id, origin=origin)
            if duplicate_sale:
                _logger.info(
                    "Duplicate guard: existing sale order %s already mapped to Shopify order %s on instance %s.",
                    duplicate_sale.name, shopify_id, instance.id
                )
                continue

            sale_order = self.env['sale.order'].create({
                'partner_id': partner_id,
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

            # Debug: Check what was actually created
            _logger.info(f"🔍 DEBUG: Sale order created with ID: {sale_order.id}")
            _logger.info(f"🔍 DEBUG: Sale order name: {sale_order.name}")
            _logger.info(f"🔍 DEBUG: Number of order lines in created order: {len(sale_order.order_line)}")
            _logger.info(f"🔍 DEBUG: Order line details:")
            for i, line in enumerate(sale_order.order_line):
                _logger.info(f"   Line {i+1}: Product: {line.product_id.name}, Qty: {line.product_uom_qty}, Price: {line.price_unit}, Name: {line.name}")

            self._apply_webhook_workflow(sale_order, order, instance, workflow_rule=workflow_rule)
            _logger.info(
                f"🆕 Created Sale Order: {sale_order.name} (Shopify ID: {shopify_id}) with status: {payment_status}")
            self._log_sync(
                operation="Import Order",
                message=f"Created sale order {sale_order.name} from Shopify order {shopify_id}.",
                status="success",
                level="info",
                channel="import",
                model_name="sale.order",
                res_id=sale_order.id,
                reference=str(shopify_id),
                instance_id=instance.id,
            )

    def import_single_shopify_order(self, order):
        instance = self._get_target_instances()[:1]
        if not instance:
            _logger.error(" No Shopify instance found in the system. Please configure one.")
            self._log_sync(
                operation="Webhook Order Sync",
                message="Webhook sync aborted. No Shopify instance configured.",
                status="failed",
                level="error",
                channel="webhook",
            )
            return

        workflow_rule = self._resolve_workflow_rule(instance, order)
        if instance.webhook_workflow_configuration_ids.filtered("active") and not workflow_rule:
            _logger.info(
                "Webhook order %s skipped. No webhook workflow rule matched (payment=%s, delivery=%s).",
                order.get("id"),
                order.get("financial_status"),
                order.get("fulfillment_status"),
            )
            self._log_sync(
                operation="Webhook Order Sync",
                message=f"Skipped Shopify order {order.get('id')}. No webhook workflow rule matched.",
                status="warning",
                level="warning",
                channel="webhook",
                reference=str(order.get("id") or ""),
                instance_id=instance.id,
            )
            return

        if (
            not instance.webhook_workflow_configuration_ids.filtered("active")
            and not instance.is_order_allowed_for_import(order)
        ):
            _logger.info(
                "Webhook order %s skipped by workflow filters (payment=%s, delivery=%s).",
                order.get("id"),
                order.get("financial_status"),
                order.get("fulfillment_status"),
            )
            self._log_sync(
                operation="Webhook Order Sync",
                message=f"Skipped Shopify order {order.get('id')} by workflow filters.",
                status="warning",
                level="warning",
                channel="webhook",
                reference=str(order.get("id") or ""),
                instance_id=instance.id,
            )
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

        # Check if order already exists in Odoo
        existing_order = self._find_existing_sale_order(instance, shopify_id, origin=origin)

        if existing_order:
            # --- SYNC RETURN/EXCHANGE BEFORE UPDATING ORDER LINES ---
            try:
                existing_order.action_sync_shopify_return()
            except Exception as e:
                _logger.warning(f"Return sync failed: {e}")
            try:
                existing_order.action_sync_shopify_exchange()
            except Exception as e:
                _logger.warning(f"Exchange sync failed: {e}")

            vals_to_update = {}
            if existing_order.tracking_number != tracking_number:
                vals_to_update['tracking_number'] = tracking_number
            
            # Handle delivery partner as Many2one field
            delivery_partner_id = None
            if delivery_partner_name:
                delivery_partner_id = self._get_or_create_delivery_partner(delivery_partner_name)
            
            if delivery_partner_id and (not existing_order.delivery_partner_id or existing_order.delivery_partner_id.id != delivery_partner_id):
                vals_to_update['delivery_partner_id'] = delivery_partner_id
            if existing_order.shopify_payment_status != payment_status:
                vals_to_update['shopify_payment_status'] = payment_status
            if existing_order.shopify_order_url != shopify_order_url:
                vals_to_update['shopify_order_url'] = shopify_order_url

            if vals_to_update:
                existing_order.write(vals_to_update)
                _logger.info(f" Updated info for existing order: {existing_order.name}")

            # Update or add order lines for existing order
            for line in order.get('line_items', []):
                updated_vals = self._prepare_order_line(line)
                product_id_from_line = updated_vals.get('product_id')

                if not product_id_from_line:
                    _logger.error(
                        f" Skipping line item '{line.get('title')}' (Shopify Barcode: {line.get('barcode')}) for existing order {existing_order.name} - Product not found in Odoo."
                    )
                    continue

                order_lines = existing_order.order_line.filtered(lambda l: l.product_id.id == product_id_from_line)

                if order_lines:
                    for ol in order_lines:
                        need_update = (
                                ol.product_uom_qty != updated_vals['product_uom_qty'] or
                                ol.price_unit != updated_vals['price_unit'] or
                                ol.shopify_line_id != updated_vals['shopify_line_id']
                        )
                        if need_update:
                            ol.write({
                                'product_uom_qty': updated_vals['product_uom_qty'],
                                'price_unit': updated_vals['price_unit'],
                                'shopify_line_id': updated_vals['shopify_line_id'],
                            })
                            _logger.info(
                                f"🔁 Updated product '{line.get('title')}' in existing order {existing_order.name}")
                else:
                    updated_vals['order_id'] = existing_order.id
                    self.env['sale.order.line'].create(updated_vals)
                    _logger.info(
                        f"➕ Added new product '{line.get('title')}' to existing order {existing_order.name}")
            
            # Handle shipping charges for existing orders
            self._update_shipping_charges_for_existing_order(existing_order, order)
            self._apply_webhook_workflow(existing_order, order, instance, workflow_rule=workflow_rule)
            self._log_sync(
                operation="Webhook Order Sync",
                message=f"Updated existing order {existing_order.name} from Shopify webhook.",
                status="success",
                level="info",
                channel="webhook",
                model_name="sale.order",
                res_id=existing_order.id,
                reference=str(shopify_id),
                instance_id=instance.id,
            )
            return  # Done updating existing order

        # If order does not exist, create a new one
        partner_id = self._get_or_create_customer(order)
        if not partner_id:
            _logger.warning(f"Skipping order {shopify_id} - No valid customer found or created.")
            self._log_sync(
                operation="Webhook Order Sync",
                message=f"Skipped Shopify order {shopify_id}. Could not resolve customer.",
                status="warning",
                level="warning",
                channel="webhook",
                reference=str(shopify_id),
                instance_id=instance.id,
            )
            return

        created_at = order.get('created_at')
        formatted_created_at = fields.Datetime.now()
        if created_at:
            try:
                created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                formatted_created_at = created_at_dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                _logger.warning(
                    f"Invalid date format for order {shopify_id}: {created_at}. Using current datetime.")
                pass

        order_lines_vals = []
        for line in order.get('line_items', []):
            line_vals = self._prepare_order_line(line)
            if line_vals.get('product_id'):
                order_lines_vals.append((0, 0, line_vals))
            else:
                pass

        # Add shipping lines (handle multiple shipping lines)
        shipping_lines = self._prepare_all_shipping_lines(order)
        for shipping_line in shipping_lines:
            order_lines_vals.append((0, 0, shipping_line))
            _logger.info(f"🚚 Added shipping charge: Rs {shipping_line['price_unit']} - {shipping_line['name']}")
        _logger.info(f"🔍 DEBUG: Added {len(shipping_lines)} shipping lines. Total lines now: {len(order_lines_vals)}")

        # Add discount line
        discount_line = self._prepare_discount_line(order)
        if discount_line:
            order_lines_vals.append((0, 0, discount_line))
            _logger.info(f"💰 Added discount: Rs {abs(discount_line['price_unit'])}")
            _logger.info(f"🔍 DEBUG: Discount line added to order_lines_vals. Total lines now: {len(order_lines_vals)}")
        else:
            _logger.info(f"ℹ️ No discount line created for order {shopify_id}")

        # Add tax line - COMMENTED OUT
        # tax_line = self._prepare_tax_line(order)
        # if tax_line:
        #     order_lines_vals.append((0, 0, tax_line))
        #     _logger.info(f"💰 Added tax charge: Rs {tax_line['price_unit']}")
        #     _logger.info(f"🔍 DEBUG: Tax line added to order_lines_vals. Total lines now: {len(order_lines_vals)}")
        # else:
        #     _logger.warning(f"⚠️ No tax line created for order {shopify_id}")

        _logger.info(f"🔍 DEBUG: Final order_lines_vals structure: {order_lines_vals}")
        _logger.info(f"🔍 DEBUG: Number of order lines: {len(order_lines_vals)}")

        if not order_lines_vals:
            _logger.warning(f"Skipping order {shopify_id} - No valid order lines to create.")
            return

        duplicate_sale = self._find_existing_sale_order(instance, shopify_id, origin=origin)
        if duplicate_sale:
            _logger.info(
                "Duplicate guard: existing sale order %s already mapped to Shopify order %s on instance %s.",
                duplicate_sale.name, shopify_id, instance.id
            )
            self._log_sync(
                operation="Webhook Order Sync",
                message=f"Skipped duplicate create for Shopify order {shopify_id}. Existing order: {duplicate_sale.name}",
                status="info",
                level="info",
                channel="webhook",
                model_name="sale.order",
                res_id=duplicate_sale.id,
                reference=str(shopify_id),
                instance_id=instance.id,
            )
            return

        sale_order = self.env['sale.order'].create({
            'partner_id': partner_id,
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

        self._apply_webhook_workflow(sale_order, order, instance, workflow_rule=workflow_rule)
        _logger.info(
            f"🆕 Created Sale Order: {sale_order.name} (Shopify ID: {shopify_id}) with status: {payment_status}")
        self._log_sync(
            operation="Webhook Order Sync",
            message=f"Created order {sale_order.name} from Shopify webhook.",
            status="success",
            level="info",
            channel="webhook",
            model_name="sale.order",
            res_id=sale_order.id,
            reference=str(shopify_id),
            instance_id=instance.id,
        )

        # --- SYNC RETURN/EXCHANGE AFTER CONFIRMING NEW ORDER ---
        try:
            sale_order.action_sync_shopify_return()
        except Exception as e:
            _logger.warning(f"Return sync failed: {e}")
        try:
            sale_order.action_sync_shopify_exchange()
        except Exception as e:
            _logger.warning(f"Exchange sync failed: {e}")

    def _get_or_create_customer(self, order):
        """
        Gets an existing customer or creates a new one in Odoo based on Shopify order data.
        """
        customer = order.get('customer')
        if not customer:
            _logger.warning(f"No customer data found for Shopify order {order.get('id')}.")
            return False

        email = customer.get('email')
        name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        phone = customer.get('phone') or ''
        default_address = customer.get('default_address', {})
        street = default_address.get('address1') or ''
        street2 = default_address.get('address2') or ''
        city = default_address.get('city') or ''
        zip_code = default_address.get('zip') or ''
        state_name = default_address.get('province') or ''
        country_name = default_address.get('country') or ''

        if not email:
            _logger.warning(f"Customer email missing for Shopify order {order.get('id')}. Cannot create/find customer.")
            return False

        # Search for existing partner by email
        partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
        if partner:
            _logger.info(f"👤 Found existing customer: {partner.name} ({email})")
            return partner.id

        # Get country and state IDs
        country_id = self.env['res.country'].search([('name', '=', country_name)], limit=1).id
        state_id = False
        if country_id and state_name:
            state_id = self.env['res.country.state'].search(
                [('name', '=', state_name), ('country_id', '=', country_id)],
                limit=1
            ).id

        # Create new partner if not found
        partner = self.env['res.partner'].create({
            'name': name or 'Shopify Customer',
            'email': email,
            'phone': phone,
            'street': street,
            'street2': street2,
            'city': city,
            'zip': zip_code,
            'state_id': state_id,
            'country_id': country_id,
        })
        _logger.info(f"👤 Created new customer: {partner.name} ({email})")
        return partner.id

    def _prepare_order_line(self, line):
        """
        Prepares values for a sale order line. It searches for existing products
        by barcode first, then by SKU, and finally by constructed variant name.
        If no product is found by any method, it will return an empty dictionary.
        """
        # Get data from Shopify line item
        product_title_base = line.get('title', '')  # e.g., "HP"
        variant_title_options = line.get('variant_title', '')  # e.g., "Red" or "Black"
        shopify_barcode = line.get('barcode')
        shopify_sku = line.get('sku')
        variant_id = line.get('variant_id')

        # Construct the full variant display name for matching in Odoo
        display_name = product_title_base
        if variant_title_options:
            display_name = f"{product_title_base} ({variant_title_options})"

        # --- Debugging: Log the raw quantity value received from Shopify ---
        raw_quantity_from_shopify = line.get('quantity')
        _logger.info(
            f"ℹ️ Raw quantity for '{display_name}' (Shopify Line ID: {line.get('id')}): {raw_quantity_from_shopify}")

        # Fetch variant details from Shopify API if variant_id exists
        # This is crucial to get the most accurate barcode and title for the variant,
        # especially if line.get('barcode') or line.get('title') are not precise.
        if variant_id:
            shopify_instance = self._get_target_instances()[:1]
            if shopify_instance:
                try:
                    variant_url = f"https://{shopify_instance.shopify_domain}/admin/api/2023-04/variants/{variant_id}.json"
                    headers = {
                        'X-Shopify-Access-Token': shopify_instance.shopify_token,
                        'Content-Type': 'application/json',
                    }
                    variant_response = requests.get(variant_url, headers=headers)
                    variant_response.raise_for_status()
                    variant_data = variant_response.json().get('variant', {})

                    # Update barcode and SKU with more accurate data from variant API
                    if variant_data.get('barcode'):
                        shopify_barcode = variant_data.get('barcode')
                    if variant_data.get('sku'):
                        shopify_sku = variant_data.get('sku')

                    # Update display_name if variant API provides a more specific title
                    if variant_data.get('title'):
                        # This title from variant API is often the full variant name (e.g., "HP - Red")
                        # If the user wants "HP (Red)" format, we stick to the constructed display_name.
                        # If variant_data.get('title') is already "HP - Red", it can be used directly.
                        # For consistency with user's example "HP (Red)", we'll keep the constructed one.
                        pass

                except requests.exceptions.RequestException as e:
                    _logger.warning(f"⚠️ Could not fetch variant details from Shopify for variant_id {variant_id}: {e}")
                except Exception as e:
                    _logger.warning(
                        f"⚠️ An unexpected error occurred while fetching variant details for variant_id {variant_id}: {e}")

        product = self.env['product.product']  # Initialize product as empty recordset

        # --- Product Search Logic ---
        # 1. Attempt to find product by barcode (most reliable for variants)
        if shopify_barcode:
            product = self.env['product.product'].search([('barcode', '=', shopify_barcode)], limit=1)
            if product:
                _logger.info(f" Found product by barcode: '{product.name}' (Barcode: {shopify_barcode})")

        # 2. If not found by barcode, try by SKU (default_code)
        if not product and shopify_sku:
            product = self.env['product.product'].search([('default_code', '=', shopify_sku)], limit=1)
            if product:
                _logger.info(f" Found product by SKU: '{product.name}' (SKU: {shopify_sku})")

        # 3. If not found by barcode or SKU, try by constructed display_name (full variant name)
        if not product and display_name:
            product = self.env['product.product'].search([('name', '=', display_name)], limit=1)
            if product:
                _logger.info(f" Found product by display name: '{product.name}' (Display Name: {display_name})")

        if not product:
            # Log an error if the product is not found by any method.
            _logger.error(
                f" Product for '{display_name}' (Shopify Barcode: '{shopify_barcode}', SKU: '{shopify_sku}') not found in Odoo by barcode, SKU, or display name. Skipping this line item."
            )
            return {}

        # If product is found, prepare the dictionary for the order line.
        quantity = float(raw_quantity_from_shopify or 1)  # Use the raw quantity, default to 1 if None/empty
        price = float(line.get('price', 0.0))
        shopify_line_id = str(line.get('id'))

        return {
            'product_id': product.id,
            'product_uom_qty': quantity,
            'price_unit': price,
            'name': display_name,  # Use the constructed full variant name for the order line name
            'shopify_line_id': shopify_line_id,
        }

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
                _logger.info(f" Created discount product: {discount_product.name} (ID: {discount_product.id})")
            else:
                _logger.info(f" Found existing discount product: {discount_product.name} (ID: {discount_product.id})")
            
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
                _logger.info(f" Discount codes structure: {order_data.get('discount_codes')}")
            if order_data.get('total_discounts'):
                _logger.info(f" Total discounts: {order_data.get('total_discounts')}")
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
                _logger.info(f" Created shipping product: {shipping_product.name} (ID: {shipping_product.id})")
            else:
                _logger.info(f" Found existing shipping product: {shipping_product.name} (ID: {shipping_product.id})")
            
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

    def _prepare_all_shipping_lines(self, order_data):
        """Prepare all shipping charges as separate order lines"""
        shipping_lines = []
        
        _logger.info(f"🔍 DEBUG: Processing all shipping lines for order {order_data.get('id')}")
        _logger.info(f"🔍 DEBUG: Order data keys: {list(order_data.keys())}")
        
        if order_data.get('shipping_lines'):
            _logger.info(f"🔍 DEBUG: Found {len(order_data.get('shipping_lines'))} shipping lines: {order_data.get('shipping_lines')}")
            
            # Find or create a shipping product
            shipping_product = self.env['product.product'].search([
                ('name', 'ilike', 'shipping'),
                ('type', '=', 'service')
            ], limit=1)
            
            if not shipping_product:
                _logger.info(f"🆕 Creating new shipping product")
                shipping_product = self.env['product.product'].create({
                    'name': 'Shipping Charges',
                    'type': 'service',
                    'sale_ok': True,
                    'purchase_ok': False,
                    'list_price': 0.0,
                    'default_code': 'SHIPPING',
                    'barcode': 'SHIPPING001'
                })
                _logger.info(f" Created shipping product: {shipping_product.name} (ID: {shipping_product.id})")
            else:
                _logger.info(f" Found existing shipping product: {shipping_product.name} (ID: {shipping_product.id})")
            
            # Process each shipping line
            for i, shopify_shipping in enumerate(order_data.get('shipping_lines', [])):
                try:
                    shipping_price = float(shopify_shipping.get('price', 0.0))
                    shipping_title = shopify_shipping.get('title', 'Standard Shipping')
                    shipping_id = shopify_shipping.get('id', f"shipping_{order_data.get('id')}_{i}")
                    
                    _logger.info(f"🚚 Processing shipping line {i+1}: {shipping_title} - Price: {shipping_price}")
                    
                    if shipping_price > 0:
                        shipping_line_data = {
                            'product_id': shipping_product.id,
                            'product_uom_qty': 1,
                            'price_unit': shipping_price,
                            'name': f"Shipping: {shipping_title}",
                            'shopify_line_id': f"shipping_{shipping_id}",
                        }
                        shipping_lines.append(shipping_line_data)
                        _logger.info(f" Prepared shipping line: {shipping_title} - {shipping_price}")
                    else:
                        _logger.info(f"ℹ️ Skipping shipping line with zero price: {shipping_title}")
                        
                except Exception as e:
                    _logger.error(f" Error processing shipping line {i}: {str(e)}")
                    continue
        else:
            _logger.warning(f"⚠️ No shipping_lines found in order data")
        
        _logger.info(f"🚚 Prepared {len(shipping_lines)} shipping lines total")
        return shipping_lines

    def _update_shipping_charges_for_existing_order(self, order, order_data):
        """Update shipping charges for existing orders - handles multiple shipping lines"""
        try:
            _logger.info(f"🚚 Processing shipping charges for existing order {order.name}")
            _logger.info(f"🔍 DEBUG: Order data keys: {list(order_data.keys())}")
            
            # Get all shipping lines from Shopify
            shopify_shipping_lines = order_data.get('shipping_lines', [])
            _logger.info(f"📦 Found {len(shopify_shipping_lines)} shipping lines in Shopify data")
            _logger.info(f"🔍 DEBUG: Shipping lines data: {shopify_shipping_lines}")
            
            # Get existing shipping lines in Odoo - check both shopify_line_id and product name
            existing_shipping_lines = order.order_line.filtered(lambda l: 
                (l.shopify_line_id and 'shipping' in l.shopify_line_id.lower()) or
                (l.name and ('shipping' in l.name.lower() or '[shipping]' in l.name.lower()))
            )
            _logger.info(f"📦 Found {len(existing_shipping_lines)} existing shipping lines in Odoo")
            
            # Create a mapping of existing shipping lines by their shopify_line_id
            existing_shipping_map = {line.shopify_line_id: line for line in existing_shipping_lines}
            
            # Track which Shopify shipping lines we've processed
            processed_shopify_shipping = set()
            
            # Process each shipping line from Shopify
            for i, shopify_shipping in enumerate(shopify_shipping_lines):
                try:
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
                    
                    _logger.info(f"🚚 Processing shipping line: {shipping_title} - Price: {shipping_price} - ID: {shopify_line_id}")
                    
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
                                _logger.info(f" Updated shipping line: {old_price} → {shipping_price}")
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
                            _logger.info(f" Created new shipping line: {shipping_title} - {shipping_price}")
                    else:
                        # Remove shipping line if price is 0
                        if shopify_line_id in existing_shipping_map:
                            existing_line = existing_shipping_map[shopify_line_id]
                            existing_line.unlink()
                            _logger.info(f" Removed shipping line: {shipping_title}")
                            
                except Exception as e:
                    _logger.error(f" Error processing shipping line {i}: {str(e)}")
                    continue
            
            # Remove shipping lines that are no longer in Shopify
            for shopify_line_id, existing_line in existing_shipping_map.items():
                if shopify_line_id not in processed_shopify_shipping:
                    try:
                        existing_line.unlink()
                        _logger.info(f" Removed old shipping line: {existing_line.name}")
                    except Exception as e:
                        _logger.error(f" Error removing old shipping line {shopify_line_id}: {str(e)}")
                        continue
            
            # Recalculate order totals after shipping changes
            try:
                order._amount_all()
                _logger.info(f" Recalculated order totals after shipping changes")
            except Exception as e:
                _logger.error(f" Error recalculating order totals: {str(e)}")
                
        except Exception as e:
            _logger.error(f" Error in _update_shipping_charges_for_existing_order: {str(e)}")

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
            
            _logger.info(f" Creating tax line with EXACT amount from Shopify: {total_tax} (Rate: {tax_rate})")
            return tax_line_data
        else:
            _logger.warning(f"⚠️ No tax amount found in Shopify order data. Available fields: {list(order_data.keys())}")
            # Log the order data structure for debugging
            if order_data.get('tax_lines'):
                _logger.info(f" Tax lines structure: {order_data.get('tax_lines')}")
            if order_data.get('subtotal_price'):
                _logger.info(f" Subtotal: {order_data.get('subtotal_price')}")
            if order_data.get('total_price'):
                _logger.info(f" Total: {order_data.get('total_price')}")
        return None

    def force_update_shipping_charges(self, shopify_order_id):
        """Force update shipping charges for a specific order by fetching from Shopify"""
        try:
            instance = self._get_target_instances()[:1]
            if not instance:
                _logger.error("No Shopify instance found")
                return False
            
            # Find the order in Odoo
            order = self.env['sale.order'].search([('shopify_order_id', '=', str(shopify_order_id))], limit=1)
            if not order:
                _logger.error(f"Order with Shopify ID {shopify_order_id} not found in Odoo")
                return False
            
            # Fetch fresh data from Shopify
            shopify_domain = instance.shopify_domain
            access_token = instance.shopify_token
            
            url = f"https://{shopify_domain}/admin/api/2024-01/orders/{shopify_order_id}.json"
            headers = {
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": access_token,
            }
            
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                order_data = response.json().get('order', {})
                _logger.info(f"Fetched fresh order data from Shopify: {order_data.get('shipping_lines', [])}")
                
                # Update shipping charges
                self._update_shipping_charges_for_existing_order(order, order_data)
                _logger.info(f"Updated shipping charges for order {order.name}")
                return True
            else:
                _logger.error(f"Failed to fetch order from Shopify: {response.status_code}")
                return False
                
        except Exception as e:
            _logger.error(f"Error in force_update_shipping_charges: {str(e)}")
            return False

    def action_update_shipping_from_shopify(self):
        """Action method to manually update shipping charges from Shopify"""
        for order in self:
            if order.shopify_order_id:
                _logger.info(f"🔄 Manually updating shipping charges for order {order.name}")
                result = self.env['shopify.sale.paid.order'].force_update_shipping_charges(order.shopify_order_id)
                if result:
                    _logger.info(f"✅ Successfully updated shipping charges for order {order.name}")
                else:
                    _logger.error(f"❌ Failed to update shipping charges for order {order.name}")
            else:
                _logger.warning(f"⚠️ Order {order.name} has no Shopify order ID")

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
