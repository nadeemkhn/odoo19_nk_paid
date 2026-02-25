import requests
from urllib.parse import urlparse
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
import logging
from datetime import timedelta

_logger = logging.getLogger(__name__)


class ShopifyInstance(models.Model):
    _name = 'shopify.instance'
    _description = "Shopify Instance"

    name = fields.Char("Instance Name")
    shopify_domain = fields.Char(string='Domain')
    shopify_token = fields.Char(string='Token',required=True)
    api_key = fields.Char(string='API Key')
    api_secret_key = fields.Char(string='API Secret Key')
    odoo_domain = fields.Char(string='Odoo Domain')
    shopify_location_id = fields.Char(string="Shopify Location ID")
    webhook_secret = fields.Char(string="Webhook Secret", help="Secret key used to verify Shopify webhooks. Generate it in Shopify Admin > Settings > Notifications > Webhooks.", groups="base.group_system")
    stock_location_id = fields.Many2one('stock.location', string="Odoo Stock Location")  # ✅
    shopify_order_count = fields.Integer(string="Shopify Orders", compute="_compute_connector_counts")
    shopify_product_count = fields.Integer(string="Shopify Products", compute="_compute_connector_counts")
    shopify_customer_count = fields.Integer(string="Shopify Customers", compute="_compute_connector_counts")
    sync_log_count = fields.Integer(string="Sync Logs", compute="_compute_connector_counts")
    order_import_status_filter = fields.Selection([
        ("any", "Any"),
        ("open", "Open"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ], string="Order Import Status", default="any")
    payment_status_filter = fields.Selection([
        ("any", "Any"),
        ("pending", "Pending"),
        ("authorized", "Authorized"),
        ("paid", "Paid"),
        ("partially_paid", "Partially Paid"),
        ("unpaid", "Unpaid"),
        ("partially_refunded", "Partially Refunded"),
        ("refunded", "Refunded"),
        ("voided", "Voided"),
    ], string="Payment Status", default="any")
    delivery_status_filter = fields.Selection([
        ("any", "Any"),
        ("fulfilled", "Fulfilled"),
        ("partial", "Partial"),
        ("unfulfilled", "Unfulfilled"),
        ("restocked", "Restocked"),
    ], string="Delivery Status", default="any")
    auto_confirm_imported_orders = fields.Boolean(
        string="Auto Confirm Imported Orders",
        default=False,
        help="If enabled, imported Shopify orders are automatically confirmed into Sale Orders. "
             "If disabled, they stay as Quotations."
    )
    auto_confirm_and_create_invoice = fields.Boolean(
        string="Auto Confirm + Create Invoice",
        default=False,
        help="If enabled, imported Shopify orders are auto-confirmed and customer invoices are auto-created. "
             "For paid Shopify orders, invoice posting/payment follows the payment workflow."
    )
    webhook_workflow_configuration_ids = fields.One2many(
        "shopify.webhook.workflow.configuration",
        "instance_id",
        string="Webhook Workflow Rules",
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'Confirmed')
    ], string="Status", default='draft')

    @api.model
    def _get_target_instances(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            return self.browse(instance_id).exists()
        instances = self.search([("state", "=", "confirm")])
        if not instances:
            instances = self.search([])
        return instances

    @api.model
    def _get_default_instance(self):
        return self._get_target_instances()[:1]

    @api.model
    def _normalize_shop_domain(self, shop_domain):
        domain = (shop_domain or "").strip().lower()
        if not domain:
            return ""
        domain = domain.replace("https://", "").replace("http://", "")
        return domain.split("/")[0]

    @api.model
    def _find_instance_by_domain(self, shop_domain):
        domain = self._normalize_shop_domain(shop_domain)
        if not domain:
            return self.browse()
        instance = self.search([("shopify_domain", "=", domain)], limit=1)
        if instance:
            return instance
        return self.search([("shopify_domain", "ilike", domain)], limit=1)

    @api.model
    def _resolve_instance(self, shop_domain=None, order_url=None, strict=False):
        instance = self._find_instance_by_domain(shop_domain)
        if instance:
            return instance

        if order_url:
            parsed_host = ""
            try:
                parsed_host = urlparse(order_url).netloc or order_url
            except Exception:
                parsed_host = order_url
            instance = self._find_instance_by_domain(parsed_host)
            if instance:
                return instance

        context_instance_id = self.env.context.get("shopify_instance_id")
        if context_instance_id:
            instance = self.browse(context_instance_id).exists()
            if instance:
                return instance

        if strict:
            return self.browse()
        return self._get_default_instance()

    @api.depends("name")
    def _compute_connector_counts(self):
        sale_order = self.env["sale.order"]
        product_template = self.env["product.template"]
        partner = self.env["res.partner"]
        sync_log = self.env["shopify.sync.log"]
        for rec in self:
            rec.shopify_order_count = sale_order.search_count([
                ("shopify_order_id", "!=", False),
                ("shopify_instance_id", "=", rec.id),
            ])
            rec.shopify_product_count = product_template.search_count([
                ("shopify_product_id", "!=", False),
                ("shopify_instance_id", "=", rec.id),
            ])
            rec.shopify_customer_count = partner.search_count([
                ("shopify_instance_id", "=", rec.id),
                ("shopify_customer_id", "!=", False),
            ])
            rec.sync_log_count = sync_log.search_count([("instance_id", "=", rec.id)])

    def action_confirm(self):
        for rec in self:
            rec.state = 'confirm'

    def action_set_draft(self):
        for rec in self:
            rec.state = 'draft'

    def get_order_import_query_params(self, updated_since=None):
        self.ensure_one()
        params = {"status": self.order_import_status_filter or "any"}
        if updated_since:
            params["updated_at_min"] = updated_since
        if self.payment_status_filter and self.payment_status_filter != "any":
            params["financial_status"] = self.payment_status_filter
        if self.delivery_status_filter and self.delivery_status_filter != "any":
            params["fulfillment_status"] = self.delivery_status_filter
        return params

    def _order_matches_filters(self, order, order_filter="any", payment_filter="any", delivery_filter="any"):
        self.ensure_one()

        if order_filter != "any":
            closed_at = bool(order.get("closed_at"))
            cancelled_at = bool(order.get("cancelled_at"))
            if order_filter == "open" and (closed_at or cancelled_at):
                return False
            if order_filter == "closed" and not closed_at:
                return False
            if order_filter == "cancelled" and not cancelled_at:
                return False

        payment_status = (order.get("financial_status") or "pending").lower()
        if payment_filter != "any" and payment_status != payment_filter:
            return False

        fulfillment_status = (order.get("fulfillment_status") or "unfulfilled").lower()
        if delivery_filter != "any" and fulfillment_status != delivery_filter:
            return False

        return True

    def _get_matching_webhook_workflow_rule(self, order):
        self.ensure_one()
        rules = self.webhook_workflow_configuration_ids.filtered("active").sorted(
            key=lambda r: (r.sequence, r.id)
        )
        for rule in rules:
            if rule.matches_order(order):
                return rule
        return self.env["shopify.webhook.workflow.configuration"]

    def is_order_allowed_for_import(self, order):
        self.ensure_one()
        active_rules = self.webhook_workflow_configuration_ids.filtered("active")
        if active_rules:
            return bool(self._get_matching_webhook_workflow_rule(order))
        return self._order_matches_filters(
            order,
            order_filter=self.order_import_status_filter or "any",
            payment_filter=self.payment_status_filter or "any",
            delivery_filter=self.delivery_status_filter or "any",
        )

    def check_shopify_credentials(self):
        for record in self:
            if not record.shopify_domain or not record.shopify_token:
                raise ValidationError("Domain or Token is missing.")

            url = f"https://{record.shopify_domain}/admin/api/2023-01/products.json"
            headers = {
                "X-Shopify-Access-Token": record.shopify_token
            }

            response = requests.get(url, headers=headers)

            if response.status_code == 401:
                raise ValidationError("Token is invalid or expired.")
            elif response.status_code != 200:
                raise ValidationError(f"Unexpected error: {response.status_code} - {response.text}")

    def action_view_shopify_orders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Shopify Orders",
            "res_model": "sale.order",
            "views": [(False, "list"), (False, "form")],
            "domain": [
                ("shopify_order_id", "!=", False),
                ("shopify_instance_id", "=", self.id),
            ],
            "context": {"shopify_instance_id": self.id},
        }

    def action_view_shopify_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Shopify Products",
            "res_model": "product.template",
            "views": [(False, "list"), (False, "form")],
            "domain": [
                ("shopify_product_id", "!=", False),
                ("shopify_instance_id", "=", self.id),
            ],
            "context": {"shopify_instance_id": self.id},
        }

    def action_view_shopify_customers(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Shopify Customers",
            "res_model": "res.partner",
            "views": [(False, "list"), (False, "form")],
            "domain": [
                ("shopify_instance_id", "=", self.id),
                ("shopify_customer_id", "!=", False),
            ],
            "context": {"shopify_instance_id": self.id},
        }

    def action_view_shopify_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Shopify Sync Logs",
            "res_model": "shopify.sync.log",
            "views": [(False, "list"), (False, "form")],
            "domain": [("instance_id", "=", self.id)],
            "context": {"shopify_instance_id": self.id},
        }

    def action_open_dashboard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "name": f"{self.name} Dashboard",
            "tag": "shopify_multi_store_dashboard",
            "context": {"shopify_instance_id": self.id},
        }

    def action_manual_export_products(self):
        self.ensure_one()
        return self.env['shopify.product.export'].with_context(
            shopify_instance_id=self.id
        ).push_products_to_shopify()

    @api.model
    def get_multi_store_dashboard_data(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            instances = self.browse(instance_id).exists()
        else:
            instances = self.search([], order="id")
        sale_order = self.env["sale.order"].sudo()
        product_template = self.env["product.template"].sudo()
        partner = self.env["res.partner"].sudo()
        sync_log = self.env["shopify.sync.log"].sudo()
        since_24h = fields.Datetime.now() - timedelta(hours=24)

        data = []
        for instance in instances:
            order_domain = [
                ("shopify_order_id", "!=", False),
                ("shopify_instance_id", "=", instance.id),
            ]
            sales_amount = sum(sale_order.search(order_domain).mapped("amount_total"))

            last_log = sync_log.search([("instance_id", "=", instance.id)], limit=1)
            failed_24h = sync_log.search_count([
                ("instance_id", "=", instance.id),
                ("create_date", ">=", since_24h),
                ("status", "in", ["failed", "warning"]),
            ])

            data.append({
                "id": instance.id,
                "name": instance.name or f"Store {instance.id}",
                "domain": instance.shopify_domain or "",
                "state": instance.state,
                "orders_count": sale_order.search_count(order_domain),
                "sales_amount": sales_amount,
                "products_count": product_template.search_count([
                    ("shopify_product_id", "!=", False),
                    ("shopify_instance_id", "=", instance.id),
                ]),
                "customers_count": partner.search_count([
                    ("shopify_instance_id", "=", instance.id),
                    ("shopify_customer_id", "!=", False),
                ]),
                "failed_24h": failed_24h,
                "last_sync": last_log.create_date if last_log else False,
            })
        return data
