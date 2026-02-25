

# -*- coding: utf-8 -*-
import json
import logging
from odoo import http
from odoo.http import request
from base64 import b64decode
from io import BytesIO


_logger = logging.getLogger(__name__)


def _resolve_request_instance(env, strict=True):
    shop_domain = (request.httprequest.headers.get("X-Shopify-Shop-Domain") or "").strip()
    return env["shopify.instance"].sudo()._resolve_instance(shop_domain=shop_domain, strict=strict)


class ShopifyRefundController(http.Controller):

    @http.route('/shopify/webhook/refund_create', type='json', auth='none', csrf=False,website=True)
    def shopify_refund_create_webhook(self, **post):
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            _logger.info("Received Shopify refund webhook: %s", data)

            shopify_order_id = str(data.get('order_id'))
            refund_id = data.get('id')
            refund_line_items = data.get('refund_line_items', [])
            transactions = data.get('transactions') or []
            # Calculate total refund amount from all refund transactions
            refund_amount = 0.0
            for tx in transactions:
                if tx.get('kind') == 'refund' and float(tx.get('amount', 0.0)) > 0:
                    refund_amount += float(tx.get('amount', 0.0))

            env = request.env(user=1)
            instance = _resolve_request_instance(env, strict=True)
            sale_domain = [('shopify_order_id', '=', shopify_order_id)]
            if instance:
                sale_domain.append(('shopify_instance_id', '=', instance.id))

            sale_order = env['sale.order'].sudo().search(sale_domain, limit=1)
            if not sale_order and not instance:
                candidates = env['sale.order'].sudo().search([('shopify_order_id', '=', shopify_order_id)])
                if len(candidates) == 1:
                    sale_order = candidates
                elif len(candidates) > 1:
                    _logger.error(
                        "Refund webhook ambiguous for order %s across %s instances. "
                        "Missing/invalid X-Shopify-Shop-Domain header.",
                        shopify_order_id, len(candidates),
                    )
                    return request.make_json_response({'status': 'error', 'message': 'Ambiguous store context'}, status=400)

            if sale_order:
                if refund_amount > 0:
                    sale_order.create_odoo_refund(refund_amount, refund_id, refund_line_items)
                    _logger.info("Created refund for Sale Order: %s (amount: %s)", sale_order.name, refund_amount)
                else:
                    _logger.info("Exchange detected (no refund amount). No refund invoice created for Sale Order: %s", sale_order.name)
                # Always try return sync on refund webhook. Shopify return events
                # typically surface through refunds payloads, not /shopify/return.
                try:
                    sale_order.action_sync_shopify_return()
                except Exception as sync_err:
                    _logger.warning(
                        "Return sync failed after refund webhook for order %s: %s",
                        sale_order.name, sync_err
                    )
            else:
                _logger.warning("No Sale Order found for Shopify Order ID: %s", shopify_order_id)

            return request.make_json_response({'status': 'success'})
        except Exception as e:
            _logger.error("Shopify refund webhook failed: %s", str(e))
            return request.make_json_response({'status': 'error', 'message': str(e)}, status=500)





