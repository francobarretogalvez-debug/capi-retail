# Capi — Guía de Deploy en Streamlit Cloud

## Qué necesitas
- Cuenta de GitHub (gratis) — github.com
- Cuenta de Streamlit Cloud (gratis) — share.streamlit.io
- Tu API key de Anthropic (para el chat IA — opcional, la app funciona sin ella)

## Paso 1: Crear repositorio en GitHub

1. Ve a **github.com/new**
2. Nombre del repo: `capi-retail`
3. Marca **Private** (importante — tu código no se hace público)
4. Click "Create repository"

## Paso 2: Subir archivos al repositorio

Desde la página del repo vacío, click **"uploading an existing file"** y sube estos archivos:

**Archivos obligatorios:**
- `app_streamlit.py`
- `motor_v2.py`
- `chat_engine.py`
- `renderers_alertas_tienda.py`
- `transformar_profundidad.py`
- `config_marca_tiendas.json`
- `config_matriz_tiendas.json`
- `requirements.txt`

**Carpeta .streamlit (crear manualmente en GitHub):**
- Click "Add file" → "Create new file"
- Nombre: `.streamlit/config.toml`
- Pega el contenido del archivo config.toml

Haz commit de todos los archivos.

## Paso 3: Conectar con Streamlit Cloud

1. Ve a **share.streamlit.io** y logea con tu cuenta de GitHub
2. Click **"New app"**
3. Selecciona tu repo `capi-retail`
4. Branch: `main`
5. Main file path: `app_streamlit.py`
6. Click **"Deploy!"**

## Paso 4: Configurar API Key (opcional — solo para el chat IA)

1. En Streamlit Cloud, ve a tu app → **Settings** → **Secrets**
2. Pega esto:
```toml
ANTHROPIC_API_KEY = "sk-ant-api03-TU-KEY-AQUI"
```
3. Save. La app se reinicia automáticamente.

Sin esta key, toda la app funciona normal excepto el chat con IA.

## Paso 5: Usar desde Ripley

1. Streamlit te da una URL tipo: `https://capi-retail.streamlit.app`
2. Abre esa URL en Chrome de tu PC Ripley
3. Sube tu Plantilla Excel desde el browser
4. Listo — Capi corre en la nube, tú solo necesitas el browser

## Si Ripley bloquea streamlit.app

Plan B: **Railway** (gratis 500 hrs/mes)
1. Ve a railway.app y logea con GitHub
2. "New Project" → "Deploy from GitHub repo"
3. Selecciona `capi-retail`
4. En Settings → Variables, agrega `ANTHROPIC_API_KEY`
5. En Settings → Networking, genera un dominio público
6. El comando de start es: `streamlit run app_streamlit.py --server.port $PORT`

Plan C: **Render** (gratis, se duerme tras 15 min inactivo)
1. Ve a render.com → "New Web Service"
2. Conecta tu repo
3. Start command: `streamlit run app_streamlit.py --server.port $PORT --server.headless true`
4. Agrega la variable ANTHROPIC_API_KEY

## Notas importantes

- **La data NO se guarda en el servidor.** Cada vez que uses Capi, subes tu Plantilla fresca. Esto es bueno para seguridad (la data de Ripley no queda almacenada en la nube).
- **Los config JSON sí persisten** en el repo (matriz de tiendas por marca/línea). Si los editas desde la app, los cambios duran hasta que el servidor se reinicie. Para cambios permanentes, edítalos en GitHub.
- **El repo es privado.** Nadie fuera de tu cuenta puede ver el código.
- **Streamlit Cloud gratis** te da 1 app con recursos compartidos. Para uso individual es más que suficiente.
