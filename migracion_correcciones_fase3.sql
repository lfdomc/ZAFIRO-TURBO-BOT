-- Migración: aprobación humana + retrieval de correcciones de estilo
-- (Fase 3 del sistema de autoaprendizaje). Corré esto DESPUÉS de
-- migracion_correcciones_admin.sql.
--
-- Ejecutar una sola vez en el SQL editor de Supabase.

create extension if not exists vector;

alter table correcciones_estilo
    add column if not exists descartada boolean not null default false,
    add column if not exists descartada_en timestamptz,
    add column if not exists embedding vector(768);

-- Retrieval: trae las correcciones ya aprobadas (embedding no nulo) más
-- parecidas semánticamente a la consulta del huésped. Solo lee filas con
-- aprobada = true — una corrección pendiente o descartada nunca aparece acá,
-- así que nada se usa en vivo sin que un admin la haya aprobado a mano.
create or replace function buscar_correcciones_estilo_aprobadas(
    query_embedding vector(768),
    match_count int default 2
)
returns table (
    id bigint,
    texto_original text,
    texto_editado text,
    similarity float
)
language sql stable
as $$
    select id, texto_original, texto_editado,
           1 - (embedding <=> query_embedding) as similarity
    from correcciones_estilo
    where aprobada = true and embedding is not null
    order by embedding <=> query_embedding
    limit match_count;
$$;