class ShopifyReturnController(http.Controller):

    @http.route('/shopify/return', type='json', auth='public', csrf=False)
    def shopify_return(self, **kwargs):
        data = json.loads(request.httprequest.data)
        _logger.info("Received Shopify return webhook: %s", json.dumps(data, indent=2))

        shopify_order_id = str(data.get('id'))
        if not shopify_order_id:
            return {"error": "Missing Shopify order ID"}

        env = request.env(user=1)
        instance = _resolve_request_instance(env, strict=True)
        sale_domain = [('shopify_order_id', '=', shopify_order_id)]
        if instance:
            sale_domain.append(('shopify_instance_id', '=', instance.id))
        sale_order = env['sale.order'].sudo().search(sale_domain, limit=1)
        if not sale_order and not instance:
            candidates = env['sale.order'].sudo().search([('shopify_order_id', '=', shopify_order_id)])
            if len(candidates) == 1:
                sale_order = candidates
            elif len(candidates) > 1:
                _logger.error(
                    "Return webhook ambiguous for order %s across %s instances. "
                    "Missing/invalid X-Shopify-Shop-Domain header.",
                    shopify_order_id, len(candidates),
                )
                return {"error": "Ambiguous store context"}
        if not sale_order:
            _logger.warning("Sale order not found for Shopify Order ID %s", shopify_order_id)
            return {"error": "Sale order not found"}

        # Collect all returned line items from Shopify payload
        returned_line_items = []
        for ret in data.get('returns', []):
            returned_line_items += ret.get('return_line_items', [])

        if not returned_line_items:
            _logger.info("No returned line items found in payload for order %s", shopify_order_id)
            return {"error": "No returned items found"}

        # Get the completed delivery picking
        delivery_picking = sale_order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing' and p.state == 'done'
        )
        if not delivery_picking:
            _logger.error("No completed delivery picking found for Sale Order %s", sale_order.name)
            return {"error": "No completed delivery picking found for this order"}
        delivery_picking = delivery_picking[0]

        # **Check if a matching return picking already exists**
        already_returned = request.env['stock.picking'].sudo().search([
            ('origin', '=', delivery_picking.name),
            ('picking_type_code', '=', 'incoming'),
            ('state', '!=', 'cancel'),
        ], limit=1)
        if already_returned:
            _logger.info("Return picking already exists: %s, skipping duplicate", already_returned.name)
            return {"success": True, "message": "Return already created", "picking_id": already_returned.id}

        try:
            new_return_picking = sale_order.create_standard_return_picking(delivery_picking, returned_line_items)
            if not new_return_picking:
                return {"error": "No matching lines for return"}

            # Auto-validate the return picking (set qty_done, then validate)
            qty_map = {str(item.get('line_item_id')): item.get('quantity', 0) for item in returned_line_items}
            if hasattr(new_return_picking, 'move_line_ids') and new_return_picking.move_line_ids:
                for move_line in new_return_picking.move_line_ids:
                    shopify_line_id = (
                        move_line.move_id.sale_line_id.shopify_line_id
                        if move_line.move_id and move_line.move_id.sale_line_id
                        else None
                    )
                    if shopify_line_id and shopify_line_id in qty_map:
                        if hasattr(move_line, 'qty_done'):
                            move_line.qty_done = qty_map[shopify_line_id]
            new_return_picking.button_validate()
            _logger.info("Return picking %s created and validated for Shopify order %s", new_return_picking.name,
                         shopify_order_id)
            return {"success": True, "picking_id": new_return_picking.id}
        except Exception as e:
            _logger.error("Error processing Shopify return webhook: %s", str(e))
            return {"error": str(e)}



try:
    from PIL import Image
except ImportError:
    import Image  # For older Pillow versions

class ImageController(http.Controller):
    @http.route('/shopify/images/<int:id>/<name>', type='http', auth='public', methods=['GET'], csrf=False)
    def get_shopify_data(self, id, name):
        product = request.env['product.product'].sudo().search([('id', '=', id)], limit=1)
        if product and product.image_1920:
            image_bytes = b64decode(product.image_1920)
            # Detect the image format using Pillow
            try:
                img = Image.open(BytesIO(image_bytes))
                img_format = img.format
                # Map format to proper MIME
                format_map = {
                    "JPEG": "image/jpeg",
                    "JPG": "image/jpeg",
                    "PNG": "image/png",
                    "WEBP": "image/webp",
                }
                mime_type = format_map.get(img_format.upper(), "application/octet-stream")
            except Exception:
                mime_type = "application/octet-stream"
            return http.Response(
                image_bytes,
                status=200,
                content_type=mime_type
            )
        return http.Response("Not found", status=404)




