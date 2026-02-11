from . import models

def post_init_hook(env):
    """Set invoice_policy to 'estimated' so shipping fee shows at website checkout. Allow COD."""
    env.cr.execute("""
        UPDATE delivery_carrier
        SET invoice_policy = 'estimated',
            allow_cash_on_delivery = true
        WHERE delivery_type = 'leopards'
    """)
