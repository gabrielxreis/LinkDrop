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
