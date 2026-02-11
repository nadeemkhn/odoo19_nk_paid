from . import models

def post_init_hook(env):
    """Set sane defaults for Leopards carriers on install without assuming optional columns exist."""
    env.cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'delivery_carrier'
          AND column_name IN ('invoice_policy', 'allow_cash_on_delivery')
    """)
    existing = {row[0] for row in env.cr.fetchall()}
    sets = []
    if 'invoice_policy' in existing:
        sets.append("invoice_policy = 'estimated'")
    if 'allow_cash_on_delivery' in existing:
        sets.append("allow_cash_on_delivery = true")
    if sets:
        env.cr.execute(f"""
            UPDATE delivery_carrier
            SET {', '.join(sets)}
            WHERE delivery_type = 'leopards'
        """)
