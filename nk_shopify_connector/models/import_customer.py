import logging

import requests
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = "res.partner"

    shopify_customer_id = fields.Char("Shopify Customer ID", copy=False, index=True)
    shopify_instance_id = fields.Many2one("shopify.instance", string="Shopify Instance", index=True, copy=False)


class ShopifyCustomerImporter(models.TransientModel):
    _name = "shopify.customer.importer"
    _description = "Import Customers from Shopify"

    def _log_sync(self, **vals):
        try:
            self.env["shopify.sync.log"].log_event(**vals)
        except Exception:
            _logger.debug("Failed to persist customer sync log", exc_info=True)

    def cron_import_customers_from_shopify(self):
        return self.import_customers()

    def _get_target_instances(self):
        instance_id = self.env.context.get("shopify_instance_id")
        if instance_id:
            instance = self.env["shopify.instance"].browse(instance_id).exists()
            return instance
        instances = self.env["shopify.instance"].search([("state", "=", "confirm")])
        if not instances:
            instances = self.env["shopify.instance"].search([])
        return instances

    def import_customers(self):
        instances = self._get_target_instances()
        if not instances:
            raise UserError("No Shopify instance configured.")
        total_imported = 0
        for instance in instances:
            total_imported += self._import_customers_for_instance(instance)
        return total_imported

    def _import_customers_for_instance(self, instance):
        domain = instance.shopify_domain
        token = instance.shopify_token
        limit = 250
        param_key = f"shopify.customer_import_since_id.{self.env.cr.dbname}.{instance.id}"
        since_id = int(self.env["ir.config_parameter"].sudo().get_param(param_key, "0") or 0)
        imported_count = 0
        max_seen_id = since_id

        self._log_sync(
            operation="Import Customers",
            message=f"Scheduled Shopify customer import started from since_id={since_id}.",
            status="info",
            level="info",
            channel="import",
            instance_id=instance.id,
        )

        headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        }

        while True:
            url = f"https://{domain}/admin/api/2023-10/customers.json?limit={limit}&since_id={since_id}"
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code != 200:
                self._log_sync(
                    operation="Import Customers",
                    message=f"Shopify API error during customer import: {response.text}",
                    status="failed",
                    level="error",
                    channel="import",
                    instance_id=instance.id,
                    response=response.text,
                )
                raise UserError(f"Shopify API error: {response.text}")

            customers = response.json().get("customers", [])
            if not customers:
                break

            # Shopify may redact customer PII (name/email/phone) when protected customer
            # data access is not approved for the store/app plan.
            sample = customers[0] if customers else {}
            sample_addr = sample.get("default_address") or {}
            pii_missing = (
                not sample.get("first_name")
                and not sample.get("last_name")
                and not sample.get("name")
                and not sample.get("email")
                and not sample.get("phone")
                and not sample_addr.get("name")
                and not sample_addr.get("first_name")
                and not sample_addr.get("last_name")
            )
            if pii_missing:
                self._log_sync(
                    operation="Import Customers",
                    message=(
                        "Shopify returned redacted customer data (name/email/phone are null). "
                        "This is a Shopify protected customer data restriction."
                    ),
                    status="warning",
                    level="warning",
                    channel="import",
                    instance_id=instance.id,
                )

            for customer in customers:
                self._create_or_update_partner(customer, instance)
                imported_count += 1

            since_id = max(c.get("id", 0) for c in customers)
            if since_id > max_seen_id:
                max_seen_id = since_id

        self.env["ir.config_parameter"].sudo().set_param(param_key, str(max_seen_id))
        self._log_sync(
            operation="Import Customers",
            message=(
                f"Scheduled Shopify customer import finished for '{instance.name}'. "
                f"Imported/updated {imported_count} customers."
            ),
            status="success",
            level="info",
            channel="import",
            instance_id=instance.id,
            payload={"imported_count": imported_count, "since_id": max_seen_id},
        )
        return imported_count

    @api.model
    def _build_customer_name(self, customer_data, default_address=None):
        default_address = default_address or {}
        first_name = (customer_data.get("first_name") or "").strip()
        last_name = (customer_data.get("last_name") or "").strip()
        address_first = (default_address.get("first_name") or "").strip()
        address_last = (default_address.get("last_name") or "").strip()

        # Preferred: root first/last, then default address first/last.
        name = f"{first_name} {last_name}".strip()
        if not name:
            name = f"{address_first} {address_last}".strip()
        if not name:
            name = (customer_data.get("name") or default_address.get("name") or "").strip()
        if not name:
            email = (customer_data.get("email") or "").strip()
            if email and "@" in email:
                name = email.split("@")[0].replace(".", " ").replace("_", " ").strip().title()
        customer_id = customer_data.get("id")
        if name:
            return name
        if customer_id:
            return f"Shopify Customer {customer_id}"
        return "Shopify Customer"

    @api.model
    def _create_or_update_partner(self, customer_data, instance):
        shopify_customer_id = str(customer_data.get("id") or "")
        email = (customer_data.get("email") or "").strip().lower()
        default_address = customer_data.get("default_address") or {}
        name = self._build_customer_name(customer_data, default_address)
        country_id = False
        state_id = False
        country_code = (default_address.get("country_code") or "").upper()
        province_code = (default_address.get("province_code") or "").upper()

        if country_code:
            country = self.env["res.country"].search([("code", "=", country_code)], limit=1)
            country_id = country.id or False
            if country and province_code:
                state = self.env["res.country.state"].search(
                    [("country_id", "=", country.id), ("code", "=", province_code)],
                    limit=1,
                )
                state_id = state.id or False

        partner = self.env["res.partner"].search(
            [
                ("shopify_customer_id", "=", shopify_customer_id),
                ("shopify_instance_id", "=", instance.id),
            ],
            limit=1,
        )
        if not partner and email:
            partner = self.env["res.partner"].search(
                [
                    ("email", "=", email),
                    ("shopify_instance_id", "=", instance.id),
                ],
                limit=1,
            )

        vals = {
            "name": name,
            "email": email or False,
            "phone": customer_data.get("phone") or default_address.get("phone") or False,
            "street": default_address.get("address1") or False,
            "street2": default_address.get("address2") or False,
            "city": default_address.get("city") or False,
            "zip": default_address.get("zip") or False,
            "country_id": country_id,
            "state_id": state_id,
            "customer_rank": 1,
            "shopify_customer_id": shopify_customer_id or False,
            "shopify_instance_id": instance.id,
            "active": True,
        }

        if partner:
            # Preserve existing better name if incoming payload has only generic fallback.
            if vals["name"].startswith("Shopify Customer") and partner.name and not partner.name.startswith("Shopify Customer"):
                vals["name"] = partner.name
            partner.write(vals)
            return partner
        return self.env["res.partner"].create(vals)
