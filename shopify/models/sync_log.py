import json

from odoo import api, fields, models


class ShopifySyncLog(models.Model):
    _name = "shopify.sync.log"
    _description = "Shopify Sync Log"
    _order = "create_date desc, id desc"

    create_date = fields.Datetime(readonly=True)
    instance_id = fields.Many2one("shopify.instance", string="Instance", index=True)
    user_id = fields.Many2one("res.users", string="User", default=lambda self: self.env.user, readonly=True)
    channel = fields.Selection(
        [
            ("import", "Import"),
            ("export", "Export"),
            ("webhook", "Webhook"),
            ("manual", "Manual"),
            ("system", "System"),
        ],
        string="Channel",
        default="system",
        required=True,
        index=True,
    )
    operation = fields.Char(string="Operation", required=True, index=True)
    model_name = fields.Char(string="Model", index=True)
    res_id = fields.Integer(string="Record ID")
    reference = fields.Char(string="Reference", index=True)
    status = fields.Selection(
        [
            ("success", "Success"),
            ("failed", "Failed"),
            ("warning", "Warning"),
            ("info", "Info"),
        ],
        string="Status",
        default="info",
        required=True,
        index=True,
    )
    level = fields.Selection(
        [
            ("debug", "Debug"),
            ("info", "Info"),
            ("warning", "Warning"),
            ("error", "Error"),
        ],
        string="Level",
        default="info",
        required=True,
    )
    message = fields.Text(string="Message", required=True)
    payload = fields.Text(string="Payload")
    response = fields.Text(string="Response")
    duration_ms = fields.Float(string="Duration (ms)")

    @api.model
    def log_event(
        self,
        operation,
        message,
        *,
        status="info",
        level="info",
        channel="system",
        model_name=False,
        res_id=False,
        reference=False,
        payload=None,
        response=None,
        instance_id=False,
        duration_ms=0.0,
    ):
        def _to_text(value):
            if value in (None, False, ""):
                return False
            if isinstance(value, str):
                return value
            try:
                return json.dumps(value, ensure_ascii=True, default=str)
            except Exception:
                return str(value)

        vals = {
            "operation": operation or "Shopify Sync",
            "message": message or "",
            "status": status,
            "level": level,
            "channel": channel,
            "model_name": model_name or False,
            "res_id": res_id or False,
            "reference": reference or False,
            "payload": _to_text(payload),
            "response": _to_text(response),
            "instance_id": instance_id or False,
            "duration_ms": duration_ms or 0.0,
        }
        return self.sudo().create(vals)
