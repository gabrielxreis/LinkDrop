# Prompt para o Lovable: licenças do LinkDrop

Cole este texto inteiro no chat do Lovable do projeto **linkdrop.gabrielxreis.com**.

---

Quero adicionar um sistema de licenças ao site do LinkDrop (plugin para DaVinci Resolve). O plugin já está pronto e vai chamar as Edge Functions abaixo, então **os nomes das funções, os campos e as respostas precisam ser exatamente estes** (não renomeie nada). O plugin chama `https://gveupqzeqnkixudcrvxl.supabase.co/functions/v1/<nome-da-função>` sem login.

## Regras do negócio

- Licença: **R$ 19,90**, pagamento único, **válida por 1 ano** a partir da compra.
- **Uma key por computador.** Para usar em outro computador, o cliente revoga o computador atual na página da conta.
- O cliente pode revogar computadores **até 5 vezes**; na 5ª, revogar fica bloqueado por **5 dias**, depois o contador zera. (A regra já está na função `license-revoke`; o site só mostra a mensagem que ela devolver.)
- **Teste grátis de 24 horas**: cada conta gera uma key de teste (começa com `LDT-`). As 24h começam quando a key é ativada no plugin. **Cada computador só pode usar um teste**, mesmo criando outra conta (o servidor bloqueia pelo ID do hardware).
- Keys pagas começam com `LD-`, formato `LD-XXXX-XXXX-XXXX`.

## 1. Banco de dados

Rode esta migração:

```sql
-- LinkDrop licensing schema (Supabase / Postgres)
-- A license is one key for ONE computer. Paid: valid 1 year. Trial: 24 h from first activation,
-- and each computer can only ever run one trial (tracked by its hashed hardware ID).

create table if not exists public.licenses (
  id uuid primary key default gen_random_uuid(),
  key text unique not null,                       -- e.g. LD-7K2F-9QXA-M4TD (paid) or LDT-... (trial)
  user_id uuid references auth.users (id) on delete set null,
  email text,
  kind text not null check (kind in ('trial', 'paid')),
  status text not null default 'active' check (status in ('active', 'disabled', 'refunded')),
  created_at timestamptz not null default now(),
  expires_at timestamptz,                         -- paid: created_at + 1 year; trial: set on first activation
  stripe_session_id text unique,
  revoke_count int not null default 0,            -- computers removed since the last cooldown
  revoke_locked_until timestamptz                 -- after 5 removals, removing is blocked for 5 days
);

create table if not exists public.activations (
  id uuid primary key default gen_random_uuid(),
  license_id uuid not null references public.licenses (id) on delete cascade,
  machine_id text not null,                       -- sha256 of the hardware ID, never the raw ID
  machine_name text,
  os text,
  app_version text,
  activated_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz
);
create index if not exists activations_license_idx on public.activations (license_id);
create unique index if not exists activations_one_active_per_license
  on public.activations (license_id) where revoked_at is null;

-- every computer that ever started a trial (blocks a second free trial from a new account)
create table if not exists public.trial_machines (
  machine_id text primary key,
  license_id uuid references public.licenses (id) on delete set null,
  first_seen_at timestamptz not null default now()
);

alter table public.licenses enable row level security;
alter table public.activations enable row level security;
alter table public.trial_machines enable row level security;

-- people can read their own licenses and activations; all writes go through edge functions (service role)
drop policy if exists "own licenses" on public.licenses;
create policy "own licenses" on public.licenses for select using (auth.uid() = user_id);
drop policy if exists "own activations" on public.activations;
create policy "own activations" on public.activations for select
  using (exists (select 1 from public.licenses l where l.id = license_id and l.user_id = auth.uid()));
```

## 2. Secrets

