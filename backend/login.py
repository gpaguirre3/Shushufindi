import os
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

URL = os.getenv("RECAUDACION_URL", "http://190.152.149.188/recaudacion/ingreso/")
USUARIO = os.getenv("RECAUDACION_USUARIO")
CLAVE = os.getenv("RECAUDACION_CLAVE")
PERIODO = os.getenv("RECAUDACION_PERIODO", "2026")
ENTIDAD_OPCION = int(os.getenv("RECAUDACION_ENTIDAD_OPCION", "7"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
ESPERA_LOGIN = int(os.getenv("RECAUDACION_ESPERA_LOGIN", "5"))        # segundos antes de pulsar Login
ESPERA_DEUDORES = int(os.getenv("RECAUDACION_ESPERA_DEUDORES", "30"))  # segundos que tarda en cargar Deudores

# Función usada para los mensajes; el servidor web la reemplaza para mostrar el avance.
log = print

JS_RANGO = r"() => (document.body.innerText.match(/\[\s*\d+\s*a\s*\d+\s*de\s*\d+\s*\]/) || [''])[0]"

# Marca la tabla actual; si tras la búsqueda la marca desaparece, la tabla ya se recargó.
JS_MARCAR = r"""() => {
    window.__rcMarca = true;
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n, el = null;
    while ((n = w.nextNode())) {
        if (/\[\s*\d+\s*a\s*\d+\s*de\s*\d+\s*\]/.test(n.textContent)) { el = n.parentElement; break; }
    }
    (el || document.body).setAttribute('data-rc-marca', '1');
}"""
JS_CAMBIO = r"() => !window.__rcMarca || !document.querySelector('[data-rc-marca]')"


# Devuelve el texto visible de un frame (o "" si no se puede leer).
def texto_frame(frame):
    try:
        return frame.locator("body").inner_text(timeout=1000)
    except Exception:
        return ""


# Espera y devuelve el primer elemento visible con ese texto exacto, en la página o en sus iframes.
def buscar_en_frames(page, texto, timeout=20):
    for _ in range(timeout * 2):
        for frame in page.frames:
            loc = frame.get_by_text(texto, exact=True)
            for i in range(loc.count()):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        page.wait_for_timeout(500)
    raise RuntimeError(f"No se encontró '{texto}' en la pantalla")


# Inicia sesión: Entidad, Período, Usuario y Clave, pulsa Login y acepta si la sesión ya estaba abierta.
def login(page):
    page.goto(URL, wait_until="networkidle")

    # Cada campo se elige y se SALE con Tab: la página envía sus validaciones al servidor
    # (sin el onblur del período, el servidor rechaza el login).
    empresa = page.locator("#id_sc_field_cmb_empresa")
    empresa.focus()
    empresa.select_option(index=ENTIDAD_OPCION)
    page.wait_for_load_state("networkidle")
    empresa.press("Tab")
    page.wait_for_load_state("networkidle")

    periodo = page.locator("#id_sc_field_cmb_periodo")
    page.wait_for_function(
        "t => [...document.querySelectorAll('#id_sc_field_cmb_periodo option')].some(o => o.text.trim() === t)",
        arg=PERIODO, timeout=15000,
    )
    periodo.focus()
    periodo.select_option(label=PERIODO)
    page.wait_for_load_state("networkidle")
    periodo.press("Tab")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)

    for selector, valor in (("#id_sc_field_login", USUARIO), ("#id_sc_field_pswd", CLAVE)):
        campo = page.locator(selector)
        campo.click()
        campo.fill("")
        campo.press_sequentially(valor, delay=120)
        campo.press("Tab")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)

    if ESPERA_LOGIN:
        log(f"Esperando {ESPERA_LOGIN} s antes de presionar Login...")
        page.wait_for_timeout(ESPERA_LOGIN * 1000)
    page.click("#sub_form_b")

    # Éxito = "Bienvenido". Si la sesión ya estaba abierta, sale
    # "El usuario X ya está conectado. ¿Desea continuar?" -> Aceptar.
    for _ in range(60):
        for frame in page.frames:
            texto = texto_frame(frame)
            if "Bienvenido" in texto:
                page.wait_for_load_state("networkidle")
                log("Login OK")
                return
            if "ya está conectado" in texto:
                boton = frame.locator("input[value='Aceptar'], button:text-is('Aceptar'), a:text-is('Aceptar')").filter(visible=True)
                if boton.count():
                    log("Sesión ya abierta en otro lado: se pulsa 'Aceptar'")
                    boton.first.click()
                    page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
    page.screenshot(path="login_error.png", full_page=True)
    raise RuntimeError("No se pudo iniciar sesión. Revisa login_error.png")


