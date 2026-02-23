{
    "name": "POS Stock Restriction and Overselling Control",
    "version": "19.0.1.0.0",
    "summary": "POS stock restriction: block or warn on low stock and prevent overselling",
    "price": 90.0,
    "currency": "USD",
    "category": "Point of Sale",
    "author": "Muhammad Nadeem (nk)",
    "maintainer": "Hameed Pvt.Ltd",
    "support": "nadeemwazir0123@gmail.com",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "stock"],
    "images": [
        "static/description/icon.png",
        "static/description/images/pos_stock_guard_configuration.png",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/pos_config_views.xml",
        "views/pos_stock_guard_log_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_stock_guard_plus/static/src/app/pos_stock_guard_patch.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
