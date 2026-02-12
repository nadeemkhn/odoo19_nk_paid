{
    'name': 'Dynamic Upselling Pos',
    'version': '18.0.1.0.0',
    'author': 'Muhammad Nadeem (nk)',
    'company': 'Hameed Pvt.Ltd',
    'maintainer': 'Hameed Pvt.Ltd',
    'support': 'nadeemwazir0123@gmail.com',
    'category': 'Point of Sale',
    'summary': 'Dynamic upselling features for Point of Sale',
    'description': """
Dynamic Upselling POS
====================

This module suggests extra products during POS checkout.
You can configure upselling products per POS shop.

How to use:
1. Go to Point of Sale > Configuration > Dynamic adding Products.
2. Create a new rule.
3. Select Shops where the rule applies.
4. Select Products to suggest.
5. Open POS and proceed to checkout.
6. Use Add/Okay/Skip in the upselling popup.
""",
    'license': 'LGPL-3',
    'price': 40.0,
    'currency': 'USD',
    'depends': ['point_of_sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/dynamic_upselling_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'dynamic_upselling_pos/static/src/app/**/*.js',
            'dynamic_upselling_pos/static/src/app/**/*.xml',
            'dynamic_upselling_pos/static/src/app/**/*.scss',
        ],
    },
    'images': [
        'static/description/screenshot_01_popup.png',
        'static/description/screenshot_02_menu.png',
        'static/description/screenshot_03_config_form.png',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
