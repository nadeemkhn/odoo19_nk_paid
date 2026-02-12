from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    line_salesperson_employee_ids = fields.Many2many(
        "hr.employee",
        "pos_config_line_salesperson_employee_rel",
        "config_id",
        "employee_id",
        string="Line Salespersons",
        help="Only these employees will appear in the POS line salesperson selector for this shop.",
    )

    @api.model
    def get_pos_salespersons(self, config_id):
        # POS user often lacks direct hr.employee read rights, so fetch with sudo.
        config = self.sudo().browse(config_id)
        if not config.exists():
            return []

        Employee = self.env["hr.employee"].sudo()

        # Keep domains primitive (ids/booleans) to avoid model-valued domain warnings.
        base_domain = [
            ("active", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", config.company_id.id),
        ]

        allowed_ids = set()
        has_explicit_allowed_ids = False

        if config.line_salesperson_employee_ids:
            allowed_ids |= set(config.line_salesperson_employee_ids.ids)
            has_explicit_allowed_ids = True

        # If POS HR is enabled, also respect the employees allowed on this POS config.
        if getattr(config, "module_pos_hr", False):
            pos_hr_ids = set(
                config.basic_employee_ids.ids
                + config.advanced_employee_ids.ids
                + config.minimal_employee_ids.ids
            )
            # If POS HR has explicit employees, intersect with line-salesperson employees when both exist.
            if pos_hr_ids:
                if has_explicit_allowed_ids:
                    allowed_ids &= pos_hr_ids
                else:
                    allowed_ids = pos_hr_ids
                    has_explicit_allowed_ids = True

        if has_explicit_allowed_ids:
            if not allowed_ids:
                return []
            base_domain.append(("id", "in", list(allowed_ids)))
        else:
            # Backward-compatible behavior when no shop-level list is configured.
            base_domain.append(("show_in_pos_salesperson", "=", True))

        employees = Employee.search(base_domain, order="name")
        return [
            {
                "id": emp.id,
                "name": emp.name,
                "source": "employee",
                "image": f"/web/image/hr.employee.public/{emp.id}/avatar_128",
            }
            for emp in employees
        ]
