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
