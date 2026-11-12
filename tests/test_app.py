from collections import namedtuple
from decimal import Decimal
import unittest

from app import (
    InventoryConflict,
    build_invoice_xml,
    discount_stock,
    is_valid_ecuadorian_id,
    validate_invoice,
)


ProductRow = namedtuple("ProductRow", "id names price stock version image_url", defaults=[""])


class FakeCursor:
    def __init__(self, initial, current=None, update_count=1, update_counts=None):
        self.initial = initial
        self.current = current or initial
        self.update_count = update_count
        self.update_counts = list(update_counts or [])
        self.rowcount = -1
        self._fetches = 0

    def execute(self, sql, params):
        if "UPDATE" in sql:
            self.rowcount = (
                self.update_counts.pop(0)
                if self.update_counts
                else self.update_count
            )

    def fetchone(self):
        self._fetches += 1
        return self.initial if self._fetches == 1 else self.current


def valid_invoice():
    return {
        "numero": "F-001",
        "fecha": "2026-11-11",
        "cliente": {"nombre": "Ana", "cedula_ruc": "1712345675"},
        "detalles": [{"id": 1, "cantidad": 2, "version": 1}],
    }


class InvoiceTests(unittest.TestCase):
    def test_accepts_valid_cedula_and_natural_ruc(self):
        self.assertTrue(is_valid_ecuadorian_id("1712345675"))
        self.assertTrue(is_valid_ecuadorian_id("1712345675001"))
        self.assertTrue(is_valid_ecuadorian_id("1791234561001"))
        self.assertTrue(is_valid_ecuadorian_id("1761234510001"))

    def test_rejects_invalid_client_name_and_id(self):
        invoice = valid_invoice()
        invoice["cliente"] = {
            "nombre": "A" * 51,
            "cedula_ruc": "1712345674",
        }

        errors = validate_invoice(invoice)

        fields = {error["campo"] for error in errors}
        self.assertIn("cliente.nombre", fields)
        self.assertIn("cliente.cedula_ruc", fields)

    def test_rejects_digits_in_client_name(self):
        invoice = valid_invoice()
        invoice["cliente"]["nombre"] = "Ana123"

        errors = validate_invoice(invoice)

        self.assertTrue(
            any(error["campo"] == "cliente.nombre" for error in errors)
        )

    def test_validation_requires_version(self):
        invoice = valid_invoice()
        del invoice["detalles"][0]["version"]

        errors = validate_invoice(invoice)

        self.assertTrue(any(error["campo"].endswith(".version") for error in errors))

    def test_discount_uses_expected_version_and_stock(self):
        row = ProductRow(1, "Agua", Decimal("0.25"), 10, 1, "")
        cursor = FakeCursor(row)

        products = discount_stock(cursor, valid_invoice()["detalles"])

        self.assertEqual(products[0]["cantidad"], 2)
        self.assertEqual(products[0]["precio"], Decimal("0.25"))

    def test_stale_version_retries_when_stock_is_still_available(self):
        old = ProductRow(1, "Agua", Decimal("0.25"), 10, 1, "")
        current = ProductRow(1, "Agua", Decimal("0.25"), 5, 2, "")
        cursor = FakeCursor(old, current=current, update_counts=[0, 1])
        details = [{"id": 1, "cantidad": 5, "version": 1}]

        products = discount_stock(cursor, details)

        self.assertEqual(products[0]["stock"], 5)
        self.assertEqual(products[0]["version"], 2)

    def test_rejects_purchase_only_when_current_stock_is_insufficient(self):
        current = ProductRow(1, "Agua", Decimal("0.25"), 1, 2, "")
        cursor = FakeCursor(current)

        with self.assertRaises(InventoryConflict) as raised:
            discount_stock(cursor, valid_invoice()["detalles"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.product["stock"], 1)

    def test_xml_uses_database_price(self):
        xml = build_invoice_xml(
            valid_invoice(),
            [{
                "id": 1,
                "nombre": "Agua",
                "precio": Decimal("0.25"),
                "cantidad": 2,
                "stock": 10,
                "version": 1,
            }],
        ).decode("utf-8")

        self.assertIn("<Subtotal>0.50</Subtotal>", xml)
        self.assertIn("<Total>0.58</Total>", xml)


if __name__ == "__main__":
    unittest.main()
