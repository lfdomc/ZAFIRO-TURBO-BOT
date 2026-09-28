-- Migración: captura y clasificación de correcciones del admin
-- (Fases 1-3 del sistema de autoaprendizaje por retrieval)
--
-- Ejecutar una sola vez en el SQL editor de Supabase.

-- Fase 1: guardar el message_id de Telegram de cada borrador que manda el
-- bot, y el texto final que el admin terminó enviando cuando corrige por
-- "reply". Solo se usan en filas con role='model'.
alter table historial_archivo
    add column if not exists telegram_message_id bigint,
    add column if not exists respuesta_editada text,
    add column if not exists editada_en timestamptz;

-- Índice para el lookup por message_id que hace obtener_historial_por_message_id
-- en cada reply del admin.
create index if not exists idx_historial_archivo_telegram_message_id
    on historial_archivo (telegram_message_id)
    where telegram_message_id is not null;

-- Fase 2/3: correcciones de ESTILO (no de información) detectadas al
-- comparar el borrador vs. la versión final del admin. 'aprobada' arranca
-- en false siempre — la Fase 3 (inyectar estos patrones como ejemplos en el
-- prompt) exige que un admin la apruebe explícitamente desde el panel; nunca
-- se promueve sola, ni siquiera después de repetirse muchas veces.
create table if not exists correcciones_estilo (
    id bigint generated always as identity primary key,
    property_id text,
    unit_id text,
    texto_original text not null,
    texto_editado text not null,
    aprobada boolean not null default false,
    aprobada_en timestamptz,
    creado_en timestamptz not null default now()
);

create index if not exists idx_correcciones_estilo_aprobada
    on correcciones_estilo (aprobada);