- `LICENSE_PRIVATE_KEY`: vou colar a chave privada RSA (formato PKCS#8, começa com `-----BEGIN PRIVATE KEY-----`). Ela assina as licenças; **nunca exponha no frontend**.
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`: os padrões do projeto.
- Stripe (para o pagamento, item 4).

## 3. Edge Functions (crie com este código exato)

`license-activate`, `license-validate` e `license-deactivate` são chamadas pelo plugin **sem login**: configure `verify_jwt = false` para elas. `license-revoke` e `license-start-trial` são chamadas pelo site com a sessão do usuário (`Authorization: Bearer <access_token>`).

### `supabase/functions/_shared/license.ts`

```ts
// Shared helpers for the LinkDrop license edge functions.
// Secrets (Supabase project settings):
//   LICENSE_PRIVATE_KEY        RSA private key, PKCS#8 PEM (the plugin embeds the matching public key)
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   (provided by Supabase)
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

export const SITE = "https://linkdrop.gabrielxreis.com";
export const OFFLINE_GRACE_DAYS = 7;   // a token keeps the plugin unlocked this long without internet
export const MAX_REVOKES = 5;          // computers a key can be removed from...
export const REVOKE_COOLDOWN_DAYS = 5; // ...before removing is blocked for this long

export const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
}

export function admin() {
  return createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false },
  });
}

/** Status the plugin understands. `message` is shown to the person as-is. */
export function refuse(status: string, message: string) {
  return json({ ok: false, status, message });
}

let keyPromise: Promise<CryptoKey> | null = null;
function signingKey() {
  if (!keyPromise) {
    const pem = Deno.env.get("LICENSE_PRIVATE_KEY")!;
    const b64 = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
    const der = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    keyPromise = crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  }
  return keyPromise;
}

function b64url(bytes: Uint8Array) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** token = base64url(payload JSON) + "." + base64url(RSA-SHA256 signature of those payload bytes) */
export async function issueToken(license: { key: string; kind: string; email: string | null; expires_at: string },
                                 machineId: string) {
  const now = Math.floor(Date.now() / 1000);
  const expires = Math.floor(new Date(license.expires_at).getTime() / 1000);
  const payload = {
    key: license.key,
    kind: license.kind,
    email: license.email,
    machine: machineId,
    issued: now,
    expires,                                                        // end of the license itself
    valid_until: Math.min(expires, now + OFFLINE_GRACE_DAYS * 86400), // re-check with the server before this
  };
  const body = new TextEncoder().encode(JSON.stringify(payload));
  const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", await signingKey(), body));
  return { token: `${b64url(body)}.${b64url(sig)}`, payload };
}

export async function readBody(req: Request) {
  try {
    const b = await req.json();
    return {
      key: String(b.key ?? "").trim().toUpperCase(),
      machine_id: String(b.machine_id ?? "").trim().toLowerCase(),
      machine_name: String(b.machine_name ?? "").slice(0, 80),
      os: String(b.os ?? "").slice(0, 40),
      app_version: String(b.app_version ?? "").slice(0, 20),
    };
  } catch {
    return null;
  }
}

export const validMachine = (m: string) => /^[0-9a-f]{64}$/.test(m);

/** Removes a computer from a license, enforcing "5 removals, then wait 5 days".
 *  Returns null on success, or a message explaining when it can be tried again. */
// deno-lint-ignore no-explicit-any
export async function revokeActivation(db: any, license: any, activationId: string): Promise<string | null> {
  const now = new Date();
  if (license.revoke_locked_until && new Date(license.revoke_locked_until) > now) {
    const until = new Date(license.revoke_locked_until);
    const hours = Math.ceil((until.getTime() - now.getTime()) / 3600000);
    const wait = hours > 48 ? `${Math.ceil(hours / 24)} days` : `${hours} hours`;
    return `You've removed this license from ${MAX_REVOKES} computers. You can remove it again in ${wait} ` +
      `(${until.toISOString().slice(0, 10)}).`;
  }
  const { data: done } = await db.from("activations").update({ revoked_at: now.toISOString() })
    .eq("id", activationId).eq("license_id", license.id).is("revoked_at", null).select("id");
  if (!done || done.length === 0) return "This computer isn't active on this license.";
  let count = (license.revoke_count ?? 0) + 1;
  let lockedUntil: string | null = null;
  if (count >= MAX_REVOKES) {
    lockedUntil = new Date(now.getTime() + REVOKE_COOLDOWN_DAYS * 86400000).toISOString();
    count = 0;
  }
  await db.from("licenses").update({ revoke_count: count, revoke_locked_until: lockedUntil }).eq("id", license.id);
  return null;
}

