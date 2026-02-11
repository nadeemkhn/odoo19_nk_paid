{
    "name": "Leopards Courier Integration",
    "version": "19.0.1.0.0",
    "summary": "Official Leopards Courier Shipping Integration with Auto Booking, Label & Tracking",
    "description": """
Leopards Courier Integration for Odoo
=====================================

Professional API-based shipping integration for Leopards Courier.

Key Features
------------
- Book shipments directly from Delivery Orders
- Real-time shipping rate fetching
- Auto-generate and attach shipment labels
- Consignment tracking & status updates
- Cron-based automatic tracking sync
- Website Sale compatible
- Works with Odoo Delivery carriers

Why Choose This Module?
------------------------
- Fully automated courier workflow
- Reduces manual shipment processing
- Saves operational time
- Clean and scalable architecture

Compatible with Odoo 19.0
    """,
    "license": "LGPL-3",
    "category": "Inventory/Delivery",
    "price": 100.00,
    "currency": "USD",
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
    "author": "Muhammad nadeem (nk)",
    "website": "https://nadeemwazir.com",
    "maintainer": "Muhammad nadeem (nk)",
    "support": "nadeemwazir0123@gmail.com",
    "installable": True,
    "application": False,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}