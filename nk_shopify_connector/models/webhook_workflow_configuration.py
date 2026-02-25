from odoo import api, fields, models


class ShopifyWebhookWorkflowConfiguration(models.Model):
    _name = "shopify.webhook.workflow.configuration"
    _description = "Shopify Webhook Workflow Configuration"
    _order = "sequence, id"

    _workflow_unique_constraint = models.Constraint(
        "unique(instance_id, order_import_status, payment_status, delivery_status)",
        "A webhook workflow rule already exists for this status combination on this instance.",
    )

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(
        string="Rule Name",
        compute="_compute_name",
        store=True,
    )
    instance_id = fields.Many2one(
        "shopify.instance",
        required=True,
        ondelete="cascade",
        string="Shopify Instance",
    )
    order_import_status = fields.Selection([
        ("any", "Any"),
        ("open", "Open"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ], string="Order Import Status", default="any", required=True)
    payment_status = fields.Selection([
        ("any", "Any"),
        ("pending", "Pending"),
        ("authorized", "Authorized"),
        ("paid", "Paid"),
        ("partially_paid", "Partially Paid"),
        ("unpaid", "Unpaid"),
        ("partially_refunded", "Partially Refunded"),
        ("refunded", "Refunded"),
        ("voided", "Voided"),
    ], string="Payment Status", default="any", required=True)
    delivery_status = fields.Selection([
        ("any", "Any"),
        ("fulfilled", "Fulfilled"),
        ("partial", "Partial"),
        ("unfulfilled", "Unfulfilled"),
        ("restocked", "Restocked"),
    ], string="Delivery Status", default="any", required=True)

    auto_confirm_order = fields.Boolean(
        string="Auto Confirm Order",
        default=False,
        help="If enabled, matching webhook orders are automatically confirmed.",
    )
    create_invoice = fields.Boolean(
        string="Create Invoice",
        default=False,
        help="If enabled, matching webhook orders create customer invoices.",
    )
    register_payment = fields.Boolean(
        string="Register Payment",
        default=False,
        help="If enabled, posted invoices are auto-paid when Shopify financial status is paid.",
    )
    validate_delivery = fields.Boolean(
        string="Validate Delivery",
        default=False,
        help="If enabled, delivery orders are auto-validated when Shopify fulfillment is fulfilled.",
    )

    @api.depends("order_import_status", "payment_status", "delivery_status")
    def _compute_name(self):
        label_map = dict(self._fields["order_import_status"].selection)
        payment_map = dict(self._fields["payment_status"].selection)
        delivery_map = dict(self._fields["delivery_status"].selection)
        for rec in self:
            rec.name = "%s | %s | %s" % (
                label_map.get(rec.order_import_status, "Any"),
                payment_map.get(rec.payment_status, "Any"),
                delivery_map.get(rec.delivery_status, "Any"),
            )

    def matches_order(self, order_data):
        self.ensure_one()
        return self.instance_id._order_matches_filters(
            order_data,
            order_filter=self.order_import_status,
            payment_filter=self.payment_status,
            delivery_filter=self.delivery_status,
        )
