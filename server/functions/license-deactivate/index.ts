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