class ShopifyWebhookController(http.Controller):

    @http.route('/shopify/webhook/create', type='json', website=True, auth='none', methods=['POST'], csrf=False)
    def shopify_order_create_webhook(self, **kwargs):
        _logger.warning('Shopify /create webhook endpoint hit!')
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            _logger.info(f"Shopify Webhook Received (CREATE): Order #{data.get('id')}")
            env = request.env(user=1)
            instance = _resolve_request_instance(env, strict=True)
            if not instance:
                _logger.error(
                    "CREATE webhook skipped: instance not found for domain '%s'",
                    (request.httprequest.headers.get('X-Shopify-Shop-Domain') or '').strip(),
                )
                return "OK"
            service = env['shopify.sale.paid.order'].sudo()
            service = service.with_context(shopify_instance_id=instance.id)
            service.import_single_shopify_order(data)
        except Exception as e:
            _logger.error(f"Webhook Error (CREATE): {str(e)}")
        return "OK"

    @http.route('/shopify/webhook/update', type='json', website=True, auth='none', methods=['POST'], csrf=False)
    def shopify_order_update_webhook(self, **kwargs):
        _logger.warning('Shopify /update webhook endpoint hit!')
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            _logger.info(f"Shopify Webhook Received (UPDATE): Order #{data.get('id')}")
            _logger.info(f"📋 Order data shipping_lines: {data.get('shipping_lines', [])}")
            _logger.info(f"📋 Number of shipping lines: {len(data.get('shipping_lines', []))}")
            
            env = request.env(user=1)
            instance = _resolve_request_instance(env, strict=True)
            if not instance:
                _logger.error(
                    "UPDATE webhook skipped: instance not found for domain '%s'",
                    (request.httprequest.headers.get('X-Shopify-Shop-Domain') or '').strip(),
                )
                return "OK"
            service = env['shopify.sale.paid.order'].sudo()
            service = service.with_context(shopify_instance_id=instance.id)
            service.import_single_shopify_order(data)
        except Exception as e:
            _logger.error(f"Webhook Error (UPDATE): {str(e)}")
        return "OK"

    # @http.route('/shopify/webhook/order', type='json', website=True, auth='none', methods=['POST'], csrf=False)
    # def shopify_order_webhook(self, **kwargs):
    #     _logger.warning('Shopify /order webhook endpoint hit!')
    #     try:
    #         data = json.loads(request.httprequest.data.decode('utf-8'))
    #         _logger.info(f"Shopify Webhook Received: Order #{data.get('id')}")
    #         env = request.env(user=1)
    #         env['shopify.sale.paid.order'].sudo().import_single_shopify_order(data)
    #     except Exception as e:
    #         _logger.error(f"Webhook Error: {str(e)}")
    #     return "OK"




class ShopifyFulfillmentWebhook(http.Controller):

    @http.route('/shopify/webhook/fulfillment', type='json', website=True, auth='none', methods=['POST'], csrf=False)
    def shopify_fulfillment_created(self, **kwargs):
        payload = json.loads(request.httprequest.data.decode('utf-8'))

        _logger.info(f"📦 Fulfillment webhook received: {json.dumps(payload, indent=2)}")

        shopify_order_id = payload.get('order_id')
        tracking_number = payload.get('tracking_number', '')
        tracking_company = payload.get('tracking_company', '')

        if not shopify_order_id:
            _logger.warning("⚠️ Fulfillment webhook missing order_id.")
            return "Missing order_id"

        env = request.env(user=1)
        instance = _resolve_request_instance(env, strict=True)
        origin = f"Shopify-{shopify_order_id}"
        order_domain = [('origin', '=', origin)]
        if instance:
            order_domain.append(('shopify_instance_id', '=', instance.id))
        order = env['sale.order'].sudo().search(order_domain, limit=1)
        if not order:
            _logger.warning(f"⚠️ Order with origin '{origin}' not found in Odoo.")
            return "Order not found"

        vals = {}
        if tracking_number and order.tracking_number != tracking_number:
            vals['tracking_number'] = tracking_number
        if tracking_company and (not order.delivery_partner_id or order.delivery_partner_id.name != tracking_company):
            # Find or create delivery partner in res.partner
            delivery_partner = request.env['res.partner'].sudo().search([('name', '=', tracking_company)], limit=1)
            if not delivery_partner:
                delivery_partner = request.env['res.partner'].sudo().create({
                    'name': tracking_company,
                    'is_company': True,
                    'customer_rank': 0,
                    'supplier_rank': 0,
                })
            vals['delivery_partner_id'] = delivery_partner.id

        if vals:
            order.write(vals)
            _logger.info(f"✅ Order {order.name} updated with tracking info.")

        return "Success"


