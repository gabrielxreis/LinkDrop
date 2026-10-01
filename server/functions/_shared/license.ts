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
