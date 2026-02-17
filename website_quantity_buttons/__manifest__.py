{
    "name": "Website Quantity Buttons",
    "version": "19.0.1.1.0",
    "summary": "Website/product quantity buttons with dynamic pricing, discount badges, and savings text",
    "category": "Website",
    "license": "LGPL-3",
    "author": "Muhammad Nadeem (nk)",
    "maintainer": "Hameed Pvt.Ltd",
    "support": "nadeemwazir0123@gmail.com",
    "price": 55.0,
    "currency": "USD",
    "images": [
        "static/description/icon.png",
        "static/description/screen_website_settings.png",
        "static/description/screen_product_settings.png",
        "static/description/screen_pricelist_rules.png",
    ],
    "depends": ["website_sale"],
    "data": [
        "views/product_template_views.xml",
        "views/website_views.xml",
        "views/website_sale_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "website_quantity_buttons/static/src/js/website_quantity_buttons.js",
            "website_quantity_buttons/static/src/scss/website_quantity_buttons.scss",
        ],
    },
    "installable": True,
    "application": False,
}
