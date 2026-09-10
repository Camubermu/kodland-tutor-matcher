"""Punto de entrada para Vercel.

Vercel exige que el archivo se llame `handler` (minuscula) y herede de
`BaseHTTPRequestHandler`. Este es el UNICO archivo de funcion: todas las
rutas bajo `/api/*` llegan aca via el rewrite definido en `vercel.json`
(que pasa el sub-path real como query string `?route=...`); `server.py`
sabe reconstruir la ruta logica a partir de eso (ver `_route_and_query`
en `Handler`), asi que el mismo codigo sirve para el modo local (donde
`self.path` ya es la ruta real) y para el desplegado.

Toda la logica real vive en `server.py` (el mismo modulo que usa el server
local): esto solo la reexpone con el nombre que Vercel espera, para no
duplicar nada entre el modo local y el desplegado.
"""

import sys
from pathlib import Path

# server.py y sus dependencias (groups.py, kodland_api.py, matching.py) viven
# en la raiz del repo, un nivel arriba de /api.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import Handler as handler  # noqa: E402,F401
