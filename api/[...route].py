"""Punto de entrada para Vercel.

Vercel exige que el archivo se llame `handler` (minuscula) y herede de
`BaseHTTPRequestHandler`; el nombre del archivo (`[...route].py`) es la
convencion de Vercel para "atrapar" cualquier sub-ruta bajo `/api/*` con una
sola funcion, en vez de necesitar un archivo por endpoint.

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
