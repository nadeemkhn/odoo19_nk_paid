{
    "name": "Odoo Shopify Connector",
    "version": "19.0.2.0.0",
    "summary": "Multi-store Shopify <-> Odoo connector with dashboard and workflow rules.",
    "description": """
Shopify Connector for Odoo
=============================
Production-ready integration between Shopify and Odoo with multi-store support,
webhook workflow rules, scheduled imports, manual exports, and a modern dashboard.

Highlights:
- Multi-store Shopify instance management
- Order, customer, product, stock and payment synchronization
- Webhook and scheduler driven automation
- Return and refund synchronization
- OWL dashboard with per-store drill-down actions
""",
    "category": "Sales/Integration",
    "license": "LGPL-3",
    "author": "Muhammad nadeem (nk)",
    "maintainer": "Hameed Pvt.Ltd",
    "support": "nadeemwazir0123@gmail.com",
    "price": 300.0,
    "currency": "USD",
    "external_dependencies": {"python": ["requests"]},
    "depends": [
        "base",
        "sale_management",
        "account",
        "stock",
        "web",
        "purchase",
        "base_setup",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/data.xml",
        "data/server_action.xml",
        "views/views.xml",
        "views/shopify_instance_views.xml",
        "views/webhook_workflow_views.xml",
        "views/inventory_inherit.xml",
        "views/dashboard.xml",
        "views/sale_order_line.xml",
        "views/stock_picking.xml",
        "views/sale_order.xml",
        "views/product_material_type.xml",
        "views/shopify_diagnostic.xml",
        "views/sync_log_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nk_shopify_connector/static/src/js/multi_store_dashboard.js",
            "nk_shopify_connector/static/src/xml/multi_store_dashboard.xml",
            "nk_shopify_connector/static/src/scss/multi_store_dashboard.scss",
        ],
    },
    "demo": [],
    "images": [
        "static/description/icon.png",
        "static/description/src/img/dashboard.png",
        "static/description/src/img/instance.png",
        "static/description/src/img/connector_data.png",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "sequence": 10,
    "keywords": [
        "Shopify",
        "Connector",
        "Odoo",
        "Multi Store",
        "Orders",
        "Inventory",
        "Webhooks",
        "Dashboard",
    ],
}
