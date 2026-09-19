# Bot de conocimiento de propiedades (Zafiro) — Fase 1: solo admin

Adaptado del backend JARVIS: se rescató la capa de infraestructura
(Gemini con rotación de claves, cliente de Telegram, idempotencia,
caché de FAQ) y se reemplazó todo lo específico de gestión de
documentos por un esquema de propiedades con búsqueda vectorial en
Supabase.

## Diseño: jsonb como fuente de verdad + índice derivado

`properties.datos` guarda el objeto **completo y verbatim** de cada
propiedad — la misma forma exacta que ya produce `App.jsx`/
`generar-propiedades.mjs` (listings, `rooms`, `extra`, `checkin[]`,
`messages[]`, `publicInfo`, `localExperiences`, etc.). Eso es la
fuente de verdad. `knowledge_chunks` es un índice **derivado**: un
fragmento de texto + su embedding por cada dato atómico (un check-in,
una regla, un dato de wifi...), solo para que el bot busque por
significado. Si mañana agregás un campo nuevo al JSON, el export
estático lo sirve igual sin tocar el esquema — solo hay que sumar la
extracción correspondiente en `ingest_propiedades.py` si querés que
también sea buscable por el bot.

## 1. Preparar Supabase

1. Creá un proyecto en [supabase.com](https://supabase.com) (plan free alcanza).
2. Anda a **SQL Editor** y corré el contenido completo de `supabase/schema.sql`.
3. En **Settings -> API**, copiá la `Project URL` y la clave `service_role`
   (NUNCA la `anon`) — van en `SUPABASE_URL` y `SUPABASE_KEY`.

## 2. Cargar tus propiedades — sin terminal, sin Python local

No hace falta correr nada en tu computadora. Una vez desplegado el
backend en Railway (paso 4) y el sitio en Vercel (paso 6):

1. Abrí tu sitio → pestaña **Admin** → poné tu `ADMIN_API_KEY`.
2. En la sección **"Importar JSON completo"**, hacé clic en "Elegir
   archivo…", confirmá, y seleccioná tu `propiedades.json`.
3. Se sube el archivo, arranca la importación en segundo plano dentro
   de Railway (genera un embedding por cada fragmento — con tus 12
   propiedades tarda unos minutos) y ves una barra de progreso en vivo.
   Podés cerrar la pestaña y volver después, sigue corriendo igual.

Si alguna vez preferís hacerlo desde la terminal (por ejemplo para
automatizarlo), `scripts/ingest_propiedades.py` sigue funcionando
igual — ver el Apéndice al final de este documento.

## 3. Probar localmente

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y llená tus valores reales
uvicorn app.main:app --reload
```

Para probar el webhook con Telegram real desde tu computadora, usa un
túnel público (ngrok o Cloudflare Tunnel) apuntando a
`http://localhost:8000`.

## 4. Desplegar en Railway

1. Subí este proyecto a un repositorio de GitHub (debe incluir el `Procfile`).
2. En [railway.com](https://railway.com): **New Project** -> **Deploy from GitHub repo**.
3. Railway detecta Python automáticamente y usa el `Procfile`.
4. En la pestaña **Variables**, agregá todas las de `.env.example` con
   tus valores reales (incluí tantas `GEMINI_KEY_N` como cuentas
   tengas, y tu propio chat_id en `ADMIN_CHAT_IDS`).
5. **Settings -> Networking -> Generate Domain** para obtener la URL pública.
6. Cada `git push` redespliega automáticamente.

## 5. Configurar el webhook de Telegram

```bash
curl -X POST "https://api.telegram.org/bot<TU_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://tu-servicio.up.railway.app/webhook/telegram",
    "secret_token": "el-mismo-valor-de-TELEGRAM_WEBHOOK_SECRET"
  }'
```

## 6. Generar el JSON estático para el sitio en React

Cada vez que actualices datos de propiedades en Supabase (o vuelvas a
correr la ingesta), regenerá el JSON que consume el sitio — el sitio
en Vercel nunca consulta Supabase directamente, solo lee este archivo:

```bash
python -m scripts.generar_json_estatico ruta/al/repo/react/public/propiedades.json
```

Automatizalo como paso previo al build/deploy del sitio (igual que ya
hacías con `generar-propiedades.mjs`).

## 7. Panel Admin — agregar/editar propiedades desde el sitio

Ya no hace falta editar `App.jsx` a mano ni correr scripts para agregar
una propiedad. El sitio en React ahora tiene una pestaña **Admin** que:

- Lista, crea, edita y elimina propiedades directo contra este backend
  (`/admin/propiedades`), que guarda en Supabase y **reindexa
  automáticamente** los fragmentos del bot (mismo código que usa
  `ingest_propiedades.py`, vía `app/property_service.py`).
- Permite definir **campos personalizados** nuevos (ej. "¿Aceptan
  animales?") desde `/admin/campos` — una vez creado, aparece como
  input editable en el formulario de TODAS las propiedades.
- Al guardar, dispara un **redeploy automático de Vercel** (Deploy
  Hook) — el sitio se actualiza solo en unos minutos, sin que toques
  una terminal.

Para que esto funcione, además de las variables ya descritas arriba,
configurá en Railway:
- `ADMIN_API_KEY` — inventá un valor largo; la pestaña Admin del sitio
  te lo va a pedir la primera vez que la abrís (se guarda en el
  navegador).
- `VERCEL_DEPLOY_HOOK_URL` (opcional pero recomendado) — desde tu
  proyecto en Vercel: **Settings -> Git -> Deploy Hooks**, creá uno y
  pegá la URL acá.

Y en Vercel (**Settings -> Environment Variables** del proyecto React):
- `VITE_BOT_API_URL` — la URL pública de tu servicio en Railway (la
  misma que usás en `setWebhook`, sin `/webhook/telegram` al final).
- `BOT_ADMIN_KEY` — el mismo valor que `ADMIN_API_KEY` en Railway. Esta
  variable la usa `scripts/generar-propiedades.mjs` en el build
  (server-side) — nunca llega al navegador, a diferencia de
  `VITE_BOT_API_URL` que sí es pública (por diseño de Vite, cualquier
  variable `VITE_*` queda visible en el bundle del navegador).

## 8. Reportes de mantenimiento/limpieza por WhatsApp

Cuando alguien le escribe al bot algo que suena a un problema de
mantenimiento o limpieza, el bot:

1. Lo clasifica automáticamente con IA.
2. Manda un mensaje en Telegram con dos botones: **"📲 Enviar yo por
   WhatsApp"** (abre WhatsApp con el texto ya escrito — vos apretás
   enviar) y **"✅ Ya avisé"**.
3. Si no tocás ninguno de los dos en `AUTO_ENVIO_WHATSAPP_MINUTOS`
   (10 por defecto), el bot lo manda solo, automáticamente, vía
   WhatsApp Cloud API.

**Límite importante:** la API oficial de Meta solo puede mandar
mensajes a números individuales, nunca a grupos de WhatsApp. Si el
destino de mantenimiento o limpieza de una propiedad es hoy un grupo,
para el envío automático necesitás el número de una persona
responsable en su lugar.

### Configurar Meta Cloud API (una sola vez)

1. Entrá a [developers.facebook.com](https://developers.facebook.com) → creá una cuenta de desarrollador si no tenés.
2. **Mis apps → Crear app → tipo "Empresa"** → seguí el asistente.
3. Dentro de la app, agregá el producto **WhatsApp**.
4. En **WhatsApp → Configuración de la API**: vas a ver un número de
   prueba gratis ya asignado (sirve para probar mandándote mensajes a
   vos mismo primero) y un **Phone Number ID** → eso es tu
   `WHATSAPP_PHONE_NUMBER_ID`.
5. Generá un **token de acceso permanente**: **Configuración de la
   app → Usuarios del sistema** → creá uno, asignale el permiso
   `whatsapp_business_messaging`, generá el token → eso es tu
   `WHATSAPP_ACCESS_TOKEN`.
6. Cuando quieras usar tu propio número de WhatsApp Business (no el de
   prueba), lo agregás y verificás ahí mismo en **WhatsApp → Números
   de teléfono**.

### Crear y aprobar la plantilla

1. **WhatsApp Manager → Plantillas de mensajes → Crear plantilla**.
2. Nombre: `reporte_incidencia` (o el que pongas en
   `WHATSAPP_TEMPLATE_NAME`). Categoría: **Utilidad**. Idioma: Español.
3. Cuerpo del mensaje, con 4 variables:
   ```
   🔧 Reporte de {{1}}
   Propiedad: {{2}}
   Unidad: {{3}}
   Detalle: {{4}}

   Enviado automáticamente por el bot de Zafiro.
   ```
4. Enviá a revisión. Las plantillas de categoría "Utilidad" suelen
   aprobarse rápido (minutos a un día).

### Configurar los números de los coordinadores

Lo normal es un solo coordinador de mantenimiento y uno de limpieza
para todas las propiedades — eso se configura una sola vez en Railway
(**Variables**):
- `WHATSAPP_MANTENIMIENTO_DEFAULT=+506xxxxxxxx`
- `WHATSAPP_LIMPIEZA_DEFAULT=+506xxxxxxxx`

Si algún día UNA propiedad puntual necesita un número distinto al
coordinador general (una excepción, no la regla), podés agregarlo
desde la pestaña Admin del sitio → **Campos personalizados** → crear
`whatsapp_mantenimiento` y/o `whatsapp_limpieza` — el bot revisa
primero el campo de esa propiedad y, si está vacío, usa el
coordinador global.

## 9. Página de consulta temporal para huéspedes

Cuando le preguntás al bot algo de check-in/acceso de una propiedad, te
manda automáticamente un segundo mensaje en Telegram con un link para
el huésped. El bot decide cuál link mandar así:

1. **Si la propiedad ya tiene una guía en guia.zafiropm.com** (el
   sitio que ya usa tu empleador): agregá el link completo
   (`https://guia.zafiropm.com/g/...`) como campo personalizado
   `link_guia_publica` desde la pestaña Admin del sitio, por
   propiedad. El bot manda ESE link tal cual — no genera nada propio,
   no hay token, no vence.
2. **Si esa propiedad no tiene ese campo lleno**, cae de respaldo al
   link temporal propio descrito abajo.

### El link temporal propio (respaldo)

Si `SITE_BASE_URL` está configurada, el link de respaldo es un
`tusitio.com/consulta?token=...` — válido por `ACCESO_TEMPORAL_HORAS`
(24 por defecto), reenviable al huésped las veces que haga falta
dentro de esa ventana.

**No es un chat con IA** — es una página plana, liviana (7.88 KB, vs
258 KB del panel completo) y pensada para el celular: nombre de la
propiedad, buscador de texto simple arriba, y abajo toda la info que
un huésped necesita (check-in, wifi, código de acceso, reglas,
amenidades, cómo llegar, mensajes de bienvenida) — sin notas internas
ni plantillas de correo a recepción (`app/guest_filter.py` define
exactamente qué se excluye; ajustalo si algo no debería estar ahí).

Configurar en Railway (solo si querés el respaldo activo):
- `SITE_BASE_URL` = la URL pública de tu sitio en Vercel (sin `/` al final).
- `ACCESO_TEMPORAL_HORAS` (opcional, default 24).

Del lado de Vercel no hace falta nada nuevo — `consulta.html` ya es
parte del mismo proyecto/build, solo asegurate de que `VITE_BOT_API_URL`
esté configurada (la misma que usa el panel Admin).

## 10. Actualizador desde la guía pública existente (guia.zafiropm.com)

Tu empleador ya tiene una guía por propiedad en `guia.zafiropm.com/g/<id>`.
En vez de cargar esa info dos veces a mano, el backend la puede leer —
**pero nunca cambia nada solo**: siempre te muestra qué encontró y vos
elegís campo por campo si aceptarlo.

### Para una propiedad que ya tenés cargada

1. Completá el campo personalizado `link_guia_publica` con la URL de
   su guía (el mismo que ya usás para el link que le das al huésped).
2. En el formulario de esa propiedad, botón **"Revisar guía pública"**.
3. Aparece una comparación campo por campo:
   - **Check-in, check-out, parqueo, wifi** — comparación directa
     contra lo que ya tenés cargado, marcada como *igual* o
     *diferente*. Cada uno con un checkbox "Usar este valor".
   - **Cómo llegar, reglas de la casa** — se muestran lado a lado
     (actual vs. guía) para que lo evalúes vos; un texto libre nunca
     se marca "igual/diferente" en automático, porque dos redacciones
     distintas de lo mismo darían un falso positivo constante.
   - **Lugares cercanos, WhatsApp de servicio al cliente** — solo
     informativo, no hay campo equivalente hoy para aplicar directo.
4. Tildás lo que querés aceptar → **"Aplicar cambios seleccionados"**
   → se guarda en Supabase y se reindexa para el bot. Nada se toca
   hasta ese clic.

### Para crear una propiedad nueva

Al tocar "Agregar propiedad", ahora aparece la opción de poner el id
de la propiedad y el link de su guía pública — si lo cargás, el
formulario se abre **pre-llenado** (nombre, zona, check-in/check-out,
wifi, reglas, cómo llegar) para que lo revises, completes lo que
falte, y guardes vos mismo. Si no tenés guía todavía, "Empezar en
blanco" abre el formulario vacío como siempre.

### Aviso honesto sobre el `robots.txt`

`guia.zafiropm.com` tiene un `robots.txt` que dice no permitir acceso
automatizado. Esto lee la página de tu propio empleador para un uso
interno legítimo (no scrapea un sitio de terceros, y manda un
User-Agent identificándose como `ZafiroBot-Sync`), pero si administrás
o conocés a quien administra ese sitio, vale la pena confirmarlo antes
de usarlo en producción — sobre todo si corre detrás de Cloudflare u
otra protección que podría bloquear la IP de Railway.

### Preparado para el día que tengas una API real

Toda esta lógica vive en `app/fuente_externa.py`, con un contrato
simple: `obtener_html(url)` + `parsear_guia_zafiropm(html) -> dict`. El
día que tu empleador te dé acceso a una API real, se reemplaza esa
función por una llamada a la API — la comparación, el "aplicar
cambios" y el pre-llenado de propiedades nuevas siguen funcionando
igual, sin tocar nada más.

## Por qué Supabase "no se activa/desactiva" por request

No hace falta un interruptor manual: el sitio web es estático (nunca
toca Supabase en runtime) y solo el bot de FastAPI en Railway consulta
la base, únicamente cuando llega un mensaje de Telegram. El plan free
de Supabase pausa el proyecto completo tras ~1 semana sin ninguna
actividad — con este diseño eso casi nunca ocurre, pero si pasa, la
primera consulta después de la pausa simplemente tarda unos segundos
más mientras el proyecto despierta solo.

## Qué falta para fase 2 (atención directa a huéspedes)

El esquema ya está listo para esto sin migrar nada — cada fragmento en
`knowledge_chunks` tiene una columna `audiencia` (`admin` | `guest` |
`both`), y el panel Admin/export estático no cambian en nada. Pendiente
cuando llegue el momento:

- [ ] Marcar qué fragmentos son seguros para huéspedes (`audiencia='guest'`
      o `'both'`) al momento de la ingesta — códigos de acceso y notas
      internas quedan siempre en `'admin'`.
- [ ] En `main.py`, distinguir remitente admin vs huésped (mismo patrón
      que `ADMIN_CHAT_IDS` de hoy) y pasar `audiencia="guest"` en la
      búsqueda cuando no sea admin.
- [ ] Firma obligatoria en las respuestas a huéspedes (como
      `asegurarFirma()` en ZafiroBot).
- [ ] Decidir el canal de entrada para huéspedes (¿Telegram directo,
      o integración con el inbox de Hostify como ya tenías?).

## Apéndice: cargar propiedades desde la terminal (opcional)

El botón "Importar JSON completo" del panel Admin hace exactamente lo
mismo que este script — usalo solo si preferís la terminal o querés
automatizar la carga:

```bash
pip install -r requirements.txt
cp .env.example .env   # y llená tus valores reales primero
python -m scripts.ingest_propiedades ruta/a/tu/propiedades.json
```
