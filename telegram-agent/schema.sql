-- شغّل هذا الملف مرة وحدة في Supabase: SQL Editor → New query → الصق والصق Run

create table if not exists messages (
  id bigint generated always as identity primary key,
  chat_id bigint not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz not null default now()
);
create index if not exists messages_chat_id_idx on messages (chat_id, created_at);

create table if not exists reminders (
  id bigint generated always as identity primary key,
  chat_id bigint not null,
  due_at timestamptz not null,
  text text not null,
  sent boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists reminders_due_idx on reminders (due_at) where sent = false;

create table if not exists settings (
  chat_id bigint primary key,
  model text not null,
  updated_at timestamptz not null default now()
);