class ShopifyOrderEditWebhook(http.Controller):
    """
    Comprehensive webhook to handle all order edits from Shopify:
    - Product changes (add/remove/modify)
    - Size/Color/Variant changes
    - Price changes
    - Shipping charges changes
    - Discount changes
    - Quantity changes
    - Delivery partner changes
    """

    @http.route('/shopify/webhook/order_edit', type='json', website=True, auth='none', methods=['POST'], csrf=False)
    def shopify_order_edit_webhook(self, **kwargs):
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            _logger.info(f"🔄 Shopify Order Edit Webhook Received: {json.dumps(data, indent=2)}")
            
            env = request.env(user=1)
            result = env['shopify.order.edit'].sudo().process_order_edit(data)
            
            if result.get('success'):
                _logger.info(f"✅ Order edit processed successfully: {result.get('message')}")
                return request.make_json_response({'status': 'success', 'message': result.get('message')})
            else:
                _logger.error(f"❌ Order edit failed: {result.get('message')}")
                return request.make_json_response({'status': 'error', 'message': result.get('message')}, status=400)
                
        except Exception as e:
            _logger.error(f"❌ Shopify order edit webhook failed: {str(e)}")
            return request.make_json_response({'status': 'error', 'message': str(e)}, status=500)

    @http.route('/shopify/webhook/debug', type='http', website=True, auth='public', methods=['GET'], csrf=False)
    def debug_webhook(self, **kwargs):
        """
        Debug endpoint to check webhook status and recent orders
        """
        try:
            # Get recent orders
            recent_orders = request.env['sale.order'].sudo().search([], limit=10, order='create_date desc')
            
            orders_html = ""
            for order in recent_orders:
                shipping_lines = order.order_line.filtered(lambda l: 'shipping' in l.shopify_line_id.lower())
                orders_html += f"""
                <tr>
                    <td>{order.name}</td>
                    <td>{order.origin}</td>
                    <td>{len(order.order_line)}</td>
                    <td>{len(shipping_lines)}</td>
                    <td>{order.state}</td>
                </tr>
                """
            
            return f"""
            <html>
            <head><title>Shopify Webhook Debug</title></head>
            <body>
                <h1>Shopify Webhook Debug</h1>
                <h2>Recent Orders:</h2>
                <table border="1" style="border-collapse: collapse;">
                    <tr>
                        <th>Order Name</th>
                        <th>Origin</th>
                        <th>Total Lines</th>
                        <th>Shipping Lines</th>
                        <th>State</th>
                    </tr>
                    {orders_html}
                </table>
                
                <h2>Webhook URLs:</h2>
                <ul>
                    <li><strong>Order Edit:</strong> <code>/shopify/webhook/order_edit</code></li>
                    <li><strong>Order Create:</strong> <code>/shopify/webhook/order_import</code></li>
                </ul>
                
                <h2>Instructions:</h2>
                <ol>
                    <li>Make sure Shopify webhook is configured for "Order updated"</li>
                    <li>Check Odoo logs for webhook calls</li>
                    <li>Verify order origin matches Shopify ID</li>
                </ol>
            </body>
            </html>
            """
        except Exception as e:
            return f"Error: {str(e)}"

    @http.route('/shopify/webhook/manual_sync', type='http', website=True, auth='public', methods=['GET', 'POST'], csrf=False)
    def manual_sync(self, **kwargs):
        """
        Manual sync endpoint to trigger order edit for specific order
        """
        try:
            if request.httprequest.method == 'POST':
                order_id = request.params.get('order_id', '')
                if not order_id:
                    return "Please provide an order ID"
                
                # Find the order
                order = request.env['sale.order'].sudo().search([('id', '=', int(order_id))], limit=1)
                if not order:
                    return f"Order with ID {order_id} not found"
                
                # Create test data with shipping
                test_data = {
                    "id": order.origin.replace('Shopify-', '') if order.origin.startswith('Shopify-') else "12345",
                    "name": order.name,
                    "financial_status": "paid",
                    "created_at": order.date_order.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    "line_items": [],
                    "shipping_lines": [
                        {
                            "id": "ship001",
                            "title": "ship",
                            "price": "100.00"
                        },
                        {
                            "id": "ship002",
                            "title": "new fee", 
                            "price": "100.00"
                        }
                    ],
                    "discount_codes": [],
                    "total_discounts": "0.00",
                    "fulfillments": []
                }
                
                # Add existing line items
                for line in order.order_line:
                    if 'shipping' not in line.shopify_line_id.lower() and 'discount' not in line.shopify_line_id.lower():
                        test_data["line_items"].append({
                            "id": line.shopify_line_id or f"line_{line.id}",
                            "title": line.product_id.name,
                            "variant_title": "",
                            "quantity": int(line.product_uom_qty),
                            "price": str(line.price_unit),
                            "barcode": line.product_id.barcode or "",
                            "sku": line.product_id.default_code or ""
                        })
                
                # Process the test data
                env = request.env(user=1)
                result = env['shopify.order.edit'].sudo().process_order_edit(test_data)
                
                return f"""
                <html>
                <head><title>Manual Sync Results</title></head>
                <body>
                    <h1>Manual Sync Results</h1>
                    <h2>Order: {order.name}</h2>
                    <h2>Test Data:</h2>
                    <pre>{json.dumps(test_data, indent=2)}</pre>
                    <h2>Result:</h2>
                    <pre>{json.dumps(result, indent=2)}</pre>
                    <p><a href="/shopify/webhook/manual_sync">Try Another Order</a></p>
                </body>
                </html>
                """
            else:
                # Show form
                orders = request.env['sale.order'].sudo().search([], limit=20)
                order_options = ""
                for order in orders:
                    order_options += f'<option value="{order.id}">{order.name} - {order.origin}</option>'
                
                return f"""
                <html>
                <head><title>Manual Sync</title></head>
                <body>
                    <h1>Manual Sync Test</h1>
                    <p>Select an order to test shipping sync:</p>
                    <form method="POST">
                        <select name="order_id" required>
                            <option value="">Select an order...</option>
                            {order_options}
                        </select>
                        <button type="submit">Test Shipping Sync</button>
                    </form>
                    <p>This will add 2 shipping lines: "ship" (₹100) and "new fee" (₹100)</p>
                </body>
                </html>
                """
                
        except Exception as e:
            return f"Error: {str(e)}"


