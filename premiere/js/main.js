/* LinkDrop for Premiere Pro (CEP panel). Node.js runs the same download tools the LinkDrop installer
   puts on the computer; ExtendScript (host/linkdrop.jsx) imports and places clips in Premiere. */
"use strict";
const VERSION = "1.0.0";
const PRODUCT = "premiere";                         // licenses are sold per product
const API = ["https://linkdrop.com.br/api/public", "https://linkdrop.gabrielxreis.com/api/public"];
const SITE = "https://linkdrop.com.br";
const INSTAGRAM = "https://instagram.com/gabrielxreis_";
const OFFLINE_GRACE_DAYS = 7;
const PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyZDHjS4fAiJXzb/WUcFd
AJbEBal5QhRRM2N6ARORkf2Ys0MfsIH9OqnWMFcLo2apXmRvkHjAHbpsxGebABh9
2taXBDxFvznsJmlnH++9Q/9ks+7xOu/41HN4n+BzD1UIjaEBPGSj9xC7iqWFtcc+
qW4X0vetjJ+9goW1Tz+N8H4NzllNG2PunHLK2emboU8zNmfPxisQDdszRTez7DDe
2v2ABNdPQ5Y1YERNVxIvsoVdFAAZ0UhXY7CnrnzB3BNdhxAq39VJy0TtWf+ouyAs
cdq2NZAl0XRvHs2A5lD7udKKfrsoitORTSC7ViiXE+wsX3dxM7NynglyMHyxiuSi
awIDAQAB
-----END PUBLIC KEY-----`;

const cs = new CSInterface();
const fs = require("fs"), path = require("path"), os = require("os"), cp = require("child_process"),
      crypto = require("crypto"), https = require("https");
const IS_WIN = process.platform === "win32";
const HOME = os.homedir();
const DATA = IS_WIN ? path.join(process.env.APPDATA || path.join(HOME, "AppData", "Roaming"), "LinkDrop")
                    : path.join(HOME, "Library", "Application Support", "LinkDrop");
const BIN = path.join(DATA, "bin");
const SETTINGS = path.join(DATA, "premiere-settings.json");
const LICENSE = path.join(DATA, "license-premiere.dat");
const $ = (id) => document.getElementById(id);
const LOCKED = ["expired", "revoked", "invalid", "disabled", "not_found", "inactive", "no_trial"];

/* ------------------------------------------------------------------ helpers */
const env = Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" });
env.PATH = [BIN, "/opt/homebrew/bin", "/usr/local/bin", env.PATH || ""].join(path.delimiter);

function exists(p) { try { fs.accessSync(p); return true; } catch (e) { return false; } }
function python3() {
  const fw = "/Library/Frameworks/Python.framework/Versions";
  if (exists(fw)) {
    const vs = fs.readdirSync(fw).filter((v) => /^3\.\d+$/.test(v)).sort((a, b) => +b.split(".")[1] - +a.split(".")[1]);
    for (const v of vs) { const p = path.join(fw, v, "bin", "python3"); if (exists(p)) return p; }
  }
  for (const p of ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]) if (exists(p)) return p;
  return null;
}
function ytdlp() {
  if (IS_WIN) { const e = path.join(BIN, "yt-dlp.exe"); return exists(e) ? [e] : null; }
  const z = path.join(BIN, "yt-dlp.pyz"), py = python3();
  if (exists(z) && py) return [py, z];
  return exists("/opt/homebrew/bin/yt-dlp") ? ["/opt/homebrew/bin/yt-dlp"] : null;
}
function ffmpegDir() {
  for (const d of [BIN, "/opt/homebrew/bin", "/usr/local/bin"])
    if (exists(path.join(d, IS_WIN ? "ffmpeg.exe" : "ffmpeg"))) return d;
  return null;
}
function loadJSON(p, dflt) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return dflt; } }
function saveJSON(p, o) { try { fs.mkdirSync(DATA, { recursive: true }); fs.writeFileSync(p, JSON.stringify(o)); } catch (e) {} }
function openURL(u) { cs.openURLInDefaultBrowser(u); }
function reveal(p) { IS_WIN ? cp.spawn("explorer", ["/select,", p], { detached: true }) : cp.spawn("open", ["-R", p]); }
function evalHost(code) { return new Promise((res) => cs.evalScript(code, (r) => { try { res(JSON.parse(r)); } catch (e) { res({ ok: false, message: String(r) }); } })); }
const jsStr = (s) => JSON.stringify(String(s));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* -------------------------------------------------------------- platforms */
const PLATFORMS = [
  [["music.youtube.com"], "YouTube Music", "youtubemusic", "music"], [["youtube.com", "youtu.be"], "YouTube", "youtube", "video"],
  [["instagram.com"], "Instagram", "instagram", "video"], [["tiktok.com"], "TikTok", "tiktok", "video"],
  [["twitter.com", "x.com"], "X", "x", "video"], [["facebook.com", "fb.watch"], "Facebook", "facebook", "video"],
  [["vimeo.com"], "Vimeo", "vimeo", "video"], [["soundcloud.com"], "SoundCloud", "soundcloud", "music"],
  [["twitch.tv"], "Twitch", "twitch", "video"], [["spotify.com"], "Spotify", "spotify", "match"],
  [["music.apple.com"], "Apple Music", "applemusic", "match"], [["deezer.com"], "Deezer", "deezer", "match"]];
const SHOWCASE = ["youtube", "instagram", "tiktok", "x", "facebook", "vimeo", "spotify", "applemusic", "youtubemusic", "soundcloud", "deezer"];
function platformOf(url) {
  let host = ""; try { host = new URL(url).hostname.toLowerCase(); } catch (e) {}
  for (const [ds, name, icon, kind] of PLATFORMS) if (ds.some((d) => host === d || host.endsWith("." + d))) return { name, icon, kind };
  return { name: "Web", icon: null, kind: "video" };
}
const linkList = () => [...new Set(($("links").value.match(/https?:\/\/\S+/gi) || []).map((u) => u.replace(/[,;]+$/, "")))];

/* ---------------------------------------------------------------- license */
let machineCache = null;
function machineId() {
  if (machineCache) return machineCache;
  let raw = "";
  try {
    if (IS_WIN) {
      const o = cp.execSync('reg query "HKLM\\SOFTWARE\\Microsoft\\Cryptography" /v MachineGuid', { encoding: "utf8" });
      raw = (o.match(/MachineGuid\s+REG_SZ\s+(\S+)/) || [])[1] || "";
    } else {
      const o = cp.execSync("ioreg -rd1 -c IOPlatformExpertDevice", { encoding: "utf8" });
      raw = (o.match(/"IOPlatformUUID" = "([^"]+)"/) || [])[1] || "";
    }
  } catch (e) {}
  if (!raw) raw = os.hostname();
  // same ID as the DaVinci plugin, so the site shows one computer
  machineCache = crypto.createHash("sha256").update("linkdrop:" + raw.trim().toUpperCase()).digest("hex");
  return machineCache;
}
function machineName() {
  try { return IS_WIN ? (process.env.COMPUTERNAME || "Windows PC") : cp.execSync("scutil --get ComputerName", { encoding: "utf8" }).trim(); }
  catch (e) { return os.hostname(); }
}
function licKey() { return crypto.createHash("sha256").update("linkdrop-premiere:" + machineId()).digest(); }
function loadLicense() {
  try {
    const raw = Buffer.from(fs.readFileSync(LICENSE, "utf8"), "base64");
    const d = crypto.createDecipheriv("aes-256-gcm", licKey(), raw.subarray(0, 12));
    d.setAuthTag(raw.subarray(12, 28));
    return JSON.parse(Buffer.concat([d.update(raw.subarray(28)), d.final()]).toString("utf8"));
  } catch (e) { return {}; }
}
function saveLicense(o) {
  try {
    const iv = crypto.randomBytes(12), c = crypto.createCipheriv("aes-256-gcm", licKey(), iv);
    const body = Buffer.concat([c.update(JSON.stringify(o), "utf8"), c.final()]);
    fs.mkdirSync(DATA, { recursive: true });
    fs.writeFileSync(LICENSE, Buffer.concat([iv, c.getAuthTag(), body]).toString("base64"));
  } catch (e) {}
}
function clearLicense() { try { fs.unlinkSync(LICENSE); } catch (e) {} }
function verifyToken(token) {
  try {
    const [b, s] = token.split(".");
    const body = Buffer.from(b.replace(/-/g, "+").replace(/_/g, "/"), "base64");
    const sig = Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
    if (!crypto.verify("RSA-SHA256", body, PUBLIC_KEY, sig)) return null;
    return JSON.parse(body.toString("utf8"));
  } catch (e) { return null; }
}
function postJSON(url, body, redirects = 3) {
  return new Promise((resolve) => {
    const data = Buffer.from(JSON.stringify(body));
    const req = https.request(url, { method: "POST", timeout: 35000,
      headers: { "Content-Type": "application/json", Accept: "application/json", "Content-Length": data.length } }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects > 0) {
        res.resume(); return resolve(postJSON(new URL(res.headers.location, url).toString(), body, redirects - 1));
      }
      let t = ""; res.setEncoding("utf8"); res.on("data", (c) => (t += c));
      res.on("end", () => { try { resolve(JSON.parse(t)); } catch (e) { resolve(null); } });
    });
    req.on("error", () => resolve(null)); req.on("timeout", () => { req.destroy(); resolve(null); });
    req.end(data);
  });
}
async function api(name, body) {
  for (const base of API) { const r = await postJSON(`${base}/${name}`, body); if (r) return r; }
  return null;
}
const licBody = (key) => ({ key, product: PRODUCT, machine_id: machineId(), machine_name: machineName(),
                             app_version: VERSION, os: IS_WIN ? "Windows" : "macOS" });
function apiMessage(r, fallback) { return (r && (r.message || r.error)) || fallback; }

let LIC = null;
function licState() {
  const d = loadLicense();
  if (!d.mode) return ["none", d];
  if (LOCKED.includes(d.status)) return ["locked", d];
  if (d.expires && Date.now() / 1000 >= d.expires) return ["expired", d];
  if (Date.now() / 1000 - (d.checked || 0) > OFFLINE_GRACE_DAYS * 86400) return ["stale", d];
  return ["ok", d];
}
function acceptLicense(d) { d.checked = Date.now() / 1000; saveLicense(d); LIC = d; refreshTrial(); }
function readAnswer(r, key) {
  if (!r || r.ok !== true || !["active", "trial", undefined].includes(r.status)) return null;
  let signed = null;
  if (r.token) {
    signed = verifyToken(r.token);
    if (!signed || signed.machine !== machineId()) return { error: "The license server's answer couldn't be verified." };
  }
  const exp = r.expires_at ? Date.parse(r.expires_at) / 1000 : (signed && signed.expires) || 0;
  const trial = r.status === "trial" || /^LDT-/.test(key || "") || (signed && signed.kind === "trial");
  return { mode: trial ? "trial" : "paid", key, status: r.status || "active", expires: exp || 0 };
}
async function activate() {
  const key = $("key").value.replace(/\s+/g, "").toUpperCase();
  if (!/^[A-Z0-9]{2,6}(-[A-Z0-9]{4}){2,4}$/.test(key)) return licMsg("That doesn't look like a LinkDrop key (LD-XXXX-XXXX-XXXX).", "err");
  licMsg("Activating... this can take a few seconds."); $("activate").disabled = true;
  const r = await api("license-activate", licBody(key));
  $("activate").disabled = false;
  const d = readAnswer(r, key);
  if (d && !d.error) { acceptLicense(d); licMsg(""); go("links"); }
  else licMsg((d && d.error) || apiMessage(r, "Couldn't reach the LinkDrop server. Check your internet connection."), "err");
}
async function backgroundCheck(purpose) {
  const d = loadLicense();
  const r = await api("license-validate", licBody(d.key || ""));
  if (!r) { if (purpose === "resume") licMsg("Couldn't check your license. Connect to the internet and try again.", "err"); return; }
  const ok = readAnswer(r, d.key);
  if (ok && !ok.error) { acceptLicense(Object.assign(d, ok)); if (page === "license") go("links"); }
  else if (LOCKED.includes(r.status) || r.ok === false) lock(apiMessage(r, "This computer is no longer licensed."));
}
function lock(message) {
  const d = loadLicense(); d.status = "revoked"; saveLicense(d); LIC = null; refreshTrial();
  if (running) pendingLock = message; else { go("license"); licMsg(message, "err"); }
}
function licMsg(t, kind) { $("licmsg").textContent = t; $("licmsg").className = "msg " + (kind || ""); }
function licensed() { return LIC && !LOCKED.includes(LIC.status) && (!LIC.expires || Date.now() / 1000 < LIC.expires); }
function refreshTrial() {
  const el = $("trial");
  const trial = LIC && LIC.mode === "trial";
  el.hidden = !trial;
  if (trial) {
    const left = Math.max(0, LIC.expires - Date.now() / 1000);
    el.textContent = `Trial ${Math.floor(left / 3600)}:${String(Math.floor(left % 3600 / 60)).padStart(2, "0")}:${String(Math.floor(left % 60)).padStart(2, "0")} left`;
    el.classList.toggle("low", left < 3600);
    if (left <= 0) lock("Your free trial ended. Get a license on the LinkDrop site to keep using it.");
  }
  const info = $("licinfo");
  if (LIC && LIC.mode === "paid") {
    const k = (LIC.key || "").split("-"); const masked = k.length > 2 ? [k[0], "••••", "••••", k[k.length - 1]].join("-") : "••••";
    const lifetime = !LIC.expires || LIC.expires - Date.now() / 1000 > 50 * 365 * 86400;
    info.textContent = (lifetime ? "Lifetime license " : "License ") + masked + (lifetime ? "" : "  ·  valid until " + new Date(LIC.expires * 1000).toLocaleDateString());
  } else info.textContent = LIC ? "Free trial on this computer" : "";
  $("deactivate").disabled = !(LIC && LIC.mode === "paid");
}
setInterval(() => { if (LIC && LIC.mode === "trial") refreshTrial(); }, 1000);

/* ------------------------------------------------------------- navigation */
const PAGES = ["license", "links", "analyze", "format", "run", "done", "settings"];
const DOT = { links: 0, analyze: 0, settings: 0, format: 1, run: 3, done: 3 };
let page = "";
function go(p) {
  page = p;
  for (const id of PAGES) $("p-" + id).classList.toggle("on", id === p);
  document.querySelectorAll(".dots i").forEach((d, i) => d.classList.toggle("on", DOT[p] === i));
}

/* ----------------------------------------------------------------- state */
const S = Object.assign({ folder: path.join(HOME, IS_WIN ? "Videos" : "Movies", "Premiere Downloads"),
                          mode: "video", vcodec: "h264", afmt: "wav", quality: "1080", target: "0" }, loadJSON(SETTINGS, {}));
const save = () => saveJSON(SETTINGS, S);
let items = [], running = false, cancelled = false, pendingLock = null, current = null, returnPage = "links";

/* ----------------------------------------------------------------- links */
function refreshLinks() {
  const urls = linkList();
  const icons = [...new Set(urls.map((u) => platformOf(u).icon).filter(Boolean))];
  $("logos").innerHTML = (urls.length ? icons : SHOWCASE).map((i) => `<img src="img/${i}.png">`).join("");
  $("count").textContent = urls.length ? `${urls.length} link${urls.length > 1 ? "s" : ""} ready` : "YouTube, Instagram, TikTok, X, Spotify and 1,000+ more sites";
  $("next0").disabled = !urls.length;
  $("links").rows = Math.min(9, Math.max(3, urls.length + 1));
}
function clipboardText() {
  try { return IS_WIN ? cp.execSync("powershell -NoProfile -Command Get-Clipboard", { encoding: "utf8" }) : cp.execSync("pbpaste", { encoding: "utf8" }); }
  catch (e) { return ""; }
}

/* -------------------------------------------------------------- analyze */
function run(cmd, args, onLine) {
  return new Promise((resolve) => {
    const p = cp.spawn(cmd, args, { env, windowsHide: true });
    current = p;
    let out = "", err = "", buf = "";
    p.stdout.on("data", (c) => { const t = c.toString("utf8"); out += t; if (onLine) { buf += t; const ls = buf.split(/[\r\n]/); buf = ls.pop(); ls.forEach((l) => l.trim() && onLine(l.trim())); } });
    p.stderr.on("data", (c) => (err += c.toString("utf8")));
    p.on("close", (code) => { current = null; resolve({ code, out, err }); });
    p.on("error", (e) => { current = null; resolve({ code: -1, out, err: String(e) }); });
  });
}
const LOGIN_ERROR = /not a bot|Sign in to confirm|confirm your age|cookies-from-browser/i;
const LOGIN_HELP = "YouTube asked to confirm you're not a bot. Sign in to YouTube in Chrome, Firefox or Safari on this computer, then try again.";
const COOKIE_FILE = path.join(DATA, "cookie-browser.txt");
function cookieBrowsers() {
  let found;
  if (IS_WIN) {
    const local = process.env.LOCALAPPDATA || "", roaming = process.env.APPDATA || "";
    found = [["firefox", path.join(roaming, "Mozilla", "Firefox", "Profiles")], ["chrome", path.join(local, "Google", "Chrome", "User Data")],
             ["edge", path.join(local, "Microsoft", "Edge", "User Data")], ["brave", path.join(local, "BraveSoftware", "Brave-Browser", "User Data")]]
      .filter(([, d]) => exists(d)).map(([b]) => b);
  } else {
    found = [["chrome", "Google Chrome"], ["brave", "Brave Browser"], ["edge", "Microsoft Edge"], ["firefox", "Firefox"],
             ["vivaldi", "Vivaldi"], ["opera", "Opera"], ["chromium", "Chromium"]]
      .filter(([, n]) => ["/Applications", path.join(HOME, "Applications")].some((d) => exists(path.join(d, n + ".app")))).map(([b]) => b);
    found.push("safari");
  }
  let last = ""; try { last = fs.readFileSync(COOKIE_FILE, "utf8").trim(); } catch (e) {}
  return (found.includes(last) ? [last] : []).concat(found.filter((b) => b !== last));
}
function rememberCookieBrowser(b) { try { fs.mkdirSync(DATA, { recursive: true }); fs.writeFileSync(COOKIE_FILE, b); } catch (e) {} }
function cleanError(t) {
  const l = ((t || "").split("\n").filter((x) => /ERROR/.test(x)).pop() || t || "").replace(/^ERROR:\s*/, "").replace(/^\[[^\]]+\]\s*[^:\s]+:\s*/, "").trim();
  return (l.charAt(0).toUpperCase() + l.slice(1, 160)) || "This link couldn't be read.";
}
async function songFromLink(url, pl) {
  // Spotify / Apple Music / Deezer are DRM-protected: read "Artist - Title" and find it on YouTube
  const get = (u) => new Promise((res) => https.get(u, { headers: { "User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/126 Safari/537.36" } }, (r) => {
    if (r.statusCode >= 300 && r.headers.location) { r.resume(); return res(get(new URL(r.headers.location, u).toString())); }
    let t = ""; r.setEncoding("utf8"); r.on("data", (c) => (t += c)); r.on("end", () => res(t)); }).on("error", () => res("")));
  try {
    if (pl === "Spotify") {
      const id = (url.match(/track\/([A-Za-z0-9]+)/) || [])[1]; if (!id) return null;
      const t = await get("https://open.spotify.com/embed/track/" + id);
      const e = JSON.parse(t.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/)[1]).props.pageProps.state.data.entity;
      return `${(e.artists || []).slice(0, 2).map((a) => a.name).join(", ")} - ${e.name}`;
    }
    if (pl === "Deezer") {
      const id = (url.match(/track\/(\d+)/) || [])[1]; if (!id) return null;
      const d = JSON.parse(await get("https://api.deezer.com/track/" + id)); return `${d.artist.name} - ${d.title}`;
    }
    const t = await get(url);
    const m = (t.match(/property="og:title"[^>]*content="([^"]+)"/) || [])[1]; if (!m) return null;
    const s = m.replace(/&amp;/g, "&").replace(/\s+on Apple Music$/, ""), by = s.match(/(.*) by (.*)$/);
    return by ? `${by[2]} - ${by[1]}` : s;
  } catch (e) { return null; }
}
async function analyze() {
  if (!licensed()) { go("license"); return licMsg("Activate LinkDrop to download.", "err"); }
  const tool = ytdlp();
  if (!tool) { alert("The download tools aren't installed. Run the LinkDrop installer."); return; }
  items = linkList().map((url) => Object.assign({ url, state: "queued", title: null, info: null, pct: 0 }, { p: platformOf(url) }));
  renderAnalyze(); go("analyze"); $("next1").disabled = true;
  await Promise.all(items.map(async (it) => {
    it.state = "analyzing"; renderAnalyze();
    let target = it.url;
    if (it.p.kind === "match") {
      it.matched = await songFromLink(it.url, it.p.name);
      if (!it.matched) { it.state = "failed"; it.error = `Couldn't read the song from this ${it.p.name} link.`; return renderAnalyze(); }
      target = `ytsearch1:${it.matched} audio`;
    }
    // YouTube may ask to "confirm you're not a bot": then use the YouTube login of a browser on this computer
    let r = await run(tool[0], tool.slice(1).concat(["-J", "--no-playlist", "--no-warnings", target]));
    for (const b of LOGIN_ERROR.test(r.err) ? cookieBrowsers() : []) {
      r = await run(tool[0], tool.slice(1).concat(["-J", "--no-playlist", "--no-warnings", "--cookies-from-browser", b, target]));
      if (r.code === 0) { it.cookies = b; rememberCookieBrowser(b); break; }
      if (!LOGIN_ERROR.test(r.err)) break;
    }
    if (r.code !== 0 && LOGIN_ERROR.test(r.err)) { it.state = "failed"; it.error = LOGIN_HELP; return renderAnalyze(); }
    try {
      let info = JSON.parse(r.out); if (info._type === "playlist") info = (info.entries || [])[0];
      it.info = info; it.title = info.title; it.thumb = info.thumbnail; it.dur = info.duration;
      it.src = info.webpage_url || it.url; it.state = "ready";
    } catch (e) { it.state = "failed"; it.error = cleanError(r.err); }
    renderAnalyze();
  }));
  $("next1").disabled = !items.some((i) => i.state === "ready");
}
function card(it, right, sub, cls, bar) {
  const img = it.thumb ? `<img src="${esc(it.thumb)}">` : (it.p.icon ? `<span class="pf"><img src="img/${it.p.icon}.png"></span>` : "<span></span>");
  return `<div class="card ${cls || ""}">${img}<div><div class="t">${esc(it.title || it.matched || it.p.name + " link")}</div><div class="s">${esc(sub)}</div></div>${right}${bar || ""}</div>`;
}
function fmtDur(s) { if (!s) return ""; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function renderAnalyze() {
  $("alist").innerHTML = items.map((it) => {
    if (it.state === "ready") return card(it, '<span class="pill green">✓ Ready</span>', [it.p.name, fmtDur(it.dur)].filter(Boolean).join(" · "), "ok");
    if (it.state === "failed") return card(it, '<span class="pill rose">✕ Failed</span>', it.error, "err");
    if (it.state === "analyzing") return card(it, '<span class="pill blue">Reading</span>', it.p.name, "active");
    return card(it, '<span class="pill">Queued</span>', it.p.name);
  }).join("");
  const done = items.filter((i) => ["ready", "failed"].includes(i.state)).length;
  $("abar").style.width = (items.length ? (100 * done) / items.length : 0) + "%";
  $("aprog").textContent = `${done} of ${items.length} link${items.length === 1 ? "" : "s"} processed`;
}

/* --------------------------------------------------------------- format */
function refreshFormat() {
  const musicOnly = items.filter((i) => i.state === "ready").every((i) => i.p.kind !== "video");
  if (musicOnly) S.mode = "audio";
  document.querySelectorAll(".seg button").forEach((b) => { b.classList.toggle("on", b.dataset.mode === S.mode); b.disabled = musicOnly && b.dataset.mode === "video"; });
  $("fmt-video").style.display = S.mode === "video" ? "" : "none";
  $("fmt-audio").style.display = S.mode === "audio" ? "" : "none";
  document.querySelectorAll("[data-v]").forEach((b) => b.classList.toggle("on", b.dataset.v === S.vcodec));
  document.querySelectorAll("[data-a]").forEach((b) => b.classList.toggle("on", b.dataset.a === S.afmt));
  const q = $("quality"); q.innerHTML = "";
  const opts = S.mode === "audio" ? (S.afmt === "wav" ? [["best", "Lossless"]] : [["best", "Best"], ["320", "320 kbps"], ["128", "128 kbps"]])
    : [["0", "Best available"], ["2160", "4K"], ["1440", "1440p"], ["1080", "1080p"], ["720", "720p"]];
  for (const [v, l] of opts) q.add(new Option(l, v));
  q.value = opts.some((o) => o[0] === S.quality) ? S.quality : opts[S.mode === "audio" ? 0 : 3][0];
  $("target").value = S.target;
}

/* ------------------------------------------------------------- download */
async function startDownloads() {
  S.quality = $("quality").value; S.target = $("target").value; save();
  const list = items.filter((i) => i.state === "ready");
  list.forEach((i) => { i.state = "queued"; i.pct = 0; i.error = null; i.path = null; });
  running = true; cancelled = false; go("run"); renderRun();
  const tool = ytdlp(), ff = ffmpegDir();
  fs.mkdirSync(S.folder, { recursive: true });
  let cursor = -1, ok = 0;
  for (const it of list) {
    if (cancelled) { it.state = "failed"; it.error = "Canceled."; continue; }
    it.state = "downloading"; it.status = "Getting info"; renderRun();
    const audio = S.mode === "audio" || it.p.kind !== "video";
    const args = tool.slice(1).concat(["--no-playlist", "--newline", "--no-colors", "--progress", "--windows-filenames", "--encoding", "utf-8",
      "-o", path.join(S.folder, "%(title).90B [%(id)s].%(ext)s"), "--print", "after_move:FINAL:%(filepath)s",
      "--progress-template", "download:PROG:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"]);
    if (ff) args.push("--ffmpeg-location", ff);
    if (audio) {
      args.push("-f", "bestaudio/best", "-x", "--audio-format", S.afmt === "aac" ? "m4a" : S.afmt);
      if (S.afmt !== "wav") args.push("--audio-quality", S.quality === "best" ? "0" : S.quality + "K");
    } else {
      const h = +S.quality;
      args.push("-f", "bv*+ba/b", "-S", h ? `res:${h},vcodec:h264,acodec:aac` : "vcodec:h264,acodec:aac", "--merge-output-format", "mp4");
    }
    if (it.cookies) args.push("--cookies-from-browser", it.cookies);
    args.push(it.src || it.url);
    let part = 0;
    const r = await run(tool[0], args, (line) => {
      if (line.startsWith("[download] Destination:")) part++;
      else if (line.startsWith("PROG:")) {
        const [p, sp, eta] = line.slice(5).split("|"); const v = parseFloat(p);
        if (!isNaN(v)) it.pct = Math.max(it.pct, audio ? v * 0.95 : part <= 1 ? v * 0.85 : 85 + v * 0.1);
        it.status = `Downloading · ${(sp || "").trim().replace("MiB/s", "MB/s")} · ${(eta || "").trim()} left`; renderRun();
      } else if (line.startsWith("FINAL:")) it.path = line.slice(6).trim();
      else if (/^\[(Merger|ExtractAudio)\]/.test(line)) { it.status = "Processing"; it.pct = Math.max(it.pct, 96); renderRun(); }
    });
    if (cancelled) { it.state = "failed"; it.error = "Canceled."; continue; }
    if (r.code !== 0 && LOGIN_ERROR.test(r.err || r.out) && !it.retriedLogin) {
      // YouTube started asking for a login after the analysis: try the browsers' logins once, then this item again
      it.retriedLogin = true;
      const b = cookieBrowsers().find((x) => x !== it.cookies);
      if (b) { it.cookies = b; list.splice(list.indexOf(it) + 1, 0, it); it.pct = 0; continue; }
    }
    if (r.code !== 0 && LOGIN_ERROR.test(r.err || r.out)) { it.state = "failed"; it.error = LOGIN_HELP; renderRun(); continue; }
    if (r.code !== 0 || !it.path || !exists(it.path)) { it.state = "failed"; it.error = cleanError(r.err || r.out); renderRun(); continue; }
    if (!audio && S.vcodec === "prores" && ff) {
      it.status = "Converting to ProRes 422"; renderRun();
      const out = it.path.replace(/\.[^.]+$/, ".mov");
      const c = await run(path.join(ff, IS_WIN ? "ffmpeg.exe" : "ffmpeg"), ["-y", "-v", "error", "-i", it.path, "-c:v", "prores_ks", "-profile:v", "2",
        "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le", out]);
      if (c.code === 0) { try { fs.unlinkSync(it.path); } catch (e) {} it.path = out; }
    }
    it.state = "importing"; it.status = "Importing into Premiere"; it.pct = 99; renderRun();
    const res = await evalHost(`ld_place(${jsStr(it.path)}, ${+S.target}, ${cursor})`);
    if (res.ok) { it.state = "done"; it.placed = res.message; ok++; if (res.next >= 0 && +S.target === 0) cursor = res.next; }
    else { it.state = "done"; it.placed = null; it.error = res.message; ok++; }
    it.pct = 100; renderRun();
  }
  running = false;
  if (pendingLock) { const m = pendingLock; pendingLock = null; go("license"); return licMsg(m, "err"); }
  const failed = list.filter((i) => i.state === "failed").length;
  $("donemsg").textContent = `${ok} file${ok === 1 ? "" : "s"} ready in Premiere Pro` + (failed ? ` · ${failed} failed` : "");
  ok ? go("done") : (go("analyze"), renderAnalyze());
}
function renderRun() {
  const list = items.filter((i) => i.state !== "ready" || running);
  $("dlist").innerHTML = list.filter((i) => i.state !== "ready").map((it) => {
    const bar = `<div class="bar"><b style="width:${it.pct}%"></b></div>`;
    if (it.state === "done") return card(it, `<span class="pill green">✓ ${it.placed ? "Imported" : "Saved"}</span>`, it.placed || it.error || "", "ok", bar);
    if (it.state === "failed") return card(it, '<span class="pill rose">✕ Failed</span>', it.error || "", "err", bar);
    if (it.state === "queued") return card(it, '<span class="pill">Queued</span>', S.mode === "audio" ? S.afmt.toUpperCase() : "MP4", "", bar);
    return card(it, `<span class="pill blue">${it.pct > 0 ? Math.floor(it.pct) + "%" : "Getting info"}</span>`, it.status || "", "active", bar);
  }).join("");
  const all = items.filter((i) => ["queued", "downloading", "importing", "done", "failed"].includes(i.state));
  const pct = all.length ? all.reduce((a, i) => a + (i.state === "failed" ? 100 : i.pct), 0) / all.length : 0;
  $("pct").textContent = Math.floor(pct); $("obar").style.width = pct + "%";
  $("files").textContent = `${all.filter((i) => ["done", "failed"].includes(i.state)).length} of ${all.length} files`;
  const cur = all.find((i) => ["downloading", "importing"].includes(i.state)); $("status").textContent = cur ? cur.status || "" : "";
}

/* ---------------------------------------------------------------- events */
$("links").addEventListener("input", refreshLinks);
$("paste").onclick = () => { const u = (clipboardText().match(/https?:\/\/\S+/gi) || []); if (u.length) { $("links").value = ($("links").value.trim() + "\n" + u.join("\n")).trim(); refreshLinks(); } };
$("next0").onclick = analyze;
$("back1").onclick = () => go("links");
$("next1").onclick = () => { refreshFormat(); go("format"); };
document.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => { S.mode = b.dataset.mode; refreshFormat(); save(); }));
document.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => { S.vcodec = b.dataset.v; refreshFormat(); save(); }));
document.querySelectorAll("[data-a]").forEach((b) => (b.onclick = () => { S.afmt = b.dataset.a; refreshFormat(); save(); }));
$("back2").onclick = () => go("analyze");
$("start").onclick = startDownloads;
$("cancel").onclick = () => { cancelled = true; if (current) current.kill(); };
$("donebtn").onclick = () => { $("links").value = ""; refreshLinks(); go("links"); };
$("openfolder").onclick = () => { const f = items.filter((i) => i.path).pop(); f ? reveal(f.path) : reveal(S.folder); };
$("opensettings").onclick = () => { returnPage = page; $("folder").textContent = S.folder; refreshTrial(); go("settings"); };
$("settingsdone").onclick = () => go(returnPage === "settings" ? "links" : returnPage);
$("browse").onclick = $("footfolder").onclick = () => {
  const r = window.cep.fs.showOpenDialogEx(false, true, "Choose where LinkDrop saves files", S.folder);
  if (r && r.data && r.data[0]) { S.folder = r.data[0]; save(); $("folder").textContent = S.folder; $("footfolder").title = S.folder; }
};
$("deactivate").onclick = async () => {
  if (!LIC || running) return;
  const r = await api("license-deactivate", { key: LIC.key, product: PRODUCT, machine_id: machineId() });
  if (r && r.ok) { clearLicense(); LIC = null; refreshTrial(); go("license"); licMsg("This computer was removed from your license.", "ok"); }
  else $("licinfo").textContent = apiMessage(r, "Couldn't remove this computer. Try again.");
};
$("activate").onclick = activate;
$("key").addEventListener("keydown", (e) => e.key === "Enter" && activate());
$("getkey").onclick = $("trial").onclick = () => openURL(SITE + "/account");
$("insta").onclick = () => openURL(INSTAGRAM);

