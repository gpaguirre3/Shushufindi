# recaudacion-bot

Consulta el Total Acumulado de deudores en el sistema de Recaudación, para una cédula o para todas las cédulas de un Excel.

## Estructura

```
backend/    API Flask + bot Playwright (app.py, lote.py, login.py, .env)
frontend/   Interfaz Angular
```

## Instalación

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cd ../frontend && npm install && npm run build
```

## Uso web

```bash
cd backend
python app.py          # http://localhost:5050  (sirve la API y el frontend compilado)
```

1. Carga un `.xlsx` o `.csv` con una columna **Cédula** (también vale *Identificación* o *RUC*; si no hay encabezado se usa la primera columna). En la página hay una plantilla para descargar.
2. El bot inicia sesión **una sola vez** y consulta las cédulas una tras otra. En la página se ve el avance.
3. Al terminar (o si lo cancelas) se descarga un ZIP con:
   - `resumen.xlsx`: cédula, estado (Consultada / Sin registros / Error), valor y texto del Total Acumulado.
   - `capturas/total_<cédula>.png` de cada cédula (o `sin_datos_…` / `error_…` como evidencia).

Los lotes se procesan de uno en uno, porque el sistema solo permite una sesión por usuario.

No se crean carpetas en el proyecto: cada lote trabaja en una carpeta temporal del sistema, las capturas se borran al armar el ZIP y los ZIP se borran al detener el servidor.

Para desarrollar el frontend: `cd frontend && npm start` (http://localhost:4300; las llamadas a `/api` se envían a `:5050`).

## Uso por consola

```bash
cd backend
python login.py 0102030405        # una cédula -> total_0102030405.png
python login.py cedulas.xlsx      # lote -> resultados_<fecha>.zip
```

## Notas

- Excel guarda las cédulas como número y pierde el 0 inicial: si quedan 9 dígitos se completa a 10 (o de 12 a 13 si es un RUC). Se eliminan las cédulas repetidas.
- Si hay 2 errores seguidos, el bot vuelve a iniciar sesión y continúa.
- En un servidor sin pantalla usa `HEADLESS=true`.
- Variables opcionales en `backend/.env`: `RECAUDACION_MAX_CEDULAS` (500 por defecto) y `PORT` (5050).
