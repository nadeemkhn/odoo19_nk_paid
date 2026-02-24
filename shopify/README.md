# Shopify Connector for Odoo

Production-ready Shopify integration for Odoo with multi-store support, webhook workflows, scheduler jobs, and a modern OWL dashboard.

## Pricing

- Price: USD 300 (one-time license)

## Key Features

- Multi-store instance configuration in one Odoo database
- Import Shopify orders, products, customers, and stock
- Export products and stock manually (with bulk actions)
- Webhook-first order flow with rule-based auto workflow
- Auto actions by status: confirm, invoice, payment, and delivery validation
- Return and refund synchronization from Shopify to Odoo
- Detailed sync logs with operation-level traceability
- Store-wise OWL dashboard with quick drill-down actions

## Module Functionalities

- Multi-store instance configuration
  - Draft/Confirm state, domain/token configuration, location mapping, credential validation.
- Order import and processing
  - Webhook endpoints for create/update/order edit/fulfillment.
  - Import service with per-instance filtering and duplicate protection.
  - Status-driven webhook workflow rules with sequence priority.
- Workflow automation
  - Rule actions: auto confirm order, create invoice, register payment, validate delivery.
  - Supports quotation-style flow or fully automated order-to-cash flow based on status rules.
- Product and stock sync
  - Scheduled product import from Shopify.
  - Manual single/bulk product export to Shopify.
  - Bulk stock import from Shopify to Odoo.
  - Scheduled stock export from Odoo to Shopify.
  - SKU validation logic for safe export behavior.
- Customer sync
  - Scheduled customer import.
  - Customer create webhook import.
- Payment, returns, and refunds
  - Push paid customer invoice status to Shopify.
  - Shopify refund and return webhook handling.
  - Return picking creation/validation and refund document linking.
- Monitoring and diagnostics
  - OWL multi-store dashboard with drill-down actions.
  - Sync logs by operation/channel/status/record.
  - Connector Data menus for orders, customers, products, invoices, refunds, deliveries/returns, queue.

## Main Menus

- Shopify Instances
- Dashboard
- Connector Data
  - Orders
  - Customers
  - Products
  - Invoices
  - Refunds
  - Deliveries/Returns
  - Imported Queue
- Sync Logs

## Installation

1. Copy module to your custom addons path.
2. Restart Odoo.
3. Update Apps List.
4. Install `Shopify Connector`.

## Basic Configuration

1. Create a Shopify instance record.
2. Set Shopify domain and Admin API token.
3. Set Shopify location mapping.
4. Confirm the instance.
5. Configure webhook workflow rules (priority sequence based).

## Webhook Workflow Rules

For each instance, define rules with:

- Order Status
- Payment Status
- Delivery Status
- Auto Confirm
- Create Invoice
- Register Payment
- Validate Delivery

The first matching active rule is applied.

## Notes

- Products without SKU can be excluded from export by connector logic.
- Duplicate Shopify orders are protected by Shopify reference checks.
- Refund posting and reconciliation rely on matching invoices and journals.

## Support

- Author: Muhammad nadeem (nk)
- Maintainer: Hameed Pvt.Ltd
- Email: nadeemwazir0123@gmail.com
