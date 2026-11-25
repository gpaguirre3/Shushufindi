"""
Consulta por lote: lee las cédulas de un Excel (o CSV), las consulta todas con una
sola sesión del sistema de Recaudación y empaqueta las capturas en un ZIP junto con
un resumen.xlsx.
"""

import csv
import os
import re
import tempfile
import unicodedata
import zipfile
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import sync_playwright

import login

MAX_CEDULAS = int(os.getenv("RECAUDACION_MAX_CEDULAS", "500"))
ERRORES_PARA_RELOGIN = 2   # errores seguidos antes de volver a iniciar sesión

ENCABEZADOS = ("cedula", "identificacion", "ruc", "ci", "documento", "nro identificacion")


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto).strip().lower()


# Convierte el valor de una celda en cédula. Excel guarda las cédulas como número y
# pierde el 0 inicial (provincias 01-09): se repone si quedan 9 (cédula) o 12 (RUC) dígitos.
def limpiar_cedula(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = re.sub(r"[^0-9A-Za-z]", "", str(valor).strip())
    if texto.isdigit():
        if len(texto) == 9:
            texto = texto.zfill(10)
        elif len(texto) == 12:
            texto = texto.zfill(13)
    return texto


def _filas(ruta):
    if ruta.lower().endswith(".csv"):
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            muestra = f.read(4096)
            f.seek(0)
            try:
                dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
            except csv.Error:
                dialecto = csv.excel
            return [fila for fila in csv.reader(f, dialecto)]
    libro = load_workbook(ruta, read_only=True, data_only=True)
    try:
        hoja = libro.worksheets[0]
        return [list(fila) for fila in hoja.iter_rows(values_only=True)]
    finally:
        libro.close()


# Devuelve la lista de cédulas (sin repetir, en el orden del archivo).
# Usa la columna cuyo encabezado sea "Cédula"/"Identificación"/"RUC"...; si no hay, la primera.
def leer_cedulas(ruta):
    filas = [f for f in _filas(ruta) if f and any(c not in (None, "") for c in f)]
    if not filas:
        return []

    columna, inicio = 0, 0
    for i, fila in enumerate(filas[:10]):
        for j, celda in enumerate(fila):
            if _normalizar(celda) in ENCABEZADOS:
                columna, inicio = j, i + 1
                break
        else:
            continue
        break
    else:
        # Sin encabezado reconocido: si la primera fila no parece una cédula, es un título.
        if not limpiar_cedula(filas[0][0]).isdigit():
            inicio = 1

    cedulas, vistas = [], set()
    for fila in filas[inicio:]:
        cedula = limpiar_cedula(fila[columna] if columna < len(fila) else None)
        if len(cedula) >= 5 and cedula not in vistas:
            vistas.add(cedula)
            cedulas.append(cedula)
    return cedulas


def _valor_total(texto):
    numeros = re.findall(r"-?[\d.,]*\d", texto or "")
    return numeros[-1] if numeros else ""


def _captura_error(pagina, carpeta, cedula):
    archivo = os.path.join(carpeta, f"error_{cedula}.png")
    try:
        pagina.screenshot(path=archivo, full_page=True)
        return archivo
    except Exception:
        return ""


# Consulta todas las cédulas. `progreso(i, total, resultado)` se llama tras cada una;
# `cancelado()` permite detener el lote entre cédulas.
def procesar_lote(cedulas, carpeta, progreso=None, cancelado=lambda: False):
    os.makedirs(carpeta, exist_ok=True)
    resultados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=login.HEADLESS)
        context, deudores, errores_seguidos = None, None, 0

        def abrir_sesion():
            nonlocal context, deudores
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            context = browser.new_context()
            deudores = login.preparar_sesion(context)

        try:
            abrir_sesion()
            for i, cedula in enumerate(cedulas, 1):
                if cancelado():
                    login.log("Lote cancelado por el usuario")
                    break
                login.log(f"[{i}/{len(cedulas)}] Consultando {cedula}")
                resultado = {"cedula": cedula, "estado": "ok", "total": "", "valor": "", "archivo": "", "detalle": ""}
                try:
                    if errores_seguidos >= ERRORES_PARA_RELOGIN:
                        login.log("Varios errores seguidos: se vuelve a iniciar sesión")
                        abrir_sesion()
                        errores_seguidos = 0
                    marco = login.buscar_cedula(deudores, cedula)
                    if not marco.evaluate(login.JS_RANGO):
                        resultado.update(estado="sin_datos", detalle="La búsqueda no devolvió registros")
                        archivo = os.path.join(carpeta, f"sin_datos_{cedula}.png")
                        deudores.screenshot(path=archivo, full_page=True)
                        resultado["archivo"] = archivo
                    else:
                        total, archivo = login.capturar_total(marco, cedula, carpeta)
                        resultado.update(total=total, valor=_valor_total(total), archivo=archivo)
                    errores_seguidos = 0
                except Exception as e:
                    errores_seguidos += 1
                    resultado.update(estado="error", detalle=str(e).splitlines()[0][:300])
                    resultado["archivo"] = _captura_error(deudores, carpeta, cedula) if deudores else ""
                    login.log(f"Error con {cedula}: {resultado['detalle']}")
                resultados.append(resultado)
                if progreso:
                    progreso(i, len(cedulas), resultado)
        finally:
            browser.close()
    return resultados


def _escribir_resumen(resultados, ruta):
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Resumen"
    hoja.append(["Cédula", "Estado", "Valor", "Total Acumulado", "Captura", "Detalle"])
    for celda in hoja[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E78")
    etiquetas = {"ok": "Consultada", "sin_datos": "Sin registros", "error": "Error"}
    for r in resultados:
        hoja.append([
            r["cedula"], etiquetas.get(r["estado"], r["estado"]), r["valor"], r["total"],
            os.path.basename(r["archivo"]) if r["archivo"] else "", r["detalle"],
        ])
    for col, ancho in zip("ABCDEF", (16, 14, 16, 40, 28, 60)):
        hoja.column_dimensions[col].width = ancho
    hoja.freeze_panes = "A2"
    libro.save(ruta)


# Crea el ZIP con resumen.xlsx y la carpeta capturas/.
def armar_zip(resultados, ruta_zip):
    with tempfile.TemporaryDirectory() as tmp:
        resumen = os.path.join(tmp, "resumen.xlsx")
        _escribir_resumen(resultados, resumen)
        with zipfile.ZipFile(ruta_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(resumen, "resumen.xlsx")
            for r in resultados:
                if r["archivo"] and os.path.exists(r["archivo"]):
                    z.write(r["archivo"], f"capturas/{os.path.basename(r['archivo'])}")
    return ruta_zip


# Uso desde la línea de comandos (python login.py cedulas.xlsx).
def procesar_archivo(ruta, carpeta_salida="."):
    cedulas = leer_cedulas(ruta)
    if not cedulas:
        raise SystemExit(f"No se encontraron cédulas en {ruta}")
    login.log(f"{len(cedulas)} cédulas a consultar")
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    with tempfile.TemporaryDirectory(prefix="recaudacion_") as carpeta:  # capturas temporales
        resultados = procesar_lote(cedulas, carpeta)
        return armar_zip(resultados, os.path.join(carpeta_salida, f"resultados_{marca}.zip"))