/* ------------------------------------------------------------- self-update */
// Every time the panel opens it compares its version with premiere/ on GitHub (main) and, when GitHub is newer,
// copies the new files over this extension and reloads. Skipped in development (.debug present or symlinked folder).
const REPO_RAW = "https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main/premiere";
const REPO_ZIP = "https://codeload.github.com/gabrielxreis/LinkDrop/zip/refs/heads/main";
function download(url, redirects = 5) {
  return new Promise((resolve) => {
    https.get(url, { timeout: 30000, headers: { "User-Agent": "LinkDrop-Premiere/" + VERSION } }, (r) => {
      if (r.statusCode >= 300 && r.statusCode < 400 && r.headers.location && redirects > 0) {
        r.resume(); return resolve(download(new URL(r.headers.location, url).toString(), redirects - 1));
      }
      if (r.statusCode !== 200) { r.resume(); return resolve(null); }
      const parts = []; r.on("data", (c) => parts.push(c)); r.on("end", () => resolve(Buffer.concat(parts)));
    }).on("error", () => resolve(null)).on("timeout", function () { this.destroy(); resolve(null); });
  });
}
function newer(a, b) {
  const x = a.split(".").map(Number), y = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); }
  return false;
}
function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const n of fs.readdirSync(src)) {
    if (n === ".debug" || n === ".DS_Store") continue;
    const a = path.join(src, n), b = path.join(dst, n);
    if (fs.statSync(a).isDirectory()) copyDir(a, b); else fs.writeFileSync(b, fs.readFileSync(a));
  }
}
async function selfUpdate() {
  try {
    const ext = cs.getSystemPath(SystemPath.EXTENSION);
    if (!ext || exists(path.join(ext, ".debug")) || fs.lstatSync(ext).isSymbolicLink()) return;
    const man = await download(`${REPO_RAW}/CSXS/manifest.xml?t=${Date.now()}`);
    const latest = man && (man.toString("utf8").match(/ExtensionBundleVersion="([^"]+)"/) || [])[1];
    if (!latest || !newer(latest, VERSION) || running) return;
    const zip = await download(REPO_ZIP);
    if (!zip || running) return;
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "linkdrop-upd-")), zf = path.join(tmp, "repo.zip");
    fs.writeFileSync(zf, zip);
    const r = IS_WIN
      ? await run("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
          `Expand-Archive -LiteralPath '${zf}' -DestinationPath '${path.join(tmp, "x")}' -Force`])
      : await run("/usr/bin/ditto", ["-x", "-k", zf, path.join(tmp, "x")]);
    if (r.code !== 0) return;
    const root = fs.readdirSync(path.join(tmp, "x")).map((d) => path.join(tmp, "x", d, "premiere"))
      .find((d) => exists(path.join(d, "CSXS", "manifest.xml")));
    if (!root || running) return;
    copyDir(root, ext);
    fs.rmSync ? fs.rmSync(tmp, { recursive: true, force: true }) : null;
    // the ExtendScript side is loaded once per panel start: load the new one, then reload the page
    cs.evalScript(`$.evalFile(${JSON.stringify(path.join(ext, "host", "linkdrop.jsx").replace(/\\/g, "/"))})`, () => location.reload());
  } catch (e) { /* offline or no permission: keep the installed version */ }
}

/* ----------------------------------------------------------------- start */
$("ver").textContent = "v" + VERSION;
$("footfolder").title = S.folder;
refreshLinks();
const [st, data] = licState();
if (st === "ok") { LIC = data; refreshTrial(); go("links"); backgroundCheck("background"); }
else {
  go("license");
  if (st === "stale") { licMsg("Checking your license..."); backgroundCheck("resume"); }
  else if (st === "expired") licMsg(data.mode === "trial" ? "Your free trial ended. Get a license to keep using LinkDrop." : "Your license has expired. Renew it on the site.", "err");
  else if (st === "locked") licMsg("This computer is no longer licensed. Enter a key to continue.", "err");
}
const clip = (clipboardText().match(/https?:\/\/\S+/gi) || []);
if (clip.length) { $("links").value = [...new Set(clip)].join("\n"); refreshLinks(); }
selfUpdate();
