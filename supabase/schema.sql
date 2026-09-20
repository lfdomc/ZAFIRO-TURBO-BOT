-- ============================================================
-- Esquema Supabase — Base de conocimiento de propiedades (Zafiro)
-- Fase 1: uso interno admin. Preparado para habilitar huéspedes
-- después vía la columna `audiencia`, sin migrar nada.
--
-- Diseño: `properties.datos` guarda el objeto JSON COMPLETO de cada
-- propiedad, tal cual lo produce hoy App.jsx (listings, rooms, extra,
-- checkin[], messages[], publicInfo, localExperiences, etc.) — esa es
-- la fuente de verdad, así el sitio en React sigue recibiendo
-- exactamente la misma forma de datos. `knowledge_chunks` es un
-- índice DERIVADO de ese mismo contenido, solo para que el bot busque
-- por significado en milisegundos — nunca la fuente de verdad.
-- ============================================================

create extension if not exists vector;
create extension if not exists pgcrypto; -- gen_random_uuid()

-- ------------------------------------------------------------
-- Una fila por propiedad. El id es el mismo slug que ya usa el
-- JSON/React (ej. 'urban-escalante', 'qbo') — así reingestar es
-- idempotente por diseño (upsert por id, no se acumulan duplicados).
-- ------------------------------------------------------------

create table if not exists properties (
  id text primary key,
  nombre text not null,
  grupo text,                     -- 'sanjose' | 'jaco' | 'guanacaste' (el campo "group" del JSON)
  zona text,
  propietario text,               -- el campo "owner" del JSON
  datos jsonb not null,           -- objeto COMPLETO de la propiedad, verbatim
  actualizado_en timestamptz not null default now()
);

-- ------------------------------------------------------------
-- Configuración/datos globales que no pertenecen a una sola
-- propiedad: checkInGeneral, checkOutGeneral, general{formulario,
-- comunicacion, reservaDirecta, mensajesFrecuentes, contactos, faqs},
-- masterTable. Una fila por clave de nivel superior del JSON.
-- ------------------------------------------------------------

create table if not exists configuracion_general (
  clave text primary key,
  valor jsonb not null,
  actualizado_en timestamptz not null default now()
);

-- ------------------------------------------------------------
-- Fragmentos indexados para RAG. property_id = null significa
-- información general de la compañía (no de una propiedad puntual).
-- unit_id es el id de la unidad DENTRO del jsonb de la propiedad
-- (ej. 'u2307') — no es una fila propia, solo una etiqueta para poder
-- filtrar/mostrar de qué unidad viene el fragmento.
-- ------------------------------------------------------------

create table if not exists knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  property_id text references properties(id) on delete cascade,
  unit_id text,
  categoria text not null,        -- 'check_in' | 'check_out' | 'wifi' | 'reglas' | 'amenidades' | 'contacto' | 'faq' | 'mensajes' | 'info' | 'nota_interna'
  contenido text not null,
  idioma text default 'es',
  audiencia text not null default 'admin',  -- 'admin' | 'guest' | 'both' — fase 2: filtra qué puede ver un huésped
  embedding vector(768),          -- gemini-embedding-001 con outputDimensionality=768
  metadata jsonb not null default '{}',
  creado_en timestamptz not null default now()
);

alter table knowledge_chunks
  add column if not exists fts tsvector generated always as (to_tsvector('spanish', contenido)) stored;

create index if not exists idx_kc_property on knowledge_chunks(property_id);
create index if not exists idx_kc_audiencia on knowledge_chunks(audiencia);
create index if not exists idx_kc_fts on knowledge_chunks using gin(fts);

-- Índice vectorial — ivfflat es suficiente para el volumen esperado
-- (unos cuantos miles de filas). Si algún día pasa de ~50k, migrar a hnsw.
create index if not exists idx_kc_embedding on knowledge_chunks
  using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ------------------------------------------------------------