# Abre Consultas > Visualizar > Deudores y espera a que cargue el informe. Devuelve su página.
def abrir_deudores(page, context):
    # Consultas -> pestaña Visualizar
    buscar_en_frames(page, "Consultas").click()
    buscar_en_frames(page, "Actualización - CONSULTAS")
    buscar_en_frames(page, "Visualizar").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    # Imagen a la derecha de la etiqueta "Deudores"
    paginas_antes = len(context.pages)
    etiqueta = buscar_en_frames(page, "Deudores")
    etiqueta.locator("xpath=ancestor::td[1]/following-sibling::td[1]//img").first.click()
    log(f"Abriendo Deudores, esperando {ESPERA_DEUDORES} s a que cargue la base...")
    page.wait_for_timeout(ESPERA_DEUDORES * 1000)

    destino = context.pages[-1] if len(context.pages) > paginas_antes else page
    destino.wait_for_load_state("networkidle")
    return destino


# Escribe la cédula en 'Búsqueda rápida' y pulsa la lupa. Devuelve el frame del informe.
def buscar_cedula(page, cedula):
    for _ in range(40):
        for frame in page.frames:
            caja = frame.locator("input[placeholder*='squeda r']")  # "Búsqueda rápida"
            if caja.count() and caja.first.is_visible():
                caja, marco = caja.first, frame
                break
        else:
            page.wait_for_timeout(500)
            continue
        break
    else:
        raise RuntimeError("No se encontró el buscador 'Búsqueda rápida'")

    rango_antes = marco.evaluate(JS_RANGO)
    marco.evaluate(JS_MARCAR)
    caja.click()
    caja.fill("")
    caja.press_sequentially(cedula, delay=80)
    marco.locator("[id^='SC_fast_search_submit']:visible").first.click()  # lupa

    # Esperar a que la tabla se actualice: cambia el "[x a y de z]" o se reemplaza la tabla
    # (dos cédulas seguidas pueden dar el mismo "[1 a 1 de 1]").
    for _ in range(60):
        try:
            if marco.evaluate(JS_RANGO) != rango_antes or marco.evaluate(JS_CAMBIO):
                break
        except Exception:
            pass  # el frame se está recargando
        page.wait_for_timeout(500)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    log(f"Cédula {cedula}: {marco.evaluate(JS_RANGO)}")
    return marco


# Va a la última página y guarda la captura del recuadro del informe con el Total Acumulado.
# Devuelve (texto del total, ruta de la captura).
def capturar_total(marco, cedula, carpeta="."):
    for _ in range(5):
        if "Total Acumulado" in texto_frame(marco):
            break
        marco.locator("#last_bot").click()  # botón ⏭ última página
        marco.page.wait_for_load_state("networkidle")
        marco.page.wait_for_timeout(3000)
    else:
        raise RuntimeError("No apareció 'Total Acumulado' en la última página")

    # Marcar el recuadro más pequeño que contiene título, total y barra de páginas
    marco.evaluate(r"""() => {
        document.querySelectorAll('[data-captura]').forEach(e => e.removeAttribute('data-captura'));
        let mejor = null;
        for (const el of document.querySelectorAll('body *')) {
            const t = el.innerText || '';
            if (el.offsetParent === null || !/Informe de - Deudores/.test(t) || !/Total Acumulado/.test(t) || !/de\s*\d+\s*\]/.test(t)) continue;
            const r = el.getBoundingClientRect();
            if (!mejor || r.width * r.height <= mejor.a) mejor = { el, a: r.width * r.height };
        }
        if (mejor) mejor.el.setAttribute('data-captura', 'informe');
    }""")
    total = " ".join(marco.locator("tr", has_text="Total Acumulado").last.inner_text().split())
    archivo = os.path.join(carpeta, f"total_{cedula}.png")
    marco.locator("[data-captura=informe]").screenshot(path=archivo)
    log(f"{total}  ->  {archivo}")
    return total, archivo


# Login + Consultas > Deudores. Devuelve la página del informe lista para buscar.
def preparar_sesion(context):
    page = context.new_page()
    login(page)
    return abrir_deudores(page, context)


# Uso:
#   python login.py <cédula>          -> total_<cédula>.png
#   python login.py cedulas.xlsx      -> resultados_<fecha>.zip con todas las capturas
def main():
    if not USUARIO or not CLAVE:
        sys.exit("Faltan RECAUDACION_USUARIO / RECAUDACION_CLAVE en .env")
    if len(sys.argv) < 2:
        sys.exit("Uso: python login.py <cédula | archivo.xlsx>")
    argumento = sys.argv[1].strip()

    if argumento.lower().endswith((".xlsx", ".xlsm", ".csv")):
        import lote
        zip_salida = lote.procesar_archivo(argumento)
        log(f"Resultados: {zip_salida}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        deudores = preparar_sesion(context)
        marco = buscar_cedula(deudores, argumento)
        capturar_total(marco, argumento)
        browser.close()


if __name__ == "__main__":
    main()
