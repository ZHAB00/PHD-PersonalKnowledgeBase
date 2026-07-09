(function() {
  "use strict";

  var state = {
    sessionId: localStorage.getItem("kb_session_id") || "",
    tenantId: "default",
    chatKbId: localStorage.getItem("kb_chat_kb_id") || "default",
    docKbId: localStorage.getItem("kb_doc_kb_id") || "default",
    userId: localStorage.getItem("kb_user_id") || "default",
    streaming: false
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function gid() { return "sess_" + Date.now() + "_" + Math.random().toString(36).substr(2, 9); }
  function gsl() { try { return JSON.parse(localStorage.getItem("kb_sessions") || "[]"); } catch(e) { return []; } }
  function ssl(l) { localStorage.setItem("kb_sessions", JSON.stringify(l)); }

  function uts(sid) {
    var l = gsl(), f = l.find(function(s) { return s.id === sid; });
    if (f) f.updated = Date.now(); else l.push({ id: sid, created: Date.now(), updated: Date.now(), label: "" });
    if (l.length > 100) l = l.slice(-100);
    ssl(l); rsl();
  }

  function rsl() {
    var c = $("sessionItems"); if (!c) return;
    var list = gsl();
    list.sort(function(a,b) { return (b.updated||0) - (a.updated||0); });
    c.innerHTML = list.map(function(s) {
      var act = s.id === state.sessionId ? " active" : "";
      var lb = s.label || s.id.replace("sess_","").substring(0,8);
      return '<div class="session-item' + act + '" data-sid="' + s.id + '">' +
        '<span class="session-item-title">' + esc(lb) + '</span>' +
        '<button class="session-item-del" data-sid="' + s.id + '">&times;</button></div>';
    }).join("");
    c.querySelectorAll(".session-item").forEach(function(el) {
      el.addEventListener("click", function(ev) {
        if (ev.target.classList.contains("session-item-del")) return;
        sws(el.dataset.sid);
      });
    });
    c.querySelectorAll(".session-item-del").forEach(function(b) {
      b.addEventListener("click", function(ev) { ev.stopPropagation(); dls(b.dataset.sid); });
    });
  }

  function sws(sid) {
    state.sessionId = sid; localStorage.setItem("kb_session_id", sid);
    rsl(); lch(); scv();
  }

  function dls(sid) {
    fetch("/api/chat/clear/" + sid, { method: "POST" }).catch(function(){});
    var l = gsl().filter(function(s) { return s.id !== sid; }); ssl(l);
    if (sid === state.sessionId) {
      state.sessionId = l.length > 0 ? l[0].id : gid();
      localStorage.setItem("kb_session_id", state.sessionId);
      lch();
    }
    rsl();
  }

  function sns() {
    state.sessionId = gid(); localStorage.setItem("kb_session_id", state.sessionId);
    $("chatMessages").innerHTML = "";
    $("sourcesList").innerHTML = '<p class="sources-empty">-</p>';
    uts(state.sessionId); rsl(); scv(); $("chatInput").focus();
  }

  function scv() {
    var vd = $("view-documents"); if (vd) vd.classList.remove("active");
    var vc = $("view-chat"); if (vc) vc.classList.add("active");
    var nd = $("navDocuments"); if (nd) nd.classList.remove("active");
  }
  function sdv() {
    var vc = $("view-chat"); if (vc) vc.classList.remove("active");
    var vd = $("view-documents"); if (vd) vd.classList.add("active");
    var nd = $("navDocuments"); if (nd) nd.classList.add("active");
    rdl(); lkl();
  }

  function stb() { var cm = $("chatMessages"); if (cm) cm.scrollTop = cm.scrollHeight; }

  function ams(role, txt) {
    var d = document.createElement("div");
    d.className = "message " + role;
    d.innerHTML = '<div class="message-avatar">' + (role === "assistant" ? "AI" : "Me") +
      '</div><div class="message-content"><p></p></div>';
    d.querySelector("p").textContent = txt;
    $("chatMessages").appendChild(d);
    stb();
  }

  function atb(name) {
    var d = document.createElement("div");
    d.className = "message-tool";
    d.innerHTML = '<span class="tool-icon">&#9881;</span>' +
      '<span class="tool-label">[Tool] ' + esc(name) + '</span>';
    $("chatMessages").appendChild(d);
    stb();
  }

  async function sdm() {
    var inp = $("chatInput"); var txt = inp.value.trim();
    if (!txt || state.streaming) return;
    inp.value = ""; inp.style.height = "auto";
    ams("user", txt);

    var l = gsl(); var f = l.find(function(s) { return s.id === state.sessionId; });
    if (f && !f.label) {
      fetch("/api/chat/title", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({session_id: state.sessionId, message: txt})
      }).then(function(r){ return r.json(); }).then(function(d){
        if (d && d.title) { f.label = d.title; ssl(l); rsl(); }
      }).catch(function(){});
    }

    var ai = document.createElement("div");
    ai.className = "message assistant";
    ai.innerHTML = '<div class="message-avatar">AI</div><div class="message-content"><p class="stream-text">...</p></div>';
    $("chatMessages").appendChild(ai);
    var st = ai.querySelector(".stream-text");
    state.streaming = true;
    var sb = $("sendBtn"); if (sb) sb.disabled = true;

    try {
      var p = new URLSearchParams();
      p.append("session_id", state.sessionId); p.append("message", txt);
      p.append("kb_id", state.chatKbId); p.append("tenant_id", state.tenantId);
      p.append("user_id", state.userId); p.append("top_k", "5");
      p.append("rerank_strategy", "none");

      var resp = await fetch("/api/chat/stream", { method: "POST", body: p });
      if (!resp.ok) throw new Error("HTTP " + resp.status);

      var reader = resp.body.getReader();
      var dec = new TextDecoder("utf-8");
      var buf = "", full = "";

      while (true) {
        var result = await reader.read();
        var chunk = result.done ? dec.decode() : dec.decode(result.value, {stream:true});
        buf += chunk;
        var lines = buf.split("\n");
        buf = result.done ? "" : (lines.pop() || "");
        for (var i = 0; i < lines.length; i++) {
          var ln = lines[i];
          if (!ln.startsWith("data: ")) continue;
          var dt = ln.slice(6);
          if (dt === "[DONE]") break;
          if (dt.startsWith("__TOOL_CALL__:")) {
            try { var tc = JSON.parse(dt.slice(14)); atb(tc.name); } catch(e) {}
            continue;
          }
          if (dt.startsWith("__TOOL_RESULT__:") || dt.startsWith("__SOURCES__:")) continue;
          full += dt; st.textContent = full; stb();
        }
        if (result.done) break;
      }
      if (!full) st.textContent = "(no response)";
      uts(state.sessionId);
    } catch(e) {
      st.textContent = "Error: " + e.message;
    } finally {
      state.streaming = false; if (sb) sb.disabled = false;
    }
  }

  async function lch() {
    var cm = $("chatMessages"); if (!cm) return;
    try {
      var resp = await fetch("/api/chat/history/" + state.sessionId);
      if (!resp.ok) { cm.innerHTML = ""; return; }
      var data = await resp.json();
      cm.innerHTML = "";
      (data.history || []).forEach(function(m) {
        if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
          m.tool_calls.forEach(function(tc) { atb(tc.tool_name); });
        }
        ams(m.role === "user" ? "user" : "assistant", m.content || "");
      });
      stb();
    } catch(e) { cm.innerHTML = ""; }
  }

  async function lkl() {
    try {
      var resp = await fetch("/api/kb/list?tenant_id=default");
      var data = await resp.json();
      var cks = $("chatKbSelect");
      if (cks) {
        cks.innerHTML = "";
        (data || []).forEach(function(kb) {
          var o = document.createElement("option");
          o.value = kb.id; o.textContent = kb.name + " (" + (kb.doc_count||0) + ")";
          if (kb.id === state.chatKbId) o.selected = true;
          cks.appendChild(o);
        });
      }
      var dks = $("kbSelect");
      if (dks) {
        dks.innerHTML = "";
        (data || []).forEach(function(kb) {
          var o = document.createElement("option");
          o.value = kb.id; o.textContent = kb.name + " (" + (kb.doc_count||0) + ")";
          if (kb.id === state.docKbId) o.selected = true;
          dks.appendChild(o);
        });
      }
      updateKbButtons();
    } catch(e) {}
  }

  function updateKbButtons() {
    var bd = $("btnKbDel"), be = $("btnKbEdit");
    if (bd) bd.style.display = state.docKbId === "default" ? "none" : "";
    if (be) be.style.display = state.docKbId === "default" ? "none" : "";
  }

  function swChatKb(kid) {
    state.chatKbId = kid;
    localStorage.setItem("kb_chat_kb_id", kid);
    var ck = $("chatKbSelect"); if (ck) ck.value = kid;
  }

  function swDocKb(kid) {
    state.docKbId = kid;
    localStorage.setItem("kb_doc_kb_id", kid);
    var dk = $("kbSelect"); if (dk) dk.value = kid;
    updateKbButtons();
    rdl();
  }

  async function rdl() {
    try {
      var url = "/api/documents/list?kb_id=" + encodeURIComponent(state.docKbId) + "&tenant_id=" + state.tenantId;
      var resp = await fetch(url);
      var data = await resp.json();
      var docs = data.documents || [];
      var dc = $("docCount"); if (dc) dc.textContent = docs.length + " docs";
      var di = $("docItems");
      if (di) {
        di.innerHTML = docs.map(function(d) {
          var st = d.status || "ready";
          var isOrphan = (d.id||"").startsWith("orphan_");
          var riBtn = isOrphan ? '<button class="btn-reindex" data-fn="' + esc(d.filename) + '">Reindex</button>' : "";
          return '<div class="doc-item"><span class="doc-item-name">' + esc(d.filename) +
            '</span><span class="doc-item-status">' + esc(st) +
            '</span>' + riBtn +
            '<button class="btn-del-doc" data-id="' + d.id + '">Del</button></div>';
        }).join("");
        di.querySelectorAll(".btn-del-doc").forEach(function(b) {
          b.addEventListener("click", function() { dld(b.dataset.id); });
        });
        di.querySelectorAll(".btn-reindex").forEach(function(b) {
          b.addEventListener("click", function() { rdx(b.dataset.fn); });
        });
      }
    } catch(e) {}
  }

  async function rdx(fn) {
    try {
      await fetch("/api/documents/reindex/" + encodeURIComponent(fn) + "?kb_id=" + state.docKbId);
      rdl(); lkl();
    } catch(e) {}
  }

  async function dld(did) {
    if (!confirm("Delete this document?")) return;
    try {
      await fetch("/api/documents/" + did + "?kb_id=" + state.docKbId + "&tenant_id=" + state.tenantId, { method: "DELETE" });
      rdl(); lkl();
    } catch(e) { alert("Delete failed: " + e.message); }
  }

  async function upl(files) {
    var fl = Array.from(files);
    for (var i = 0; i < fl.length; i++) {
      var fd = new FormData();
      fd.append("file", fl[i]); fd.append("kb_id", state.docKbId); fd.append("tenant_id", state.tenantId);
      try {
        var resp = await fetch("/api/documents/upload", { method: "POST", body: fd });
        if (resp.ok) {
          var rj = await resp.json();
          for (var j = 0; j < 30; j++) {
            await new Promise(function(r) { setTimeout(r, 1500); });
            var sr = await fetch("/api/documents/status/" + rj.task_id + "?kb_id=" + state.docKbId);
            var st = await sr.json();
            if (st.status === "ready" || st.status === "failed") break;
          }
        }
      } catch(e) {}
    }
    rdl(); lkl();
  }

  async function ckb() {
    var nm = $("kbName").value.trim(); if (!nm) return;
    try {
      var resp = await fetch("/api/kb/create", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({name: nm, description: $("kbDesc").value.trim(), tenant_id: state.tenantId})
      });
      if (resp.ok) { $("kbModal").style.display = "none"; await lkl(); }
    } catch(e) {}
  }

  async function ekb() {
    if (state.docKbId === "default") return;
    $("kbModalTitle").textContent = "Edit KB";
    var sel = $("kbSelect");
    var opt = sel && sel.selectedOptions[0];
    $("kbName").value = opt ? opt.textContent.split(" (")[0] : "";
    $("kbDesc").value = "";
    $("kbModal").style.display = "flex";
    var cb = $("kbCreate");
    cb.textContent = "Save";
    var oldClick = cb.onclick;
    cb.onclick = async function() {
      var nm = $("kbName").value.trim(); if (!nm) return;
      try {
        var resp = await fetch("/api/kb/" + state.docKbId, {
          method: "PUT", headers: {"Content-Type":"application/json"},
          body: JSON.stringify({name: nm, description: $("kbDesc").value.trim()})
        });
        if (resp.ok) { $("kbModal").style.display = "none"; await lkl(); }
      } catch(e) {}
      cb.textContent = "Create"; cb.onclick = oldClick;
    };
  }

  async function dkb() {
    if (state.docKbId === "default") return;
    if ($("kbDelCode").value.trim() !== "A1B2C3D4") {
      var h = $("kbDelHint"); if (h) { h.textContent = "Type A1B2C3D4 to confirm"; h.style.display = "block"; }
      return;
    }
    try {
      var resp = await fetch("/api/kb/" + state.docKbId + "?confirmation=A1B2C3D4&tenant_id=" + state.tenantId, { method: "DELETE" });
      if (resp.ok) { $("kbDelModal").style.display = "none"; state.docKbId = "default"; localStorage.setItem("kb_doc_kb_id", "default"); await lkl(); rdl(); }
    } catch(e) {}
  }

  async function chk() {
    try {
      var resp = await fetch("/health");
      if (resp.ok) {
        $("statusDot").classList.add("connected");
        $("statusText").textContent = "Connected";
        var ci = $("chatInput"); if (ci) ci.disabled = false;
        var sb = $("sendBtn"); if (sb) sb.disabled = false;
      }
    } catch(e) {}
  }

  function on(id, evt, fn) { var el = $(id); if (el) el.addEventListener(evt, fn); }

  on("btnNewChat", "click", sns);
  on("navDocuments", "click", sdv);
  on("btnLogout", "click", function() {
    localStorage.removeItem("kb_token"); localStorage.removeItem("kb_username");
    localStorage.removeItem("kb_user_id"); window.location.href = "/login";
  });
  on("sendBtn", "click", sdm);
  on("chatInput", "keydown", function(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sdm(); } });
  on("chatInput", "input", function() { var ci = $("chatInput"); ci.style.height = "auto"; ci.style.height = Math.min(ci.scrollHeight, 120) + "px"; });
  on("chatKbSelect", "change", function() { swChatKb($("chatKbSelect").value); });
  on("kbSelect", "change", function() { swDocKb($("kbSelect").value); });
  on("uploadZone", "click", function(e) { if (e.target.tagName !== "A") { var fi = $("fileInput"); if (fi) fi.click(); } });
  on("uploadZone", "dragover", function(e) { e.preventDefault(); });
  on("uploadZone", "drop", function(e) { e.preventDefault(); if (e.dataTransfer.files.length) upl(e.dataTransfer.files); });
  on("fileInput", "change", function() { var fi = $("fileInput"); if (fi.files.length) upl(fi.files); fi.value = ""; });
  on("btnKbAdd", "click", function() { $("kbModalTitle").textContent = "New KB"; $("kbName").value = ""; $("kbDesc").value = ""; var cb = $("kbCreate"); if(cb) cb.textContent = "Create"; $("kbModal").style.display = "flex"; });
  on("btnKbEdit", "click", ekb);
  on("btnKbDel", "click", function() { $("kbDelCode").value = ""; var h = $("kbDelHint"); if(h) h.style.display = "none"; $("kbDelModal").style.display = "flex"; });
  on("kbCancel", "click", function() { $("kbModal").style.display = "none"; });
  on("kbCreate", "click", ckb);
  on("kbDelCancel", "click", function() { $("kbDelModal").style.display = "none"; });
  on("kbDelOk", "click", dkb);

  chk();
  setInterval(chk, 30000);

  if (!state.sessionId) { state.sessionId = gid(); localStorage.setItem("kb_session_id", state.sessionId); }
  uts(state.sessionId); rsl();

  lkl().then(function() { rdl(); lch(); });
  setInterval(function() { rdl(); lkl(); }, 30000);

})();
