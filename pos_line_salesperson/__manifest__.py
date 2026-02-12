{
    "name": "POS Line Salesperson",
    "version": "19.0.1.0.0",
    "summary": "Assign salesperson per POS order line and print on receipt",
    "category": "Point Of Sale",
    "author": "Muhammad Nadeem (nk)",
    "maintainer": "Hameed Pvt.Ltd",
    "support": "nadeemwazir0123@gmail.com",
    "price": 32.0,
    "currency": "USD",
    "images": [
        "static/description/icon.png",
        "static/description/salesperson_popup.png",
    ],
    "license": "LGPL-3",
    "depends": ["point_of_sale", "hr"],
    "data": [
        "views/hr_employee_views.xml",
        "views/pos_config_views.xml",
        "views/pos_order_views.xml",
        "views/pos_order_report_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_line_salesperson/static/src/js/pos_line_salesperson.js",
            "pos_line_salesperson/static/src/xml/pos_line_salesperson.xml"
        ]
    },
    "installable": True,
    "application": False
}
