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
