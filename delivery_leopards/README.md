# Leopards Courier Integration for Odoo 16

Leopards Courier Integration connects Odoo Delivery with Leopards API for shipping rates, booking, tracking, labels, and cancellation workflows.

## App Store Ready Summary

- Version: Odoo 16.0
- Price: USD 90
- License: LGPL-3
- Category: Inventory/Delivery

## Main Features

- Adds delivery type `leopards` in `delivery.carrier`
- Live rate calculation using Leopards `getTariffDetails`
- Manual shipment booking from delivery orders (`bookPacket`)
- Tracking updates from Leopards `trackBookedPacket`
- Cancel shipment from Odoo (`cancelBookedPackets`)
- Optional shipper master (`leopards.shipper`) with per-picking override
- Auto tracking and cancellation processing via scheduled actions
- Website Sale compatible with Odoo delivery flow

## Business Workflow

1. Create or edit a Delivery Method and set **Delivery Type = Leopards Courier**.
2. Configure API credentials and test connection.
3. Add shipping on quotation/sales order and fetch rate.
4. Validate transfer (stock operation only).
5. Click **Book a Shipment** on delivery order to generate CN.
6. Track and cancel shipment directly from Odoo when needed.

## Installation

1. Copy module into your custom addons path.
2. Restart Odoo server.
3. Update Apps List.
4. Install **Leopards Courier Integration**.

## Configuration

Go to `Inventory > Configuration > Delivery Methods` and configure:

- API Key
- API Secret (must match Leopards API Password exactly)
- API URL (Production or Staging)
- Optional Account ID
- Optional default Shipper

Then click **Test Connection** and save.

## Scheduled Actions

Keep these scheduled actions active:

- `Leopards: Auto-refresh tracking status`
- `Leopards: Process queued cancellations`

## Dependencies

- Odoo modules: `delivery`, `sale_stock`, `website_sale`
- Python package: `requests`

## Support

- Author/Maintainer: Muhammad nadeem (nk)
- Website: https://nadeemwazir.com
- Support: nadeemwazir0123@gmail.com

## Notes

- Free-shipping rules configured on the delivery method can set shipping line to 0.00 even if API rate is fetched successfully.
- Price is set in manifest as `USD 90`.
