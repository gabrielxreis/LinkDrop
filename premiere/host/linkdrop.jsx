// LinkDrop for Premiere Pro: ExtendScript side (import into the project and place on the active sequence).
// Called from the panel with CSInterface.evalScript; every function returns a JSON string.

function ld_json(o) {
    // ExtendScript has no JSON object in every host: minimal serializer for flat objects
    var parts = [];
    for (var k in o) {
        if (!o.hasOwnProperty(k)) continue;
        var v = o[k];
        var s = (v === null || v === undefined) ? "null"
            : (typeof v === "number" || typeof v === "boolean") ? String(v)
            : '"' + String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n") + '"';
        parts.push('"' + k + '":' + s);
    }
    return "{" + parts.join(",") + "}";
}

function ld_status() {
    var p = app.project;
    var seq = p ? p.activeSequence : null;
    return ld_json({ project: p && p.name ? p.name : null, sequence: seq ? seq.name : null });
}

function ld_bin(name) {
    var root = app.project.rootItem;
    for (var i = 0; i < root.children.numItems; i++) {
        var c = root.children[i];
        if (c && c.type === ProjectItemType.BIN && c.name === name) return c;
    }
    return root.createBin(name);
}

function ld_findItem(bin, path) {
    var want = path.replace(/\\/g, "/").toLowerCase();
    for (var i = bin.children.numItems - 1; i >= 0; i--) {
        var c = bin.children[i];
        try {
            if (c && c.getMediaPath && c.getMediaPath().replace(/\\/g, "/").toLowerCase() === want) return c;
        } catch (e) {}
    }
    return null;
}

function ld_trackFree(track, startSec, endSec) {
    for (var i = 0; i < track.clips.numItems; i++) {
        var c = track.clips[i];
        if (c.start.seconds < endSec && c.end.seconds > startSec) return false;
    }
    return true;
}

// target: 0 playhead (free tracks, nothing overwritten) | 1 end of sequence | 2 new sequence | 3 project only
// cursorSec: when >= 0, place at this time instead of the playhead (keeps batch items back to back)
function ld_place(path, target, cursorSec) {
    try {
        var p = app.project;
        if (!p) return ld_json({ ok: false, message: "Open a project in Premiere Pro first." });
        var bin = ld_bin("LinkDrop Downloads");
        if (!p.importFiles([path], true, bin, false)) {
            return ld_json({ ok: false, message: "Premiere couldn't import this file. It's saved in your folder." });
        }
        var item = ld_findItem(bin, path);
        if (!item) return ld_json({ ok: false, message: "Imported, but the clip wasn't found in the project." });
        if (target === 3) return ld_json({ ok: true, message: "Imported into the project (LinkDrop Downloads).", next: -1 });

        var seq = p.activeSequence;
        if (target === 2 || !seq) {
            var name = item.name.replace(/\.[^.]+$/, "");
            p.createNewSequenceFromClips(name, [item], bin);
            return ld_json({ ok: true, message: "New sequence created.", next: -1 });
        }

        var dur = 0;
        try { dur = item.getOutPoint().seconds - item.getInPoint().seconds; } catch (e) {}
        if (!(dur > 0)) dur = 600;
        var at;
        if (target === 1) {
            at = seq.end ? Number(seq.end) / 254016000000 : 0;
        } else {
            at = cursorSec >= 0 ? cursorSec : seq.getPlayerPosition().seconds;
        }
        var isAudioOnly = /\.(wav|mp3|m4a|aac|flac)$/i.test(path);

        // first track index whose video AND audio tracks are both free in [at, at+dur): nothing gets overwritten
        var vt = seq.videoTracks, at_ = seq.audioTracks, idx = -1;
        var n = Math.max(vt.numTracks, at_.numTracks);
        for (var i = 0; i < n; i++) {
            var vOk = isAudioOnly || (i < vt.numTracks && ld_trackFree(vt[i], at, at + dur));
            var aOk = i < at_.numTracks && ld_trackFree(at_[i], at, at + dur);
            if (vOk && aOk) { idx = i; break; }
        }
        if (idx < 0) {
            app.enableQE();
            var qs = qe.project.getActiveSequence();
            // addTracks(videoCount, videoIndex, audioCount, audioType 1=stereo, audioIndex)
            qs.addTracks(isAudioOnly ? 0 : 1, vt.numTracks, 1, 1, at_.numTracks);
            seq = app.project.activeSequence;
            vt = seq.videoTracks; at_ = seq.audioTracks;
            idx = isAudioOnly ? at_.numTracks - 1 : vt.numTracks - 1;
        }
        if (isAudioOnly) at_[idx].overwriteClip(item, at);
        else vt[idx].overwriteClip(item, at);
        return ld_json({ ok: true, message: target === 1 ? "Added to the end of the sequence." : "Placed at the playhead on a free track.",
                         next: at + dur });
    } catch (err) {
        return ld_json({ ok: false, message: "Premiere error: " + err.toString() });
    }
}
