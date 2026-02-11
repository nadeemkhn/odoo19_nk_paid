# Leopards Courier Integration for Odoo 19

This module integrates Odoo Delivery with Leopards Courier for rates, shipment booking, labels, tracking, and cancellations.

## Key Behavior

- Leopards shipment is created only when user clicks **Book a Shipment** on delivery order.
- **Validate** does not send shipment to Leopards.
- Shipment can be cancelled directly from Odoo using **Cancel in Leopards**.
- Shipment status is updated by scheduled tracking refresh and manual refresh action.
- Webhook endpoint is not used.

## Features

1. Delivery Carrier Integration
- Adds delivery type `leopards` in `delivery.carrier`.
- Carrier fields:
  - `leopards_api_key`
  - `leopards_api_secret` (Leopards API Password)
  - `leopards_api_url`
  - `leopards_account_id`
  - `leopards_shipper_id`
- Includes **Test Connection** button.

2. Shipping Rate Calculation
- Uses Leopards `getTariffDetails`.
- Fallbacks for weight:
  - context `order_weight`
  - `sale.order.shipping_weight`
  - sum of sale line product weights
  - default `1.0 kg`
- Uses fixed price when API is unavailable and fixed price is configured.

3. Shipment Booking
- Uses Leopards `bookPacket` when clicking **Book a Shipment**.
- On success:
  - writes CN to `carrier_tracking_ref`
  - writes status to `leopards_last_status`
  - attaches label PDF when provided by API

4. Tracking
- Uses Leopards `trackBookedPacket`.
- Tracking link:
  - `https://www.leopardscourier.com/tracking?cn=<CN>`
- Manual refresh available from delivery order.
- Auto refresh runs by cron.

5. Cancellation
- Button **Cancel in Leopards** on delivery order.
- Background cron processes pending cancellation requests.

6. Shipper Management
- Model `leopards.shipper` for sender details.
- Menu: `Inventory > Configuration > Leopards Shippers`.

## Installation

1. Put module in custom addons path.
2. Update Apps list.
3. Install **Leopards Courier Integration**.

## Configuration

1. Go to `Inventory > Configuration > Delivery Methods`.
2. Create/edit method with `Delivery Type = Leopards Courier`.
3. Enter:
  - API Key
  - API Secret (must exactly match Leopards API Password)
  - API URL (production or staging)
  - optional Account ID
  - optional default Shipper
4. Click **Test Connection** and save.

## User Flow

1. Create Sales Order and choose Leopards delivery method.
2. Confirm Sales Order.
3. Process delivery transfer and click **Validate**.
4. On validated delivery, click **Book a Shipment** to create CN.
5. Use **Tracking** / **Refresh Tracking** to monitor status.
6. Use **Cancel in Leopards** if needed.

## Scheduled Actions

- `Leopards: Auto-refresh tracking status`
- `Leopards: Process queued cancellations`

Keep both active for smooth operations.

## Troubleshooting

- Invalid API credentials:
  - verify API Key and API Password exactly
  - verify correct API URL environment
- No CN created:
  - ensure **Book a Shipment** was clicked after validation
  - check Odoo logs for `bookPacket` response
- Status not changing:
  - verify tracking cron is active
  - use manual **Refresh Tracking** action

## Dependencies

- Odoo modules: `delivery`, `sale_stock`, `stock_delivery`, `website_sale`
- Python package: `requests`

## Support

- Maintainer: Hameed Pvt.Ltd
- Email: nadeemwazir0123@gmail.com

## License

LGPL-3
