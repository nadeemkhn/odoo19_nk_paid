from odoo import _, api, models
from odoo.tools.float_utils import float_compare


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def check_pos_stock_guard(self, config_id, product_id, add_qty, order_qty=0.0, session_id=False):
        result = {
            "status": "ok",
            "title": _("Stock Check"),
            "message": "",
            "available_qty": 0.0,
            "allowed_qty": 0.0,
            "required_qty": 0.0,
        }

        config = self.env["pos.config"].sudo().browse(config_id)
        if not config.exists() or not config.pos_stock_guard_enabled:
            return result

        product = self.env["product.product"].sudo().browse(product_id)
        if not product.exists() or not product.product_tmpl_id.is_storable:
            return result

        if product in config.pos_stock_guard_excluded_product_ids:
            return result

        add_qty = float(add_qty or 0.0)
        order_qty = max(float(order_qty or 0.0), 0.0)
        if add_qty <= 0:
            return result

        source_location = config.picking_type_id.default_location_src_id
        if not source_location:
            return result

        product_ctx = product.with_context(location=source_location.id)
        qty_basis = config.pos_stock_guard_qty_basis
        if qty_basis == "forecast_qty":
            available_qty = product_ctx.virtual_available
        else:
            available_qty = product_ctx.free_qty

        reserve_qty = max(config.pos_stock_guard_buffer_qty, 0.0)
        allowed_qty = available_qty - reserve_qty
        required_qty = order_qty + add_qty
        rounding = product.uom_id.rounding or 0.01

        result.update(
            {
                "available_qty": available_qty,
                "allowed_qty": allowed_qty,
                "required_qty": required_qty,
            }
        )

        if float_compare(required_qty, allowed_qty, precision_rounding=rounding) <= 0:
            return result

        status = "block"
        if config.pos_stock_guard_mode == "warn":
            status = "warn"
        elif config.pos_stock_guard_allow_override and self.env.user.has_group(
            "pos_stock_guard_plus.group_pos_stock_guard_override"
        ):
            status = "warn"

        basis_label = _("Available Quantity") if qty_basis == "free_qty" else _("Forecast Quantity")
        detail = _(
            "Product: %(product)s\n"
            "Basis: %(basis)s\n"
            "Available: %(available).2f\n"
            "Reserve: %(reserve).2f\n"
            "Allowed After Reserve: %(allowed).2f\n"
            "Already in Order: %(in_order).2f\n"
            "Trying to Add: %(to_add).2f\n"
            "Requested Total: %(required).2f",
            product=product.display_name,
            basis=basis_label,
            available=available_qty,
            reserve=reserve_qty,
            allowed=allowed_qty,
            in_order=order_qty,
            to_add=add_qty,
            required=required_qty,
        )

        custom_message = (config.pos_stock_guard_message or "").strip()
        message = (
            _("%(custom)s\n\nStock Details\n%(detail)s", custom=custom_message, detail=detail)
            if custom_message
            else _("Stock Details\n%(detail)s", detail=detail)
        )

        result.update(
            {
                "status": status,
                "title": _("Stock Guard Warning") if status == "warn" else _("Out of Stock"),
                "message": message,
            }
        )

        log_session = self.env["pos.session"].sudo().browse(session_id) if session_id else self.env["pos.session"]
        log_values = {
            "action": status,
            "user_id": self.env.user.id,
            "config_id": config.id,
            "session_id": log_session.id if log_session.exists() else False,
            "product_id": product.id,
            "stock_basis": qty_basis,
            "available_qty": available_qty,
            "reserve_qty": reserve_qty,
            "order_qty": order_qty,
            "add_qty": add_qty,
            "required_qty": required_qty,
            "allowed_qty": allowed_qty,
            "detail": detail,
        }
        self.env["pos.stock.guard.log"].sudo().create(log_values)

        return result
