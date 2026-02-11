# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import re
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    leopards_pending_cancel = fields.Boolean(
        string='Leopards Cancel Pending',
        copy=False,
        help='Cancellation requested; processing in background via Leopards API.',
    )
    leopards_last_status = fields.Char(
        string='Leopards Status',
        copy=False,
        help='Last known status from Leopards (RC=Booked, AC=Out for Delivery, DV=Delivered, etc.). Updated by scheduled tracking refresh or manual Refresh Tracking.',
    )
    leopards_shipper_id = fields.Many2one(
        'leopards.shipper',
        string='Shipper',
        required=True,
        domain=[('active', '=', True)],
        help='Override shipper for this delivery. Leave empty to use the carrier\'s default shipper.',
    )

    def _leopards_status_rank(self, status_code):
        """Higher rank means a more advanced/final shipment state."""
        code = (status_code or '').strip().upper()
        rank_map = {
            'RC': 10,  # Booked
            'SP': 15,
            'AR': 20,
            'DP': 30,
            'AC': 40,
            'PN1': 45,
            'PN2': 46,
            'RO': 60,
            'RN1': 65,
            'RN2': 66,
            'NR': 70,
            'RW': 80,
            'DW': 85,
            'RS': 90,
            'DR': 90,
            'CN': 95,
            'CL': 95,
            'CANCELLED': 95,
            'CANCELED': 95,
            'DV': 100,  # Delivered
            # Leopards textual variants seen in APIs/portal
            'PICKUP REQUEST NOT SEND': 10,
            'BOOKED': 10,
        }
        return rank_map.get(code, 50)

    def _leopards_extract_status_code(self, status_text):
        """Extract status code from display text like 'Delivered (DV)'."""
        text = (status_text or '').strip()
        if not text:
            return ''
        match = re.search(r'\(([^()]*)\)\s*$', text)
        if match:
            return (match.group(1) or '').strip().upper()
        return text.upper()

    def _leopards_set_last_status(self, status_code, status_name=None):
        """
        Set status while preventing regressions (e.g., AC -> RC).
        Returns True when value was written.
        """
        self.ensure_one()
        code = (status_code or '').strip()
        if not code:
            return False
        code_u = code.upper()
        name = (status_name or code).strip()
        new_status = f"{name} ({code})"

        current_code = self._leopards_extract_status_code(self.leopards_last_status)
        if current_code:
            if self._leopards_status_rank(code_u) < self._leopards_status_rank(current_code):
                _logger.info(
                    "Skip status downgrade for %s: current=%s incoming=%s",
                    self.name, current_code, code_u
                )
                return False

        if self.leopards_last_status != new_status:
            self.leopards_last_status = new_status
            return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('leopards_shipper_id') and vals.get('carrier_id'):
                carrier = self.env['delivery.carrier'].browse(vals['carrier_id'])
                if carrier.delivery_type == 'leopards' and carrier.leopards_shipper_id:
                    vals['leopards_shipper_id'] = carrier.leopards_shipper_id.id
        return super().create(vals_list)

    @api.onchange('carrier_id', 'leopards_shipper_id')
    def _onchange_carrier_id_leopards_shipper(self):
        """Auto-select shipper from carrier when empty (e.g. user cleared it)."""
        if not self.leopards_shipper_id and self.carrier_id and self.carrier_id.delivery_type == 'leopards' and self.carrier_id.leopards_shipper_id:
            self.leopards_shipper_id = self.carrier_id.leopards_shipper_id

    def _send_confirmation_email(self):
        # Prevent delivery module auto "send_to_shipper" on Validate for Leopards.
        return super(StockPicking, self.with_context(leopards_skip_validate_send=True))._send_confirmation_email()

    def send_to_shipper(self):
        # Keep manual "Book a Shipment" button behavior, but skip Validate auto-send for Leopards.
        if self.env.context.get('leopards_skip_validate_send'):
            leopards = self.filtered(lambda p: p.delivery_type == 'leopards')
            others = self - leopards
            if others:
                super(StockPicking, others).send_to_shipper()
            for picking in leopards:
                _logger.info("Skipped auto-send on Validate for Leopards picking %s", picking.name)
            return
        return super().send_to_shipper()

    def cancel_shipment(self):
        """For Leopards: cancel immediately via API. Others: use standard flow."""
        leopards = self.filtered(lambda p: p.carrier_id and p.carrier_id.delivery_type == 'leopards')
        others = self - leopards
        if others:
            super(StockPicking, others).cancel_shipment()
        for picking in leopards:
            if not picking.carrier_tracking_ref:
                raise UserError(_('No tracking number found for this shipment'))
            result = picking.carrier_id._leopards_cancel_shipment_api(picking.carrier_tracking_ref)
            if result and result.get('success'):
                picking.carrier_tracking_ref = False
                picking.leopards_last_status = 'Cancelled (CN)'
                picking.leopards_pending_cancel = False
                picking.message_post(
                    body=_('Shipment cancelled successfully in Leopards Courier.'),
                    message_type='notification',
                )
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No response'
                picking.leopards_pending_cancel = False
                raise UserError(_('Leopards cancellation failed: %s') % error_msg)

    def _cron_refresh_leopards_tracking(self):
        """Auto-refresh tracking status for recent Leopards shipments."""
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=30)
        pickings = self.search([
            ('carrier_tracking_ref', '!=', False),
            ('delivery_type', '=', 'leopards'),
            ('state', '!=', 'cancel'),
            ('leopards_last_status', 'not in', ['Delivered (DV)', 'Returned to Shipper (RS)', 'Delivered to Vendor (DR)', 'Cancelled (CN)']),
            '|',
            ('write_date', '>=', cutoff),
            ('create_date', '>=', cutoff),
        ], limit=50, order='write_date desc, id desc')
        _logger.info("Auto-refresh tracking for %s Leopards deliveries", len(pickings))
        for picking in pickings:
            try:
                result = picking.carrier_id._leopards_track_shipment_api(picking.carrier_tracking_ref)
                
                # Handle cancelled packets
                if result.get('is_cancelled'):
                    if picking.leopards_last_status != 'Cancelled (CN)':
                        picking.leopards_last_status = 'Cancelled (CN)'
                        _logger.info("Auto-updated %s status: Cancelled (CN)", picking.name)
                    continue
                
                if result.get('success'):
                    packet_list = result.get('packet_list') or []
                    if packet_list:
                        status_mapping = {
                            'RC': 'Consignment Booked', 'AC': 'Out For Delivery', 'DV': 'Delivered',
                            'PN1': 'First Attempt', 'PN2': 'Second Attempt', 'RO': 'Being Return',
                            'RN1': 'First Return Attempt', 'RN2': 'Second Return Attempt',
                            'RW': 'Returned to Warehouse', 'DW': 'Delivered to Warehouse',
                            'RS': 'Returned to Shipper', 'DR': 'Delivered to Vendor',
                            'AR': 'Arrived At Station', 'DP': 'Dispatched', 'NR': 'Ready for Return',
                            'SP': 'Shipment Picked', 'CN': 'Cancelled', 'CL': 'Cancelled',
                            'CANCELLED': 'Cancelled', 'CANCELED': 'Cancelled',
                        }
                        for pkt in (packet_list if isinstance(packet_list, list) else [packet_list]):
                            if isinstance(pkt, dict):
                                for key in ('booked_packet_status', 'consignment_status', 'current_status', 'status', 'status_code', 'Status'):
                                    val = pkt.get(key)
                                    if val and str(val).strip():
                                        code = str(val).strip()
                                        name = status_mapping.get(code, code)
                                        if picking._leopards_set_last_status(code, name):
                                            _logger.info("Auto-updated %s status: %s (%s)", picking.name, name, code)
                                        break
                                break
            except Exception as e:
                _logger.warning("Auto-refresh tracking failed for %s: %s", picking.name, str(e))

    def _cron_process_leopards_cancellations(self):
        """Process queued Leopards cancellations in background."""
        pickings = self.search([
            ('leopards_pending_cancel', '=', True),
            ('carrier_tracking_ref', '!=', False),
            ('delivery_type', '=', 'leopards'),
        ], limit=10)
        for picking in pickings:
            try:
                result = picking.carrier_id._leopards_cancel_shipment_api(picking.carrier_tracking_ref)
                if result and result.get('success'):
                    picking.carrier_tracking_ref = False
                    picking.leopards_pending_cancel = False
                    picking.message_post(
                        body=_('Shipment cancelled successfully in Leopards Courier.'),
                        message_type='notification',
                    )
                    _logger.info("Leopards cancellation succeeded: %s", picking.name)
                else:
                    error_msg = result.get('error', 'Unknown error') if result else 'No response'
                    picking.leopards_pending_cancel = False
                    picking.message_post(
                        body=_('Leopards cancellation failed: %s') % error_msg,
                        message_type='notification',
                    )
                    _logger.warning("Leopards cancellation failed for %s: %s", picking.name, error_msg)
            except Exception as e:
                _logger.exception("Leopards cron cancel error for %s: %s", picking.name, e)
                picking.leopards_pending_cancel = False
                picking.message_post(
                    body=_('Leopards cancellation error: %s') % str(e),
                    message_type='notification',
                )

    def action_refresh_tracking(self):
        """
        Fetch tracking status from Leopards API and post to chatter.
        """
        self.ensure_one()
        if not self.carrier_tracking_ref:
            raise UserError(_('No tracking number to refresh.'))
        if not self.carrier_id or self.carrier_id.delivery_type != 'leopards':
            raise UserError(_('This action is only for Leopards Courier deliveries.'))
        result = self.carrier_id._leopards_track_shipment_api(self.carrier_tracking_ref)
        
        # Handle cancelled packets (API may return success=False with cancellation info)
        if result.get('is_cancelled'):
            self.leopards_last_status = 'Cancelled (CN)'
            self.message_post(
                body=_('Leopards Tracking: Shipment has been CANCELLED.\nCN: %s') % self.carrier_tracking_ref,
                message_type='notification',
            )
            return True
        
        if not result.get('success'):
            raise UserError(_('Could not fetch tracking: %s') % (result.get('error') or 'Unknown error'))
        packet_list = result.get('packet_list') or []
        if not packet_list:
            self.message_post(
                body=_('No tracking details returned from Leopards for CN: %s') % self.carrier_tracking_ref,
                message_type='notification',
            )
            return True
        status_mapping = {
            'RC': 'Consignment Booked', 'AC': 'Out For Delivery', 'DV': 'Delivered',
            'PN1': 'First Attempt', 'PN2': 'Second Attempt', 'RO': 'Being Return',
            'RN1': 'First Return Attempt', 'RN2': 'Second Return Attempt',
            'RW': 'Returned to Warehouse', 'DW': 'Delivered to Warehouse',
            'RS': 'Returned to Shipper', 'DR': 'Delivered to Vendor',
            'AR': 'Arrived At Station', 'DP': 'Dispatched', 'NR': 'Ready for Return',
            'SP': 'Shipment Picked', 'CN': 'Cancelled', 'CL': 'Cancelled',
            'CANCELLED': 'Cancelled', 'CANCELED': 'Cancelled',
        }
        # Extract and save status from first packet (check common field names)
        status_found = False
        for pkt in (packet_list if isinstance(packet_list, list) else [packet_list]):
            if isinstance(pkt, dict):
                for key in ('booked_packet_status', 'consignment_status', 'current_status', 'status', 'status_code', 'Status'):
                    val = pkt.get(key)
                    if val and str(val).strip():
                        code = str(val).strip()
                        name = status_mapping.get(code, code)
                        self._leopards_set_last_status(code, name)
                        status_found = True
                        break
                if not status_found:
                    # Check nested activities for latest status
                    activities = pkt.get('activities') or pkt.get('history') or []
                    if isinstance(activities, list) and activities:
                        last = activities[-1] if isinstance(activities[-1], dict) else {}
                        for key in ('status', 'status_code', 'Status'):
                            val = last.get(key)
                            if val and str(val).strip():
                                code = str(val).strip()
                                name = status_mapping.get(code, code)
                                self._leopards_set_last_status(code, name)
                                status_found = True
                                break
                if not status_found:
                    # Fallback: packet exists in Leopards but no status field
                    self.leopards_last_status = 'Booked (Pickup Request not Send)'
                break

        def _fmt_activity(act):
            """Format a single activity dict for display."""
            if not isinstance(act, dict):
                return str(act)
            if act.get('_raw'):
                return act['_raw']
            # Status/activity style (RC, DV, etc.)
            status = act.get('status') or act.get('status_code') or act.get('Status') or ''
            status_name = status_mapping.get(status, status) if status else ''
            activity_date = (act.get('activity_date') or act.get('date') or act.get('ActivityDate') or
                            act.get('activityDate') or act.get('datetime') or act.get('created_at') or '')
            reason = act.get('reason') or act.get('Reason') or act.get('description') or act.get('remarks') or ''
            location = act.get('location') or act.get('Location') or act.get('city') or act.get('hub') or ''
            if status_name or status:
                parts = [status_name or status]
                if activity_date:
                    parts.append(str(activity_date))
                if location:
                    parts.append(location)
                if reason:
                    parts.append(reason)
                return ' — '.join(parts)
            # Packet metadata style (booked_packet_id, booking_date, etc.)
            labels = {
                'booked_packet_id': 'Packet ID',
                'booking_date': 'Booking Date',
                'track_number_short': 'CN',
                'booked_packet_weight': 'Weight (g)',
                'arival_dispatch_weight': 'Dispatch Weight',
                'consignment_status': 'Status',
                'current_status': 'Current Status',
                'origin_city': 'Origin',
                'destination_city': 'Destination',
            }
            ordered = ['booking_date', 'consignment_status', 'current_status', 'track_number_short',
                       'booked_packet_weight', 'origin_city', 'destination_city', 'booked_packet_id',
                       'arival_dispatch_weight']
            skip = {'api_key', 'api_password'}
            seen = set()
            items = []
            for k in ordered:
                if k in act and act[k] is not None and str(act[k]).strip() != '':
                    label = labels.get(k, k.replace('_', ' ').title())
                    items.append(f"{label}: {act[k]}")
                    seen.add(k)
            for k, v in act.items():
                if k not in skip and k not in seen and v is not None and str(v).strip() != '':
                    label = labels.get(k, k.replace('_', ' ').title())
                    items.append(f"{label}: {v}")
            return ' | '.join(items[:10]) if items else 'Tracking info received'

        lines = ['Leopards Tracking (from API):', '']
        if self.leopards_last_status:
            lines.append(f'Current Status: {self.leopards_last_status}')
        else:
            lines.append('For live status, click the Tracking button to open Leopards website.')
        lines.append('')
        activities_found = []
        raw_list = packet_list if isinstance(packet_list, list) else [packet_list]
        for pkt in raw_list:
            if isinstance(pkt, dict):
                sub = (pkt.get('activities') or pkt.get('history') or pkt.get('tracking_history') or
                       pkt.get('tracking_details') or pkt.get('scans'))
                if sub and isinstance(sub, list):
                    for act in sub:
                        activities_found.append(act if isinstance(act, dict) else {'_raw': str(act)})
                else:
                    activities_found.append(pkt)
            else:
                activities_found.append({'_raw': str(pkt)})
        for act in activities_found:
            line = _fmt_activity(act)
            if line:
                if ' | ' in line and ('Booking Date' in line or 'Packet ID' in line):
                    for part in line.split(' | '):
                        if part.strip():
                            lines.append(f'• {part.strip()}')
                else:
                    lines.append(f'• {line}')
        if not activities_found and packet_list:
            lines.append('• Tracking data received. Check Odoo logs for full response.')
        self.message_post(body='\n'.join(lines), message_type='notification')
        return True

    def action_clear_tracking_locally(self):
        """
        Remove tracking number from Odoo without calling Leopards API.
        Use when the cancel API times out or when you cancelled manually in Leopards Portal.
        """
        self.ensure_one()
        if not self.carrier_tracking_ref:
            raise UserError(_('No tracking number to clear.'))
        if self.delivery_type != 'leopards':
            raise UserError(_('This action is only for Leopards Courier deliveries.'))

        tracking_ref = self.carrier_tracking_ref
        self.carrier_tracking_ref = False
        self.message_post(
            body=_('Tracking number %s cleared locally (Leopards API was not called). '
                   'Cancel the shipment manually in Leopards Portal if needed.') % tracking_ref,
            message_type='notification',
        )
        return True