class ShopifyCustomerWebhookController(http.Controller):

    @http.route('/shopify/webhook/customer_create', type='http', auth='none', methods=['POST'], csrf=False)
    def shopify_customer_create_webhook(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.data.decode('utf-8') or '{}')
            env = request.env(user=1)
            importer = env['shopify.customer.importer'].sudo()
            instance = _resolve_request_instance(env, strict=True)
            if not instance:
                raise ValueError("No Shopify instance configured for customer webhook.")
            partner = importer._create_or_update_partner(payload, instance)
            env['shopify.sync.log'].sudo().log_event(
                operation="Webhook Customer Create",
                message=f"Customer imported from webhook: {partner.name}",
                status="success",
                level="info",
                channel="webhook",
                model_name="res.partner",
                res_id=partner.id,
                reference=partner.shopify_customer_id or partner.email,
                instance_id=instance.id if instance else False,
                payload=payload,
            )
            return http.Response("OK", status=200)
        except Exception as e:
            _logger.error("Shopify customer create webhook failed: %s", str(e), exc_info=True)
            try:
                request.env['shopify.sync.log'].sudo().log_event(
                    operation="Webhook Customer Create",
                    message=f"Customer webhook failed: {e}",
                    status="failed",
                    level="error",
                    channel="webhook",
                    payload=request.httprequest.data.decode('utf-8', errors='ignore'),
                )
            except Exception:
                pass
            return http.Response("ERROR", status=500)
