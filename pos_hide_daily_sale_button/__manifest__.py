# -*- coding: utf-8 -*-

{
    'name': 'POS Daily Sale Button Control',
    'version': '19.0.1.0.0',
    'category': 'Point Of Sale',
    'summary': 'User-based control for Daily Sale button visibility in POS closing popup',
    'description': """
        POS Daily Sale Button Control
        =============================
        
        This module provides user-specific control over the Daily Sale button visibility 
        in the Point of Sale closing popup footer.
        
        Features:
        ---------
        * User-specific setting: Each user can independently enable/disable button visibility
        * Easy configuration: Setting available in User form view under POS Settings tab
        * Seamless integration: Works with Odoo 19 POS without modifying core functionality
        * Flexible control: Different users can have different preferences
        
        Configuration:
        --------------
        1. Go to Settings → Users & Companies → Users
        2. Open the desired user
        3. Navigate to POS Settings tab
        4. Toggle "Hide Daily Sale Button" option
        
        Usage:
        ------
        When enabled: Daily Sale button will be visible in POS closing popup
        When disabled: Daily Sale button will be hidden from POS closing popup
        
        Support:
        --------
        GitHub: https://github.com/nadeemkhn
        Email: nadeemwazir0123@gmail.com
    """,
    'author': 'Muhammad nadeem (nk)',
    'website': 'https://my-portpolio-en28.vercel.app/',
    'depends': ['point_of_sale'],
    "images": ['static/description/main_screen.png'],
    'data': [
        'views/res_users_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_hide_daily_sale_button/static/src/css/hide_daily_sale_button.css',
            'pos_hide_daily_sale_button/static/src/xml/templates.xml',
            'pos_hide_daily_sale_button/static/src/js/hide_daily_sale_button.js',
        ],
    },
    'price': 10,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}