-- Campos personalizados definidos desde el panel Admin (ej. "¿Aceptan
-- animales?") — quedan disponibles para editarse en TODAS las
-- propiedades. El valor de cada propiedad vive dentro de su propio
-- `datos.camposPersonalizados[id]`, no en una tabla aparte.
-- ------------------------------------------------------------

create table if not exists custom_field_defs (
  id text primary key,
  etiqueta text not null,
  tipo text not null default 'texto',
  nivel text not null default 'propiedad',  -- 'propiedad' | 'unidad' — dónde se edita/aplica este campo
  creado_en timestamptz not null default now()
);

-- ------------------------------------------------------------
-- Reportes de mantenimiento/limpieza detectados por el bot — el botón
-- de Telegram y el envío automático por WhatsApp (Meta Cloud API) se
-- coordinan a través de esta tabla. Se guarda en Supabase (no solo en
-- memoria) para que un reinicio del proceso en Railway no pierda un
-- reporte que estaba esperando su ventana de tiempo.
-- ------------------------------------------------------------

create table if not exists reportes_incidencias (
  id uuid primary key default gen_random_uuid(),
  telegram_chat_id text,
  tipo text not null,             -- 'mantenimiento' | 'limpieza'
  property_id text references properties(id),
  nombre_propiedad text,
  detalle text not null,
  numero_destino text,            -- E.164, ej. +50688887777 (null = no hay número configurado)
  estado text not null default 'pendiente',  -- 'pendiente' | 'cancelado' | 'enviado' | 'sin_destino' | 'error_envio'
  enviar_en timestamptz not null,
  creado_en timestamptz not null default now()
);

create index if not exists idx_reportes_estado on reportes_incidencias(estado);

-- ------------------------------------------------------------
-- Accesos temporales — links de un día (configurable) que el bot le
-- da al admin para reenviarle al huésped, con una versión de la
-- propiedad sin datos internos. El token es la única "contraseña":
-- largo y aleatorio, nunca secuencial.
-- ------------------------------------------------------------

create table if not exists accesos_temporales (
  token text primary key,
  property_id text not null references properties(id) on delete cascade,
  unit_id text,                   -- null = todo el complejo (propiedades de 1 sola unidad); si no, solo esa unidad + lo compartido del complejo
  expira_en timestamptz not null,
  usos int not null default 0,
  creado_en timestamptz not null default now()
);

create index if not exists idx_accesos_expira on accesos_temporales(expira_en);

-- ------------------------------------------------------------
-- Historial de conversación (memoria corta por telegram_id)
-- ------------------------------------------------------------

create table if not exists historial_conversacion (
  id bigint generated always as identity primary key,
  telegram_id text not null,
  role text not null,
  contenido text not null,
  property_id text,               -- de qué propiedad se detectó que hablaba este turno (null = ninguna en particular)
  unit_id text,                   -- de qué unidad puntual (null = propiedad entera o ninguna) — evita mezclar Praia 41 con Praia 37, por ejemplo
  creado_en timestamptz not null default now()
);

create index if not exists idx_historial_telegram on historial_conversacion(telegram_id, property_id, unit_id, creado_en desc);

-- ------------------------------------------------------------
-- Log de errores del sistema (mismo patrón que ya usabas en JARVIS)
-- ------------------------------------------------------------

create table if not exists capa_logs (
  id bigint generated always as identity primary key,
  agente_origen text,
  descripcion_falla text,
  causa_raiz text,
  accion_correctiva text,
  estado text default 'OPEN',
  creado_en timestamptz not null default now()
);

create or replace function registrar_log_sistema(
  p_agente_origen text,
  p_descripcion_falla text,
  p_causa_raiz text,
  p_accion_correctiva text,
  p_estado text
) returns void
language sql
as $$
  insert into capa_logs(agente_origen, descripcion_falla, causa_raiz, accion_correctiva, estado)
  values (p_agente_origen, p_descripcion_falla, p_causa_raiz, p_accion_correctiva, p_estado);
$$;

-- ------------------------------------------------------------
-- Búsqueda híbrida (texto completo + vectorial) con Reciprocal
-- Rank Fusion — responde en milisegundos porque nunca escanea toda
-- la tabla, usa los índices gin/ivfflat de arriba.
-- ------------------------------------------------------------

create or replace function busqueda_hibrida_propiedades(
  query_text text,
  query_embedding vector(768),
  match_count int default 5,
  rrf_k int default 60,
  p_audiencia text default 'admin',
  p_property_id text default null
) returns table (
  id uuid,
  property_id text,
  unit_id text,
  categoria text,
  contenido text,
  metadata jsonb,
  similitud_coseno float,
  nombre_propiedad text
)
language sql
as $$
  with busqueda_texto as (
    select kc.id,
           row_number() over (order by ts_rank(kc.fts, plainto_tsquery('spanish', query_text)) desc) as rank
    from knowledge_chunks kc
    where kc.fts @@ plainto_tsquery('spanish', query_text)
      and (p_audiencia is null or kc.audiencia in (p_audiencia, 'both'))
      and (p_property_id is null or kc.property_id = p_property_id)
    limit least(match_count * 4, 50)
  ),
  busqueda_vectorial as (
    select kc.id,
           row_number() over (order by kc.embedding <=> query_embedding) as rank,
           1 - (kc.embedding <=> query_embedding) as similitud
    from knowledge_chunks kc
    where (p_audiencia is null or kc.audiencia in (p_audiencia, 'both'))
      and (p_property_id is null or kc.property_id = p_property_id)
    order by kc.embedding <=> query_embedding
    limit least(match_count * 4, 50)
  ),
  combinado as (
    select coalesce(t.id, v.id) as id,
           (1.0 / (rrf_k + coalesce(t.rank, 1000))) + (1.0 / (rrf_k + coalesce(v.rank, 1000))) as score_rrf,
           v.similitud
    from busqueda_texto t
    full outer join busqueda_vectorial v on t.id = v.id
  )
  select kc.id, kc.property_id, kc.unit_id, kc.categoria, kc.contenido, kc.metadata,
         coalesce(c.similitud, 0) as similitud_coseno,
         p.nombre as nombre_propiedad
  from combinado c
  join knowledge_chunks kc on kc.id = c.id
  left join properties p on p.id = kc.property_id
  order by c.score_rrf desc
  limit match_count;
$$;
