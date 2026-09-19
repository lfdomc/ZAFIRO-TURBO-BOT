"""
Configuración central del backend. Todas las credenciales viven en
variables de entorno (nunca en el código) — en Railway se configuran
en el servicio -> pestaña Variables.
"""
import os


class Settings:
    # Supabase — DEBE ser la clave service_role, nunca anon (ver .env.example)
    SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY", "")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_WEBHOOK_SECRET: str = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    # Clave compartida que protege /admin/* y /export/propiedades.json —
    # la usan tanto la pestaña Admin del sitio (header X-Admin-Key) como
    # el script generar-propiedades.mjs en el build de Vercel.
    ADMIN_API_KEY: str = os.environ.get("ADMIN_API_KEY", "")

    # Vercel Deploy Hook (Project Settings -> Git -> Deploy Hooks) — si
    # está configurado, cada vez que se guarda/borra una propiedad desde
    # el panel Admin se dispara un redeploy automático del sitio.
    VERCEL_DEPLOY_HOOK_URL: str = os.environ.get("VERCEL_DEPLOY_HOOK_URL", "")

    # WhatsApp Cloud API (Meta) — para el reporte automático de
    # mantenimiento/limpieza. Ver README para cómo conseguir estos valores.
    WHATSAPP_PHONE_NUMBER_ID: str = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_ACCESS_TOKEN: str = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_TEMPLATE_NAME: str = os.environ.get("WHATSAPP_TEMPLATE_NAME", "reporte_incidencia")
    WHATSAPP_TEMPLATE_LANG: str = os.environ.get("WHATSAPP_TEMPLATE_LANG", "es")

    # Números de los coordinadores (uno para todas las propiedades, en
    # formato internacional ej. +50688887777). Una propiedad puntual
    # puede anular esto con su propio campo personalizado
    # ('whatsapp_mantenimiento' / 'whatsapp_limpieza') si algún día
    # necesita un destino distinto — pero el caso normal es este único
    # número para todas.
    WHATSAPP_MANTENIMIENTO_DEFAULT: str = os.environ.get("WHATSAPP_MANTENIMIENTO_DEFAULT", "")
    WHATSAPP_LIMPIEZA_DEFAULT: str = os.environ.get("WHATSAPP_LIMPIEZA_DEFAULT", "")

    # Minutos que espera el bot, tras avisar en Telegram, antes de mandar
    # el reporte solo por WhatsApp si nadie tocó ninguno de los botones.
    AUTO_ENVIO_WHATSAPP_MINUTOS: int = int(os.environ.get("AUTO_ENVIO_WHATSAPP_MINUTOS", "10"))

    # URL pública del sitio en Vercel (sin / al final) — se usa para
    # armar el link de consulta temporal que el bot le da al admin.
    SITE_BASE_URL: str = os.environ.get("SITE_BASE_URL", "").rstrip("/")

    # Horas que dura activo un link de consulta temporal para huéspedes.
    ACCESO_TEMPORAL_HORAS: int = int(os.environ.get("ACCESO_TEMPORAL_HORAS", "24"))

    # Fase 1: solo estos chat_id de Telegram pueden usar el bot (el
    # equipo admin).
    @staticmethod
    def obtener_admin_chat_ids() -> set[str]:
        crudo = os.environ.get("ADMIN_CHAT_IDS", "")
        return {c.strip() for c in crudo.split(",") if c.strip()}

    # Pool de claves de Gemini — cualquier variable que empiece con
    # GEMINI_KEY_ se suma al pool, rotando automáticamente si una se
    # queda sin cuota (429).
    @staticmethod
    def obtener_pool_claves_gemini() -> list[str]:
        claves = []
        for nombre, valor in os.environ.items():
            if nombre.upper().startswith("GEMINI_KEY_") and valor.strip():
                for pedazo in valor.split(","):
                    pedazo_limpio = pedazo.strip()
                    if pedazo_limpio:
                        claves.append(pedazo_limpio)
        vistas = set()
        unicas = []
        for c in claves:
            if c not in vistas:
                vistas.add(c)
                unicas.append(c)
        return unicas


settings = Settings()