/** New random key, e.g. LD-7K2F-9QXA-M4TD (paid) or LDT-... (trial). No 0/O/1/I to avoid typos. */
export function newKey(kind: "trial" | "paid") {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const chars = Array.from(bytes, (b) => abc[b % abc.length]).join("");
  return `${kind === "trial" ? "LDT" : "LD"}-${chars.slice(0, 4)}-${chars.slice(4, 8)}-${chars.slice(8, 12)}`;
}
```

### `supabase/functions/license-activate/index.ts`

```ts
// POST { key, machine_id, machine_name, os, app_version }
// Ties a key to this computer (one computer per key) and returns a signed token.
import { admin, cors, issueToken, json, readBody, refuse, SITE, validMachine } from "../_shared/license.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  const b = await readBody(req);
  if (!b || !b.key || !validMachine(b.machine_id)) return refuse("invalid", "That doesn't look like a LinkDrop key.");
  const db = admin();

  const { data: lic } = await db.from("licenses").select("*").eq("key", b.key).maybeSingle();
  if (!lic) return refuse("invalid", "This key doesn't exist. Check it and try again.");
  if (lic.status !== "active") return refuse("disabled", "This key is no longer active.");

  // trial: one per computer, ever
  if (lic.kind === "trial") {
    const { data: used } = await db.from("trial_machines").select("license_id").eq("machine_id", b.machine_id).maybeSingle();
    if (used && used.license_id !== lic.id) {
      return refuse("trial_used", `This computer already used its free trial. Get a license at ${SITE}`);
    }
  }

  const now = new Date();
  if (lic.expires_at && new Date(lic.expires_at) <= now) {
    return refuse("expired", lic.kind === "trial"
      ? `Your free trial ended. Get a license at ${SITE}`
      : `This license expired. Renew it at ${SITE}`);
  }

  // one computer per key: another active computer must be revoked first (from the site)
  const { data: active } = await db.from("activations").select("*").eq("license_id", lic.id).is("revoked_at", null)
    .maybeSingle();
  if (active && active.machine_id !== b.machine_id) {
    return refuse("in_use", `This key is active on "${active.machine_name || "another computer"}". ` +
      `Revoke that computer at ${SITE}/account to use it here.`);
  }

  if (lic.kind === "trial" && !lic.expires_at) {
    const ends = new Date(now.getTime() + 24 * 3600 * 1000).toISOString();
    await db.from("licenses").update({ expires_at: ends }).eq("id", lic.id);
    lic.expires_at = ends;
    await db.from("trial_machines").upsert({ machine_id: b.machine_id, license_id: lic.id });
  }

  if (active) {
    await db.from("activations").update({
      last_seen_at: now.toISOString(), app_version: b.app_version, machine_name: b.machine_name, os: b.os,
    }).eq("id", active.id);
  } else {
    await db.from("activations").insert({
      license_id: lic.id, machine_id: b.machine_id, machine_name: b.machine_name, os: b.os, app_version: b.app_version,
    });
  }

  const { token, payload } = await issueToken(lic, b.machine_id);
  return json({ ok: true, status: "active", token, license: payload });
});
```

### `supabase/functions/license-validate/index.ts`

```ts
// POST { key, machine_id, app_version }
// Called every time LinkDrop opens (when online). Returns a fresh token, or why the computer is locked.
import { admin, cors, issueToken, json, readBody, refuse, SITE, validMachine } from "../_shared/license.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  const b = await readBody(req);
  if (!b || !b.key || !validMachine(b.machine_id)) return refuse("invalid", "That doesn't look like a LinkDrop key.");
  const db = admin();

  const { data: lic } = await db.from("licenses").select("*").eq("key", b.key).maybeSingle();
  if (!lic) return refuse("invalid", "This key doesn't exist.");
  if (lic.status !== "active") return refuse("disabled", "This key is no longer active.");

  const { data: act } = await db.from("activations").select("*").eq("license_id", lic.id)
    .eq("machine_id", b.machine_id).is("revoked_at", null).maybeSingle();
  if (!act) return refuse("revoked", `This computer was removed from your license. Activate it again at ${SITE}/account`);

  if (lic.expires_at && new Date(lic.expires_at) <= new Date()) {
    return refuse("expired", lic.kind === "trial"
      ? `Your free trial ended. Get a license at ${SITE}`
      : `This license expired. Renew it at ${SITE}`);
  }

  await db.from("activations").update({ last_seen_at: new Date().toISOString(), app_version: b.app_version })
    .eq("id", act.id);
  const { token, payload } = await issueToken(lic, b.machine_id);
  return json({ ok: true, status: "active", token, license: payload });
});
```

### `supabase/functions/license-deactivate/index.ts`

```ts
// POST { key, machine_id }  -> frees the key from this computer ("Deactivate" inside the plugin).
// Same limit as the site: 5 removals, then 5 days before removing again.
import { admin, cors, json, readBody, refuse, revokeActivation, validMachine } from "../_shared/license.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  const b = await readBody(req);
  if (!b || !b.key || !validMachine(b.machine_id)) return refuse("invalid", "That doesn't look like a LinkDrop key.");
  const db = admin();
  const { data: lic } = await db.from("licenses").select("*").eq("key", b.key).maybeSingle();
  if (!lic) return refuse("invalid", "This key doesn't exist.");
  const { data: act } = await db.from("activations").select("id").eq("license_id", lic.id)
    .eq("machine_id", b.machine_id).is("revoked_at", null).maybeSingle();
  if (!act) return json({ ok: true, status: "deactivated" });
  const problem = await revokeActivation(db, lic, act.id);
  if (problem) return refuse("revoke_locked", problem);
  return json({ ok: true, status: "deactivated" });
});
```

### `supabase/functions/license-revoke/index.ts`

```ts
// POST { activation_id }  with the signed-in person's session (Authorization: Bearer <access token>)
// The site's "Revoke" button. Only the owner of the license can revoke, with the 5-removals / 5-days limit.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { admin, cors, json, refuse, revokeActivation } from "../_shared/license.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  const auth = req.headers.get("Authorization") ?? "";
  const user = await createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: auth } },
  }).auth.getUser();
  if (!user.data.user) return refuse("unauthorized", "Sign in to manage your computers.");

  let activationId = "";
  try { activationId = String((await req.json()).activation_id ?? ""); } catch { /* empty */ }
  const db = admin();
  const { data: act } = await db.from("activations").select("id, license_id").eq("id", activationId).maybeSingle();
  if (!act) return refuse("invalid", "Computer not found.");
  const { data: lic } = await db.from("licenses").select("*").eq("id", act.license_id).maybeSingle();
  if (!lic || lic.user_id !== user.data.user.id) return refuse("unauthorized", "This isn't your license.");

  const problem = await revokeActivation(db, lic, act.id);
  if (problem) return refuse("revoke_locked", problem);
  return json({ ok: true, status: "revoked" });
});
```

### `supabase/functions/license-start-trial/index.ts`

```ts
// POST (signed-in person's session) -> creates their single free-trial key, or returns the one they already have.
// The 24 h start when the key is first activated in LinkDrop; each computer can only ever run one trial.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { admin, cors, json, newKey, refuse } from "../_shared/license.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  const user = (await createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: req.headers.get("Authorization") ?? "" } },
  }).auth.getUser()).data.user;
  if (!user) return refuse("unauthorized", "Sign in to start your free trial.");

  const db = admin();
  const { data: existing } = await db.from("licenses").select("key, expires_at").eq("user_id", user.id)
    .eq("kind", "trial").maybeSingle();
  if (existing) return json({ ok: true, key: existing.key, expires_at: existing.expires_at, existing: true });

  for (let i = 0; i < 5; i++) {
    const key = newKey("trial");
    const { error } = await db.from("licenses").insert({ key, kind: "trial", user_id: user.id, email: user.email });
    if (!error) return json({ ok: true, key, expires_at: null, existing: false });
  }
  return refuse("error", "Couldn't create a trial key. Try again.");
});
```

## 4. Pagamento (Stripe, em reais, com Pix e cartão)

- Produto **"LinkDrop: licença 1 ano"**, preço único de **R$ 19,90** (BRL). Ative Pix e cartão no Checkout.
- Função `create-checkout` (usuário logado): cria uma Checkout Session com `metadata.user_id` e `customer_email`, e redireciona para o Stripe. Sucesso volta para `/account?paid=1`.
- Webhook `stripe-webhook` (`verify_jwt = false`, valide a assinatura do Stripe): no evento `checkout.session.completed`, crie uma licença com `kind = 'paid'`, `key = newKey("paid")` (use a função `newKey` de `_shared/license.ts`), `user_id` e `email` do metadata, `expires_at = now() + 1 year`, `stripe_session_id = session.id`. Use o `stripe_session_id` para nunca criar duas licenças pela mesma compra.
- Mande um e-mail para o cliente com a key, se o projeto já tiver envio de e-mail.

## 5. Páginas do site (mesmo visual escuro/azul que o site já tem)

**Login/cadastro** (e-mail + senha, ou link mágico).

**/account** (só logado):
- Card **"Teste grátis de 24h"**: botão "Começar teste grátis" que chama `license-start-trial` e mostra a key `LDT-...` com botão Copiar. Se a conta já tem teste, mostra a key e o status (não ativada / termina em X / terminou). Texto: "Cole a key no LinkDrop dentro do DaVinci Resolve. As 24 horas começam na ativação. Um teste por computador."
- Card **"Licença LinkDrop"** para cada licença paga: key com botão Copiar, "Válida até DD/MM/AAAA", e o status.
- Em cada licença (paga ou teste), a lista de **computadores**: nome, sistema (macOS/Windows), "Ativado em", "Visto por último", e botão **"Revogar"**. O botão confirma ("Este computador vai perder o acesso ao LinkDrop. Continuar?") e chama `license-revoke` com `{ activation_id }`. Se a resposta tiver `ok: false`, mostre a `message` (ex.: o bloqueio de 5 dias). Depois de revogar, a key fica livre para ativar em outro computador.
- Botão **"Comprar licença: R$ 19,90/ano"** que chama `create-checkout`. Mostrar também na home.
- Leia `licenses` e `activations` direto do Supabase com a sessão do usuário (as políticas RLS já permitem ler só o que é dele). Não exponha a tabela `trial_machines` no frontend.

**Home:**
- Trocar "Free installers" / "Free for macOS and Windows" por: **"Teste grátis por 24 horas. Depois, R$ 19,90 por ano."**
- Seção de preço com: 1 computador por licença, 1 ano de atualizações, teste grátis de 24h, botão "Começar teste grátis" (vai para login e depois /account) e "Comprar licença".
- Seção "Como ativar": 1) Instale o LinkDrop; 2) Abra no DaVinci: Workspace > Scripts > LinkDrop; 3) Cole sua key; 4) Para trocar de computador, revogue o antigo em Minha conta.
- Link "Minha conta" no topo.

## 6. Teste antes de publicar

Crie uma licença de teste manual na tabela `licenses` (`kind = 'paid'`, `key = 'LD-TEST-TEST-TEST'`, `expires_at` daqui a 1 ano) e me avise para eu testar a ativação pelo plugin.
