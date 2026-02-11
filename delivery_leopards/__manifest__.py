{
    "name": "Leopards Courier Integration",
    "version": "19.0.1.0.0",
    "summary": "Leopards Courier shipping integration for Odoo",
    "description": """
Leopards Courier Integration for Odoo
=====================================

This module connects Odoo Delivery with Leopards Courier:
- Book shipments from delivery orders
- Fetch shipping rates
- Generate and attach labels
- Track consignments
    """,
    "license": "LGPL-3",
    "category": "Inventory/Delivery",
    "depends": [
        "delivery",
        "sale_stock",
        "stock_delivery",
        "website_sale",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/leopards_shipper_views.xml",
        "views/delivery_carrier_views.xml",
        "views/sale_order_views.xml",
        "views/stock_picking_views.xml",
    ],
    "images": ["static/description/icon.png"],
    "author": "Nadeem Khan",
    "website": "https://nadeemwazir.com",
    "maintainer": "Hameed Pvt.Ltd",
    "support": "nadeemwazir0123@gmail.com",
    "installable": True,
    "application": False,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
