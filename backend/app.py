"""
API del bot de Recaudación.

    python app.py            -> http://localhost:5050

Endpoints:
    POST /api/lotes                 (multipart, campo "archivo": .xlsx/.csv con cédulas)
    GET  /api/lotes                 lista de lotes
    GET  /api/lotes/<id>            estado, avance, resultados y log
    POST /api/lotes/<id>/cancelar
    GET  /api/lotes/<id>/zip        descarga el ZIP con las capturas y resumen.xlsx
    GET  /api/plantilla             Excel de ejemplo

Si existe ../frontend/dist/frontend/browser (ng build), también sirve la interfaz Angular.
Los lotes se procesan de uno en uno: el sistema solo admite una sesión por usuario.
No se guarda nada en el proyecto: cada lote usa una carpeta temporal del sistema, las
capturas se borran al armar el ZIP y los ZIP se borran al detener el servidor.
"""

import atexit
import io
import os
import queue
import shutil
import tempfile
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from openpyxl import Workbook

import login
import lote

BASE = Path(__file__).resolve().parent
FRONTEND = BASE.parent / "frontend" / "dist" / "frontend" / "browser"
EXTENSIONES = (".xlsx", ".xlsm", ".csv")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

LOTES = {}
CANDADO = threading.Lock()
COLA = queue.Queue()


class Lote:
    def __init__(self, nombre_archivo, cedulas):
        self.id = uuid.uuid4().hex[:12]
        self.carpeta = Path(tempfile.mkdtemp(prefix=f"recaudacion_{self.id}_"))
        self.archivo = nombre_archivo
        self.cedulas = cedulas
        self.creado = datetime.now().isoformat(timespec="seconds")
        self.estado = "en_cola"          # en_cola | procesando | listo | cancelado | error
        self.actual = 0
        self.resultados = []
        self.log = []
        self.error = ""
        self.cancelar = False
        self.zip = None

    def agregar_log(self, *partes):
        texto = " ".join(str(p) for p in partes)
        print(texto, flush=True)
        with CANDADO:
            self.log.extend(texto.split("\n"))
            del self.log[:-500]

    def como_dict(self, detalle=False):
        conteo = {"ok": 0, "sin_datos": 0, "error": 0}
        for r in self.resultados:
            conteo[r["estado"]] = conteo.get(r["estado"], 0) + 1
        datos = {
            "id": self.id, "archivo": self.archivo, "creado": self.creado, "estado": self.estado,
            "total": len(self.cedulas), "actual": self.actual, "conteo": conteo,
            "error": self.error, "zip": bool(self.zip),
        }
        if detalle:
            with CANDADO:
                datos["log"] = self.log[-200:]
            datos["resultados"] = [
                {k: r[k] for k in ("cedula", "estado", "valor", "total", "detalle")} for r in self.resultados
            ]
            datos["pendientes"] = self.cedulas[len(self.resultados):]
        return datos


def _trabajador():
    while True:
        lt = COLA.get()
        if lt.cancelar:
            lt.estado = "cancelado"
            continue
        lt.estado = "procesando"
        login.log = lt.agregar_log
        try:
            def progreso(i, _total, resultado):
                lt.actual = i
                lt.resultados.append(resultado)

            lote.procesar_lote(lt.cedulas, str(lt.carpeta / "capturas"), progreso, lambda: lt.cancelar)
            lt.estado = "cancelado" if lt.cancelar else "listo"
        except Exception as e:
            lt.estado = "error"
            lt.error = str(e).splitlines()[0] if str(e) else e.__class__.__name__
            lt.agregar_log(traceback.format_exc())
        finally:
            login.log = print
            if lt.resultados:
                lt.zip = lote.armar_zip(lt.resultados, str(lt.carpeta / f"resultados_{lt.id}.zip"))
            shutil.rmtree(lt.carpeta / "capturas", ignore_errors=True)
            if not lt.zip:
                shutil.rmtree(lt.carpeta, ignore_errors=True)


# Al detener el servidor se borran las carpetas temporales de todos los lotes.
@atexit.register
def _limpiar():
    for lt in LOTES.values():
        shutil.rmtree(lt.carpeta, ignore_errors=True)


threading.Thread(target=_trabajador, daemon=True).start()


def _buscar(id_lote):
    lt = LOTES.get(id_lote)
    if not lt:
        abort(404)
    return lt


@app.post("/api/lotes")
def crear_lote():
    if not login.USUARIO or not login.CLAVE:
        return jsonify(error="Faltan RECAUDACION_USUARIO / RECAUDACION_CLAVE en .env"), 500
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify(error="Adjunta un archivo Excel con las cédulas"), 400
    nombre = os.path.basename(archivo.filename)
    if not nombre.lower().endswith(EXTENSIONES):
        return jsonify(error="Formato no admitido: usa .xlsx o .csv"), 400

    descriptor, ruta = tempfile.mkstemp(prefix="recaudacion_subida_", suffix=Path(nombre).suffix.lower())
    os.close(descriptor)
    temporal = Path(ruta)
    archivo.save(temporal)
    try:
        cedulas = lote.leer_cedulas(str(temporal))
    except Exception as e:
        return jsonify(error=f"No se pudo leer el archivo: {e}"), 400
    finally:
        temporal.unlink(missing_ok=True)

    if not cedulas:
        return jsonify(error="No se encontraron cédulas en el archivo"), 400
    if len(cedulas) > lote.MAX_CEDULAS:
        return jsonify(error=f"El archivo tiene {len(cedulas)} cédulas; el máximo es {lote.MAX_CEDULAS}"), 400

    lt = Lote(nombre, cedulas)
    LOTES[lt.id] = lt
    COLA.put(lt)
    return jsonify(lt.como_dict(detalle=True)), 201


@app.get("/api/lotes")
def listar_lotes():
    return jsonify([lt.como_dict() for lt in sorted(LOTES.values(), key=lambda x: x.creado, reverse=True)])


@app.get("/api/lotes/<id_lote>")
def ver_lote(id_lote):
    return jsonify(_buscar(id_lote).como_dict(detalle=True))


@app.post("/api/lotes/<id_lote>/cancelar")
def cancelar_lote(id_lote):
    lt = _buscar(id_lote)
    lt.cancelar = True
    if lt.estado == "en_cola":
        lt.estado = "cancelado"
    return jsonify(lt.como_dict())


@app.get("/api/lotes/<id_lote>/zip")
def descargar_zip(id_lote):
    lt = _buscar(id_lote)
    if not lt.zip or not os.path.exists(lt.zip):
        return jsonify(error="El ZIP todavía no está listo"), 409
    base = Path(lt.archivo).stem
    return send_file(lt.zip, as_attachment=True, download_name=f"resultados_{base}.zip")


@app.get("/api/plantilla")
def plantilla():
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Cedulas"
    hoja.append(["Cedula"])
    hoja.append(["0102030405"])
    hoja.column_dimensions["A"].width = 18
    for fila in hoja.iter_rows(min_row=2):
        fila[0].number_format = "@"
    buffer = io.BytesIO()
    libro.save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="plantilla_cedulas.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# Interfaz Angular compilada (ng build)
@app.get("/")
@app.get("/<path:ruta>")
def interfaz(ruta="index.html"):
    if ruta.startswith("api/"):
        abort(404)
    if not FRONTEND.exists():
        return jsonify(servicio="Recaudación bot", api="/api",
                       aviso="Compila el frontend: cd frontend && npm run build"), 200
    if (FRONTEND / ruta).is_file():
        return send_from_directory(FRONTEND, ruta)
    return send_from_directory(FRONTEND, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5050")), threaded=True)
