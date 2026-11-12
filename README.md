# backend_techstore

Backend Flask para el carrito TechStore. Consulta los productos en Azure SQL,
genera la factura XML y descuenta el stock de forma atomica con control de
concurrencia optimista mediante la columna `version`.

## Variables de entorno

Configura estas variables localmente y en Render. Nunca subas la contrasena al
repositorio.

```text
DB_SERVER=servidor.database.windows.net
DB_DATABASE=nombre_base
DB_USER=usuario
DB_PASS=contrasena
```

## Ejecutar

```bash
pip install -r requirements.txt
flask --app app run
```

## Endpoints

- `GET /health`: estado del servicio.
- `GET /test-db`: verifica la conexion con Azure SQL.
- `GET /api/productos`: catalogo, stock, version e imagen.
- `POST /api/facturas`: valida el carrito, descuenta stock y devuelve XML.

Cada detalle enviado a `/api/facturas` debe contener `id`, `cantidad` y la
`version` obtenida desde `/api/productos`. Un stock insuficiente o una version
obsoleta devuelve HTTP `409` y revierte toda la transaccion.

La migracion aplicada a Azure para agregar las imagenes queda documentada en
`migrations/001_add_product_image_url.sql`.

## Render

El archivo `render.yaml` deja preparado el servicio `backend-techstore`. Luego
de conectarlo al repositorio, agrega las cuatro variables de Azure en el panel
de Render. La URL prevista es `https://backend-techstore.onrender.com` si ese
nombre esta disponible.
