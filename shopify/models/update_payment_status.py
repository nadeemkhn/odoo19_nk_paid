import requests
from odoo import models, fields, _

from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _log_payment_sync(self, **vals):
        try:
            self.env['shopify.sync.log'].log_event(**vals)
        except Exception:
            _logger.debug("Failed to persist payment sync log", exc_info=True)

    def cron_push_payments_to_shopify(self):
        """
        Push payments to Shopify for posted customer invoices only.
        This method explicitly excludes bills (in_invoice) and refunds.
        """
        # Only process customer invoices (out_invoice), exclude bills (in_invoice) and refunds
        invoices = self.search([
            ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice'),  # Only customer invoices, not bills
            ('shopify_order_id', '!=', False)
        ])

        _logger.info(f"🔄 Found {len(invoices)} posted customer invoices to process for Shopify payment sync")

        # Log excluded documents for transparency
        excluded_bills = self.search([
            ('state', '=', 'posted'),
            ('move_type', 'in', ['in_invoice', 'in_refund']),  # Vendor bills and refunds
            ('shopify_order_id', '!=', False)
        ])
        if excluded_bills:
            _logger.info(f"ℹ️ Excluding {len(excluded_bills)} bills/refunds from Shopify payment sync (as intended)")
            for bill in excluded_bills:
                _logger.info(f"   - Excluded {bill.move_type}: {bill.name} (ID: {bill.id})")

        for invoice in invoices:
            _logger.info(f"📤 Processing invoice {invoice.name} for Shopify payment sync")
            invoice.push_payment_to_shopify()

    def push_payment_to_shopify(self):
        """
        Push payment status to Shopify for customer invoices only.
        Only FULLY PAID invoices are allowed to sync.
        """

        self.ensure_one()

        # -------------------------------------------------
        # VALIDATION 1 — Customer invoice only
        # -------------------------------------------------
        if self.move_type != 'out_invoice':
            _logger.warning(
                f"⚠️ Skipping {self.move_type} {self.name} - "
                f"Only customer invoices (out_invoice) can be synced to Shopify."
            )
            self._log_payment_sync(
                operation="Push Payment",
                message=f"Skipped {self.move_type} {self.name}. Only out_invoice is supported.",
                status="warning",
                level="warning",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.name,
            )
            raise UserError(
                _("Only customer invoices can be synced to Shopify. "
                  "Bills and refunds are not supported.")
            )

        # -------------------------------------------------
        # VALIDATION 2 — Shopify Order ID must exist
        # -------------------------------------------------
        if not self.shopify_order_id:
            _logger.warning(
                f"⚠️ Invoice {self.name} has no Shopify Order ID."
            )
            self._log_payment_sync(
                operation="Push Payment",
                message=f"Invoice {self.name} has no Shopify Order ID.",
                status="warning",
                level="warning",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.name,
            )
            raise UserError(
                _("This invoice is not linked to any Shopify order.")
            )

        # -------------------------------------------------
        # ✅ VALIDATION 3 — FULL PAYMENT REQUIRED (IMPORTANT)
        # -------------------------------------------------
        if self.amount_residual != 0:
            _logger.info(
                f"⛔ Invoice {self.name} is NOT fully paid "
                f"(Residual: {self.amount_residual}). Sync blocked."
            )
            self._log_payment_sync(
                operation="Push Payment",
                message=f"Invoice {self.name} is not fully paid. Residual: {self.amount_residual}",
                status="warning",
                level="warning",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.name,
            )
            raise UserError(
                _("This invoice is not fully paid.\n\n"
                  "Please register the full payment before syncing "
                  "the payment status to Shopify.")
            )


        # -------------------------------------------------
        # Shopify Instance
        # -------------------------------------------------
        instance = self.invoice_line_ids.mapped("sale_line_ids.order_id.shopify_instance_id")[:1]
        if not instance:
            sale_order = self.env["sale.order"].search([
                ("shopify_order_id", "=", str(self.shopify_order_id)),
            ], limit=1)
            instance = sale_order.shopify_instance_id
        if not instance:
            instance = self.env['shopify.instance']._get_default_instance()
        if not instance:
            _logger.error("❌ No Shopify instance found in the system.")
            self._log_payment_sync(
                operation="Push Payment",
                message="No Shopify instance found while pushing payment.",
                status="failed",
                level="error",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.name,
            )
            raise UserError(_("No Shopify instance found."))

        domain = instance.shopify_domain
        token = instance.shopify_token

        if not domain or not token:
            self._log_payment_sync(
                operation="Push Payment",
                message="Shopify domain/token missing on instance.",
                status="failed",
                level="error",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.name,
                instance_id=instance.id,
            )
            raise UserError(
                _("Shopify domain or token is missing in the linked instance.")
            )

        shop_url = f"https://{domain}/admin/api/2025-04/graphql.json"

        query = """
        mutation orderMarkAsPaid($input: OrderMarkAsPaidInput!) {
          orderMarkAsPaid(input: $input) {
            order {
              id
              displayFinancialStatus
            }
            userErrors {
              field
              message
            }
          }
        }
        """

        variables = {
            "input": {
                "id": f"gid://shopify/Order/{self.shopify_order_id}"
            }
        }

        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        }

        try:
            _logger.info(
                f"🔹 Marking Shopify Order {self.shopify_order_id} as PAID"
            )

            response = requests.post(
                shop_url,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=50
            )
            response.raise_for_status()
            data = response.json()

            if data.get("errors"):
                msgs = [e["message"] for e in data["errors"]]
                self._log_payment_sync(
                    operation="Push Payment",
                    message=f"GraphQL error for invoice {self.name}: {' | '.join(msgs)}",
                    status="failed",
                    level="error",
                    channel="export",
                    model_name="account.move",
                    res_id=self.id,
                    reference=self.shopify_order_id,
                    instance_id=instance.id,
                )
                raise UserError(
                    _("Shopify GraphQL Error:\n%s") % "\n".join(msgs)
                )

            payload = data["data"]["orderMarkAsPaid"]
            if payload["userErrors"]:
                msgs = [e["message"] for e in payload["userErrors"]]
                self._log_payment_sync(
                    operation="Push Payment",
                    message=f"Shopify userErrors for invoice {self.name}: {' | '.join(msgs)}",
                    status="failed",
                    level="error",
                    channel="export",
                    model_name="account.move",
                    res_id=self.id,
                    reference=self.shopify_order_id,
                    instance_id=instance.id,
                )
                raise UserError(
                    _("Shopify Error:\n%s") % "\n".join(msgs)
                )

            status = payload["order"]["displayFinancialStatus"]
            _logger.info(
                f"✅ Shopify Order {self.shopify_order_id} marked as PAID "
                f"(Status: {status})"
            )
            self._log_payment_sync(
                operation="Push Payment",
                message=f"Marked Shopify order {self.shopify_order_id} as paid ({status}).",
                status="success",
                level="info",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.shopify_order_id,
                instance_id=instance.id,
            )
            return True

        except requests.RequestException as e:
            self._log_payment_sync(
                operation="Push Payment",
                message=f"Request error while pushing payment for invoice {self.name}: {e}",
                status="failed",
                level="error",
                channel="export",
                model_name="account.move",
                res_id=self.id,
                reference=self.shopify_order_id,
                instance_id=instance.id,
            )
            raise UserError(
                _("Failed to connect to Shopify: %s") % str(e)
            )
