from odoo import models, fields, api

class ShopifyDashboard(models.Model):
    _name = 'shopify.dashboard'
    _description = 'Simple Shopify Orders Summary'
    _auto = False  # SQL View-based model

    id = fields.Id()
    total_orders = fields.Integer(string="Total Orders", readonly=True)
    total_amount = fields.Float(string="Total Sales Amount", readonly=True)
    paid_orders = fields.Integer(string="Paid Orders", readonly=True)
    partial_orders = fields.Integer(string="Partially Paid Orders", readonly=True)
    unpaid_orders = fields.Integer(string="Unpaid Orders", readonly=True)

    def init(self):
        self.env.cr.execute("DROP VIEW IF EXISTS shopify_dashboard;")
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW shopify_dashboard AS (
                SELECT
                    1 AS id,
                    COUNT(so.id) AS total_orders,
                    SUM(so.amount_total) AS total_amount,
                    COUNT(CASE WHEN inv.payment_state = 'paid' THEN 1 END) AS paid_orders,
                    COUNT(CASE WHEN inv.payment_state = 'partial' THEN 1 END) AS partial_orders,
                    COUNT(CASE WHEN inv.payment_state = 'not_paid' OR inv.payment_state IS NULL THEN 1 END) AS unpaid_orders
                FROM sale_order so
                LEFT JOIN account_move inv ON inv.invoice_origin = so.name AND inv.move_type = 'out_invoice'
                WHERE so.origin ILIKE 'Shopify-%'
            );
        """)

    def action_open_shopify_orders(self):
        return {
            'name': 'Shopify Orders',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('origin', 'ilike', 'Shopify-%')],
            'context': {'search_default_group_by_customer': 1},
        }
