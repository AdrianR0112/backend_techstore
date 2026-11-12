from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import os
import re
import xml.etree.ElementTree as ET

import certifi
from flask import Flask, Response, jsonify, request
import pytds


app = Flask(__name__)

IVA_GENERAL = Decimal("0.15")
MAX_CONCURRENCY_RETRIES = 5


class InventoryConflict(Exception):
    def __init__(self, message, product=None, status_code=409):
        super().__init__(message)
        self.message = message
        self.product = product
        self.status_code = status_code


def get_connection():
    """Crea una conexion cifrada a Azure SQL usando solo variables de entorno."""
    return pytds.connect(
        server=os.environ["DB_SERVER"],
        database=os.environ["DB_DATABASE"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        port=1433,
        row_strategy=pytds.namedtuple_row_strategy,
        cafile=certifi.where(),
        validate_host=True,
        login_timeout=15,
        timeout=30,
    )


@contextmanager
def open_database():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        yield connection, cursor
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def product_to_dict(row):
    return {
        "id": int(row.id),
        "nombre": row.names,
        "precio": float(row.price),
        "stock": int(row.stock),
        "version": int(row.version),
        "image_url": row.image_url or "",
    }


def is_valid_ecuadorian_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{10}(?:\d{3})?", value):
        return False

    digits = [int(character) for character in value]
    province = int(value[:2])
    if not 1 <= province <= 24:
        return False

    if len(value) == 10:
        return is_valid_cedula_digits(digits)

    establishment = int(value[-3:])
    if establishment == 0:
        return False

    taxpayer_type = digits[2]
    if taxpayer_type < 6:
        return is_valid_cedula_digits(digits[:10])
    if taxpayer_type == 9:
        return is_valid_modulo_eleven(
            digits,
            [4, 3, 2, 7, 6, 5, 4, 3, 2],
            verifier_index=9,
        )
    if taxpayer_type == 6:
        return is_valid_modulo_eleven(
            digits,
            [3, 2, 7, 6, 5, 4, 3, 2],
            verifier_index=8,
        )
    return False


def is_valid_cedula_digits(digits):
    if len(digits) < 10 or digits[2] >= 6:
        return False

    total = 0
    for index, digit in enumerate(digits[:9]):
        value = digit * (2 if index % 2 == 0 else 1)
        total += value - 9 if value > 9 else value

    verifier = (10 - total % 10) % 10
    return digits[9] == verifier


def is_valid_modulo_eleven(digits, coefficients, verifier_index):
    total = sum(
        digits[index] * coefficient
        for index, coefficient in enumerate(coefficients)
    )
    remainder = total % 11
    verifier = 0 if remainder == 0 else 11 - remainder
    return verifier < 10 and digits[verifier_index] == verifier


def validate_invoice(data):
    errors = []
    if not isinstance(data, dict) or not data:
        return [{"campo": "body", "error": "El JSON esta vacio o no es valido."}]

    number = data.get("numero")
    if not isinstance(number, str) or not number.strip() or len(number.strip()) > 50:
        errors.append({
            "campo": "numero",
            "error": "El numero de factura es obligatorio y admite hasta 50 caracteres.",
        })

    invoice_date = data.get("fecha")
    try:
        date.fromisoformat(invoice_date)
    except (TypeError, ValueError):
        errors.append({
            "campo": "fecha",
            "error": "La fecha debe tener el formato AAAA-MM-DD.",
        })

    client = data.get("cliente")
    if not isinstance(client, dict):
        errors.append({"campo": "cliente", "error": "El cliente debe ser un objeto."})
    else:
        client_name = client.get("nombre")
        if (
            not isinstance(client_name, str)
            or not client_name.strip()
            or len(client_name.strip()) > 50
            or not re.fullmatch(
                r"[a-zA-ZáéíóúÁÉÍÓÚñÑüÜ]+(?:[- '][a-zA-ZáéíóúÁÉÍÓÚñÑüÜ]+)*",
                client_name.strip(),
            )
        ):
            errors.append({
                "campo": "cliente.nombre",
                "error": "El nombre es obligatorio, admite hasta 50 caracteres y solo puede contener letras.",
            })
        client_id = client.get("cedula_ruc")
        if not is_valid_ecuadorian_id(client_id.strip() if isinstance(client_id, str) else client_id):
            errors.append({
                "campo": "cliente.cedula_ruc",
                "error": "La cedula o RUC debe ser un numero ecuatoriano valido de 10 o 13 digitos.",
            })

    details = data.get("detalles")
    if not isinstance(details, list) or not details:
        errors.append({
            "campo": "detalles",
            "error": "Debe haber al menos un producto en la factura.",
        })
        return errors

    product_ids = set()
    for index, detail in enumerate(details):
        field = f"detalles[{index}]"
        if not isinstance(detail, dict):
            errors.append({"campo": field, "error": "El detalle debe ser un objeto."})
            continue

        product_id = detail.get("id")
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            errors.append({"campo": f"{field}.id", "error": "El id debe ser un entero positivo."})
        elif product_id in product_ids:
            errors.append({"campo": f"{field}.id", "error": "El producto esta repetido en la factura."})
        else:
            product_ids.add(product_id)

        quantity = detail.get("cantidad")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            errors.append({
                "campo": f"{field}.cantidad",
                "error": "La cantidad debe ser un entero positivo.",
            })

        version = detail.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            errors.append({
                "campo": f"{field}.version",
                "error": "La version debe ser un entero positivo.",
            })

    return errors


def row_to_inventory_product(row, quantity):
    return {
        "id": int(row.id),
        "nombre": row.names,
        "precio": Decimal(str(row.price)),
        "cantidad": quantity,
        "stock": int(row.stock),
        "version": int(row.version),
    }


def product_to_conflict_dict(row):
    return {
        "id": int(row.id),
        "nombre": row.names,
        "precio": float(row.price),
        "stock": int(row.stock),
        "version": int(row.version),
    }


def discount_stock(cursor, details):
    """Descuenta el carrito y reintenta si otra compra cambia la version."""
    invoiced_products = []

    # Un orden estable reduce la posibilidad de interbloqueos entre facturas
    # que contienen varios productos.
    for detail in sorted(details, key=lambda item: item["id"]):
        product_id = detail["id"]
        quantity = detail["cantidad"]
        attempt_version = detail["version"]
        last_row = None

        for _ in range(MAX_CONCURRENCY_RETRIES):
            cursor.execute(
                """
                SELECT id, names, price, stock, version
                FROM dbo.Products
                WHERE id = %s
                """,
                (product_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise InventoryConflict(
                    "Producto no encontrado.",
                    {"id": product_id},
                    status_code=404,
                )

            last_row = row
            if int(row.stock) < quantity:
                raise InventoryConflict(
                    "No existe stock suficiente para completar la factura.",
                    product_to_conflict_dict(row),
                )

            # Si otra factura ya incremento la version, se adopta la version
            # actual y se intenta el descuento sobre el stock mas reciente.
            if int(row.version) != attempt_version:
                attempt_version = int(row.version)

            cursor.execute(
                """
                UPDATE dbo.Products
                SET stock = stock - %s,
                    version = version + 1
                WHERE id = %s
                  AND version = %s
                  AND stock >= %s
                """,
                (quantity, product_id, attempt_version, quantity),
            )

            if cursor.rowcount == 1:
                invoiced_products.append(
                    row_to_inventory_product(row, quantity)
                )
                break
        else:
            raise InventoryConflict(
                "El inventario cambio varias veces durante la compra. Intenta nuevamente.",
                product_to_conflict_dict(last_row) if last_row else {"id": product_id},
            )

    return invoiced_products


def build_invoice_xml(data, products):
    subtotal = Decimal("0.00")
    invoice = ET.Element("Factura")
    header = ET.SubElement(invoice, "Cabecera")
    ET.SubElement(header, "Numero").text = data["numero"].strip()
    ET.SubElement(header, "Fecha").text = data["fecha"]

    client = ET.SubElement(invoice, "Cliente")
    ET.SubElement(client, "Nombre").text = data["cliente"]["nombre"].strip()
    ET.SubElement(client, "CedulaRUC").text = data["cliente"]["cedula_ruc"].strip()

    detail_node = ET.SubElement(invoice, "Detalle")
    for product in products:
        product_subtotal = product["precio"] * product["cantidad"]
        subtotal += product_subtotal
        product_node = ET.SubElement(detail_node, "Producto")
        ET.SubElement(product_node, "Id").text = str(product["id"])
        ET.SubElement(product_node, "Nombre").text = product["nombre"]
        ET.SubElement(product_node, "Cantidad").text = str(product["cantidad"])
        ET.SubElement(product_node, "PrecioUnitario").text = f"{product['precio']:.2f}"
        ET.SubElement(product_node, "Subtotal").text = f"{product_subtotal:.2f}"

    tax = subtotal * IVA_GENERAL
    total = subtotal + tax
    totals = ET.SubElement(invoice, "Totales")
    ET.SubElement(totals, "Subtotal").text = f"{subtotal:.2f}"
    ET.SubElement(totals, "IVA").text = f"{tax:.2f}"
    ET.SubElement(totals, "Total").text = f"{total:.2f}"
    ET.SubElement(invoice, "Estado").text = "Generada"

    ET.indent(invoice, space="  ")
    return ET.tostring(invoice, encoding="utf-8", xml_declaration=True)


def database_error():
    app.logger.exception("Error al acceder a Azure SQL")
    return jsonify({
        "error": "Error interno",
        "mensaje": "No se pudo completar la operacion en la base de datos.",
    }), 500


@app.get("/")
def home():
    return jsonify({
        "servicio": "TechStore Facturacion",
        "estado": "disponible",
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/test-db")
def test_database():
    try:
        with open_database() as (_, cursor):
            cursor.execute("SELECT DB_NAME() AS database_name")
            database_name = cursor.fetchone().database_name
        return jsonify({"status": "ok", "database": database_name})
    except Exception:
        return database_error()


@app.get("/api/productos")
def get_products():
    try:
        with open_database() as (_, cursor):
            cursor.execute(
                """
                SELECT id, names, price, stock, version, image_url
                FROM dbo.Products
                ORDER BY id
                """
            )
            products = [product_to_dict(row) for row in cursor.fetchall()]
        return jsonify(products)
    except Exception:
        return database_error()


@app.post("/api/facturas")
def create_invoice():
    data = request.get_json(silent=True)
    errors = validate_invoice(data)
    if errors:
        return jsonify({
            "error": "Datos invalidos",
            "mensaje": "Revisa los datos enviados.",
            "errores": errors,
        }), 400

    try:
        with open_database() as (connection, cursor):
            products = discount_stock(cursor, data["detalles"])
            xml = build_invoice_xml(data, products)
            connection.commit()
    except InventoryConflict as conflict:
        return jsonify({
            "error": "Conflicto de inventario",
            "mensaje": conflict.message,
            "producto": conflict.product,
        }), conflict.status_code
    except Exception:
        return database_error()

    return Response(xml, status=200, content_type="application/xml; charset=utf-8")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        threaded=True,
    )
