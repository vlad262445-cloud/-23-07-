from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestUpddLineCorrection(TransactionCase):
    """ИИ иногда неверно распознаёт данные УПД - до этой фичи исправить их
    было негде. purchase.updd.line не имеет своего mail.thread, поэтому
    правки логируются на родителя (заказ или приёмку без заказа)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'Test УПД Vendor'})
        cls.order = cls.env['purchase.order'].create({'partner_id': partner.id})

        picking_type = cls.env['stock.picking.type'].search(
            [('code', '=', 'incoming')], limit=1)
        cls.picking = cls.env['stock.picking'].create({
            'partner_id': partner.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })

    def test_correction_logged_on_parent_order_chatter(self):
        line = self.env['purchase.updd.line'].create({
            'purchase_order_id': self.order.id,
            'updd_number': 'УПД-1',
            'seller_inn': '1234567890',
        })
        line.write({'updd_number': 'УПД-1-испр', 'seller_inn': '0987654321'})

        messages = self.order.message_ids.mapped('body')
        self.assertTrue(
            any('Исправлены данные УПД' in body and 'УПД-1' in body and 'УПД-1-испр' in body
                for body in messages),
            'исправление номера/ИНН должно попасть в чат заказа')

    def test_correction_logged_on_parent_picking_when_no_order(self):
        line = self.env['purchase.updd.line'].create({
            'picking_id': self.picking.id,
            'seller_name': 'ООО Ромашка',
        })
        line.write({'seller_name': 'ООО Ромашка-исправлено'})

        messages = self.picking.message_ids.mapped('body')
        self.assertTrue(
            any('Исправлены данные УПД' in body and 'Ромашка-исправлено' in body
                for body in messages),
            'исправление УПД без заказа должно попасть в чат приёмки, а не потеряться')

    def test_no_diff_logged_when_value_unchanged(self):
        line = self.env['purchase.updd.line'].create({
            'purchase_order_id': self.order.id,
            'updd_number': 'УПД-2',
        })
        message_count_before = len(self.order.message_ids)
        line.write({'updd_number': 'УПД-2'})

        self.assertEqual(len(self.order.message_ids), message_count_before)
