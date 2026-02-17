# Website Quantity Buttons (Odoo 19)

Backend-configurable quantity buttons for `website_sale` product cards and product detail pages.

## Features

- Show quantity buttons on:
  - Shop product cards
  - Dynamic snippet product cards
  - Product detail page
- Configure values like `1,2,3` or `1,2,3,4,5,6` (up to 6 buttons).
- Configure button label (for example `Pack`, `Unit`, `Box`).
- Website-level configuration for all products of a website.
- Product-level configuration (with website-level fallback).
- Dynamic frontend price update on quantity button click.
- Optional discount badge per quantity based on pricelist quantity rules.
- Savings line under buttons (example: `Save Rs. 73 (3%)`) on card and detail page.
- Responsive button layout for desktop and mobile.
- Mobile optimized: 3 quantity buttons per row.

## Dependency

- `website_sale`

## Configuration

### Website-level (global for website)

1. Go to `Website > Configuration > Websites`.
2. Open your website record.
3. In `Quantity Buttons`:
   - Enable `Website Quantity Buttons`
   - Set `Website Quantity Values` (example: `1,2,3,4,5,6`)
   - Set `Website Quantity Label` (example: `Unit`)

### Product-level (priority)

1. Go to `Sales > Products > Products`.
2. Open a product.
3. In `Website Quantity Buttons`:
   - Enable `Website Quantity Buttons`
   - Set `Website Quantity Values`
   - Set `Website Quantity Label`

If product-level is enabled, product values are used first.
If product-level is disabled, website-level values are used.

## Pricelist and discount badge behavior

- Quantity price is read from Odoo pricelist rules using `Min. Quantity`.
- Badge percentage is calculated from unit-price difference between qty `1` and selected quantity.
- If no qty discount exists for a quantity, badge stays hidden.
- Savings amount and savings percentage are shown below quantity buttons when selected qty has discount.

## Notes

- Designed for Odoo 19.
- No core overwrite. Uses inherited views, Python model extensions, and frontend assets.

## Support

- Maintainer: `Hameed Pvt.Ltd`
- Email: `nadeemwazir0123@gmail.com`

## Company Details

- Author: `Muhammad Nadeem (nk)`
- Price: `$55` (USD)
