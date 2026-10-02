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
