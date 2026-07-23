(function() {
  "use strict";

  var state = {
    sessionId: localStorage.getItem("kb_session_id") || "",
    tenantId: "default",
    graphRagEnabled: true,
    chatKbId: localStorage.getItem("kb_chat_kb_id") || "default",
    docKbId: localStorage.getItem("kb_doc_kb_id") || "default",
    userId: localStorage.getItem("kb_user_id") || "default",
    streaming: false,
    _rdlAbort: null,
    _rdlTimer: null,
    _lklAbort: null
  }
  window.toggleGraphRag = toggleGraphRag;

  function $(id) { return document.getElementById(id); }
  function esc(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function stripMd(s) { return (s||"").replace(/[#*_`~>|]/g, "").trim(); }
  function renderSources(srcs) {
    console.log("renderSources called, args:", srcs ? srcs.length : "null/undefined");
    var sl = $("sourcesList"); if (!sl) return;
    if (!srcs || !srcs.length) { sl.innerHTML = '<p class="sources-empty">未找到相关文档</p>'; return; }
    sl.innerHTML = srcs.map(function(s) {
      return '<div class="source-item"><div class="source-file">' + esc(s.filename) + '</div><div class="source-score">' + (s.score*100).toFixed(0) + '%</div><div class="source-text">' + esc(s.content) + '</div></div>';
    }).join("");
  }
  function gid() { return "sess_" + Date.now() + "_" + Math.random().toString(36).substr(2, 9); }
  function gsl() { try { return JSON.parse(localStorage.getItem("kb_sessions") || "[]"); } catch(e) { return []; } }
  function ssl(l) { localStorage.setItem("kb_sessions", JSON.stringify(l)); }

  function uts(sid) {
    var l = gsl(), f = l.find(function(s) { return s.id === sid; });
    if (f) f.updated = Date.now(); else { l.push({ id: sid, created: Date.now(), updated: Date.now(), label: "" }); fetch("/api/chat/sessions/save", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({id: sid, title: "", user_id: "default"}) }).catch(function(){}); }
    if (l.length > 100) l = l.slice(-100);
    ssl(l); rsl();
  }


  function edt(sid) {
    var el = document.querySelector('.session-item-title[data-sid="' + sid + '"]');
    if (!el || el.querySelector("input")) return;
    var orig = el.textContent;
    el.innerHTML = '<input class="session-item-input" value="' + orig.replace(/"/g, '&quot;') + '" style="width:' + (el.offsetWidth + 20) + 'px">';
    var inp = el.querySelector("input");
    inp.focus(); inp.select();
    function commit(save) {
      var newTitle = inp.value.trim();
      if (!save || !newTitle || newTitle === orig) { el.textContent = orig; return; }
      el.textContent = newTitle;
      var l = gsl(); var f = l.find(function(s) { return s.id === sid; });
      if (f) { f.label = newTitle; f.updated = Date.now(); ssl(l); }
      fetch("/api/chat/sessions/save", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({id: sid, title: newTitle, user_id: "default"})
      }).catch(function(){});
    }
    inp.addEventListener("keydown", function(ev) { if (ev.key === "Enter") { commit(true); } else if (ev.key === "Escape") { commit(false); } });
    inp.addEventListener("blur", function() { commit(true); });
  }

  function rsl() {
    var c = $("sessionItems"); if (!c) return;
    var list = gsl();
    list.sort(function(a,b) { return (b.updated||0) - (a.updated||0); });
    c.innerHTML = list.map(function(s) {
      var act = s.id === state.sessionId ? " active" : "";
      var lb = stripMd(s.label || s.title || "新建对话");
      return '<div class="session-item' + act + '" data-sid="' + s.id + '">' +
        '<span class="session-item-title" data-sid="' + s.id + '">' + esc(lb) + '</span>' +
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
    c.querySelectorAll(".session-item-title").forEach(function(sp) {
      sp.addEventListener("dblclick", function(ev) { ev.stopPropagation(); edt(sp.closest(".session-item").dataset.sid); });
    });
  }

  function sws(sid) {
    if (state.abortController) { state.abortController.abort(); }
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
    $("chatMessages").innerHTML = '<div class="chat-empty"><div class="chat-empty-icon">💬</div><p>开始一段新的对话吧</p><p class="chat-empty-hint">在下方输入你的问题，AI 会基于知识库为你解答</p></div>';
    $("sourcesList").innerHTML = '<p class="sources-empty">-</p>';
    uts(state.sessionId); rsl(); scv(); $("chatInput").focus();
  }

  function scv() { switchView("chat"); }
  function switchView(name) {
    // Hide all views
    var views = document.querySelectorAll(".view");
    views.forEach(function(v) { v.classList.remove("active"); });
    var target = document.getElementById("view-" + name);
    if (target) target.classList.add("active");
    // Highlight nav
    var navs = document.querySelectorAll(".nav-item-side");
    navs.forEach(function(n) { n.classList.remove("active"); });
    if (name === "chat") {
      var nc = document.getElementById("navChat"); if (nc) nc.classList.add("active");
    } else if (name === "documents") {
      var nd = document.getElementById("navDocuments"); if (nd) nd.classList.add("active");
      rdl(); lkl();
    } else if (name === "graph") {
      var ng = document.getElementById("navGraph"); if (ng) ng.classList.add("active");
    }
    // Sources panel: only visible in chat view
    var sp = document.getElementById("sourcesPanel");
    if (sp) sp.style.display = (name === "chat") ? "" : "none";
  }

  function sdv() {
    var vc = $("view-chat"); if (vc) vc.classList.remove("active");
    var vd = $("view-documents"); if (vd) vd.classList.add("active");
    var nd = $("navDocuments"); if (nd) nd.classList.add("active");
    rdl(); lkl();
  }


  function toggleGraphRag() {
    state.graphRagEnabled = !state.graphRagEnabled;
    var btn = document.getElementById("graphRagToggle");
    if (btn) {
      btn.classList.toggle("active", state.graphRagEnabled);
    }
  }

  function stb() { var cm = $("chatMessages"); if (cm) cm.scrollTop = cm.scrollHeight; }


  function rmd(el, text) {
    try {
      el.innerHTML = marked.parse(text);
      try { renderMathInElement(el, { delimiters: [{left: "$\$", right: "$\$", display: true}, {left: "$", right: "$", display: false}] }); } catch(e) {}
    } catch(e) { el.textContent = text; }
  }
  function makeMsg(role, txt) {
    var d = document.createElement("div");
    d.className = "message " + role;
    d.innerHTML = '<div class="message-avatar">' + (role === "assistant" ? "AI" : "Me") +
      '</div><div class="message-content"><p></p></div>';
    if (txt) d.querySelector("p").textContent = txt;
    return d;
  }

  function ams(role, txt) {
    var d = document.createElement("div");
    d.className = "message " + role;
    d.innerHTML = '<div class="message-avatar">' + (role === "assistant" ? "AI" : "Me") +
      '</div><div class="message-content"><p></p></div>';
    if (txt) d.querySelector("p").textContent = txt;
    var ce = document.getElementById("chatMessages").querySelector(".chat-empty");
    if (ce) document.getElementById("chatMessages").innerHTML = "";
    document.getElementById("chatMessages").appendChild(d);
    stb();
    return d;
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
        if (d && d.title) { f.label = d.title; ssl(l); rsl(); fetch("/api/chat/sessions/save", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({id: state.sessionId, title: d.title, user_id: "default"}) }).catch(function(){}); }
      }).catch(function(){});
    }

    // --- dynamic bubbles: one per agent round ---
    var toolRoundDiv = null;
    var answerDiv = null;
    var inAnswer = false;
    var full = "";
    var roundIdx = 0;

    function mkThink() {
      var d = document.createElement("div");
      d.className = "message assistant thinking";
      d.innerHTML = '<div class="message-avatar">&#128269;</div>' +
        '<div class="message-content"><div class="thinking-label">正在分析问题...</div></div>';
      var cm = document.getElementById("chatMessages");
      if (cm) { cm.appendChild(d); stb(); }
      return d;
    }

    function mkAnswer() {
      var d = document.createElement("div");
      d.className = "message assistant";
      d.innerHTML = '<div class="message-avatar">AI</div>' +
        '<div class="message-content"><p class="stream-text"></p></div>';
      var cm = document.getElementById("chatMessages");
      if (cm) { cm.appendChild(d); stb(); }
      return d;
    }

    state.streaming = true;
    var sb = document.getElementById("sendBtn"); if (sb) sb.disabled = true;

    try {
      var p = new URLSearchParams();
      p.append("session_id", state.sessionId); p.append("message", txt);
      p.append("kb_id", state.chatKbId); p.append("tenant_id", state.tenantId);
      p.append("user_id", state.userId); p.append("top_k", "5");
      p.append("rerank_strategy", "none");
      p.append("enable_graphrag", state.graphRagEnabled !== false ? "true" : "false");

      state.abortController = new AbortController();
      var resp = await fetch("/api/chat/stream", { method: "POST", body: p, signal: state.abortController.signal });
      if (!resp.ok) throw new Error("HTTP " + resp.status);

      var reader = resp.body.getReader();
      var dec = new TextDecoder("utf-8");
      var buf = "";

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
          if (dt.startsWith("__REASONING__:")) { continue; }
          if (dt.startsWith("__TOOL_CALL__:")) {
            if (inAnswer) { inAnswer = false; full = ""; roundIdx++; }
            if (!toolRoundDiv || toolRoundDiv._roundIdx !== roundIdx) {
              toolRoundDiv = mkThink();
              toolRoundDiv._roundIdx = roundIdx;
            }
            try {
              var tc = JSON.parse(dt.slice(14));
              var lbl = toolRoundDiv.querySelector(".thinking-label");
              if (lbl) {
                var tools = JSON.parse(lbl.getAttribute("data-tools") || "[]");
                tools.push(tc.name);
                lbl.setAttribute("data-tools", JSON.stringify(tools));
                lbl.textContent = "调用工具: " + tools.join(", ");
              }
            } catch(e) {}
            continue;
          }
          if (dt.startsWith("__TOOL_RESULT__:")) continue;
          if (dt.startsWith("__SOURCES__:")) {
            try { var srcs = JSON.parse(dt.slice(12)); renderSources(srcs); } catch(e) {}
            continue;
          }
          // text content -> answer bubble
          if (!inAnswer) {
            answerDiv = mkAnswer();
            inAnswer = true;
          }
          full += dt;
          if (!answerDiv._lastMd || Date.now() - answerDiv._lastMd > 80) {
            rmd(answerDiv.querySelector("p"), full);
            answerDiv._lastMd = Date.now();
          }
          stb();
        }
        if (result.done) break;
      }
      if (!full) {
        if (!inAnswer) { answerDiv = mkAnswer(); }
        answerDiv.querySelector("p").textContent = "(no response)";
      } else {
        rmd(answerDiv.querySelector("p"), full);
      }
      uts(state.sessionId);
      setTimeout(function() { lss(); }, 500);

    } catch(e) {
      if (state.abortController && state.abortController.signal.aborted) { /* user navigated away */ }
      else { if (!answerDiv) { answerDiv = mkAnswer(); } rmd(answerDiv.querySelector("p"), "**Error:** " + e.message); }
    } finally {
      state.abortController = null;
      state.streaming = false; if (sb) sb.disabled = false;
    }
  }

  async function lch() {
    var cm = document.getElementById("chatMessages"); if (!cm) return;
    cm._paginateOffset = 0;
    cm._paginateHasMore = true;
    cm._loadingMore = false;
    try {
      var resp = await fetch("/api/chat/history/" + state.sessionId + "?offset=0&limit=20");
      if (!resp.ok) {
        cm.innerHTML = '<div class="chat-empty"><div class="chat-empty-icon">💬</div><p>开始一段新的对话吧</p><p class="chat-empty-hint">在下方输入你的问题，AI 会基于知识库为你解答</p></div>';
        return;
      }
      var data = await resp.json();
      cm._paginateHasMore = data.has_more;
      cm._paginateOffset = (data.history || []).length;
      cm.innerHTML = "";
      (data.history || []).forEach(function(m) {
        // Show tool calls as thinking bubble (same style as streaming)
        if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
          var thinkDiv = document.createElement("div");
          thinkDiv.className = "message assistant thinking";
          var toolNames = m.tool_calls.map(function(tc) { return tc.tool_name; }).join(", ");
          thinkDiv.innerHTML = '<div class="message-avatar">&#128269;</div><div class="message-content"><div class="thinking-label">调用工具: ' + esc(toolNames) + '</div></div>';
          var cm2 = document.getElementById("chatMessages");
          if (cm2) cm2.appendChild(thinkDiv);
        }
        // Answer bubble
        var el = makeMsg(m.role === "user" ? "user" : "assistant", "");
        var cm3 = document.getElementById("chatMessages");
        if (cm3) cm3.appendChild(el);
        rmd(el.querySelector("p"), m.content || "");
      });
      if (cm._paginateHasMore) {
        var moreDiv = document.createElement("div");
        moreDiv.className = "load-more-indicator";
        moreDiv.textContent = "↑ 向上滑动加载更多...";
        cm.insertBefore(moreDiv, cm.firstChild);
      }
      cm.onscroll = function() {
        if (cm.scrollTop < 80 && cm._paginateHasMore && !cm._loadingMore) {
          cm.onscroll = null;
          lchMore();
        }
      };
      stb();
    } catch(e) { cm.innerHTML = ""; }
  }

  async function lchMore() {
    var cm = document.getElementById("chatMessages");
    if (!cm || !cm._paginateHasMore || cm._loadingMore) return;
    var ce = cm.querySelector(".chat-empty");
    if (ce) cm.innerHTML = "";
    cm._loadingMore = true;
    try {
      var offset = cm._paginateOffset;
      var resp = await fetch("/api/chat/history/" + state.sessionId + "?offset=" + offset + "&limit=20");
      if (!resp.ok) { cm._loadingMore = false; return; }
      var data = await resp.json();
      cm._paginateHasMore = data.has_more;
      cm._paginateOffset += (data.history || []).length;
      var oldHeight = cm.scrollHeight;
      var indicator = cm.querySelector(".load-more-indicator");
      if (indicator) indicator.remove();
      var msgs = data.history || [];
      for (var i = msgs.length - 1; i >= 0; i--) {
        var m = msgs[i];
        if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
          for (var j = 0; j < m.tool_calls.length; j++) {
            var td = document.createElement("div");
            td.className = "message-tool";
            td.innerHTML = '<span class="tool-icon">&#9881;</span><span class="tool-label">[Tool] ' + esc(m.tool_calls[j].tool_name) + '</span>';
            cm.insertBefore(td, cm.firstChild);
          }
        }
        var el = makeMsg(m.role === "user" ? "user" : "assistant", "");
        rmd(el.querySelector("p"), m.content || "");
        cm.insertBefore(el, cm.firstChild);
      }
      cm.scrollTop = cm.scrollHeight - oldHeight;
      if (cm._paginateHasMore) {
        var moreDiv = document.createElement("div");
        moreDiv.className = "load-more-indicator";
        moreDiv.textContent = "↑ 向上滑动加载更多...";
        cm.insertBefore(moreDiv, cm.firstChild);
      }
      cm.onscroll = function() {
        if (cm.scrollTop < 80 && cm._paginateHasMore && !cm._loadingMore) {
          cm.onscroll = null;
          lchMore();
        }
      };
    } catch(e) {}
    cm._loadingMore = false;
  }

  async function lkl() {
    // Cancel previous in-flight request
    if (state._lklAbort) { state._lklAbort.abort(); }
    var ctrl = new AbortController(); state._lklAbort = ctrl;
    try {
      var resp = await fetch("/api/kb/list?tenant_id=default", { signal: ctrl.signal });
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
       var gks = $("graphKbSelect");
       if (gks) {
         gks.innerHTML = "";
         (data || []).forEach(function(kb) {
           var o = document.createElement("option");
           o.value = kb.id; o.textContent = kb.name;
           gks.appendChild(o);
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
    // Debounce: clear pending timer, set new 200ms timer
    if (state._rdlTimer) clearTimeout(state._rdlTimer);
    state._rdlTimer = setTimeout(function() { rdl(); }, 200);
  }

    function statusLabel(s) {
    var map = { ready: "ready", pending: "pending", failed: "failed", processing: "processing" };
    return map[s] || s || "ready";
  }
  function statusBadge(s, errorMsg) {
    var label = s;
    if (s === "failed" && errorMsg && errorMsg.indexOf("orphaned") !== -1) label = "lost";
    var cls = label === "ready" ? "badge badge-ready" : label === "failed" ? "badge badge-failed" : label === "lost" ? "badge badge-lost" : label === "processing" ? "badge badge-processing" : "badge badge-pending";
    return '<span class="' + cls + '" data-status="' + label + '"></span>';
  }
  function formatSize(bytes) {
    if (!bytes || bytes === 0) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  }
  async function rdl() {
    // Cancel previous in-flight request
    if (state._rdlAbort) { state._rdlAbort.abort(); }
    var ctrl = new AbortController(); state._rdlAbort = ctrl;
    try {
      var url = "/api/documents/list?kb_id=" + encodeURIComponent(state.docKbId) + "&tenant_id=" + state.tenantId;
      var resp = await fetch(url, { signal: ctrl.signal });
      var data = await resp.json();
      var docs = data.documents || [];
      var dc = $("docCount"); if (dc) dc.textContent = docs.length + " docs";
      var di = $("docItems");
      if (di) {
        di.innerHTML = docs.map(function(d) {
          var isOrphan = (d.id||"").startsWith("orphan_");
          var chunks = d.total_chunks || 0;
          var meta = [chunks ? chunks + " chunks" : "", formatSize(d.file_size)].filter(Boolean).join(" · ");
          var actions = '';
          var isFailed = d.status === 'failed' || d.status === 'error';
          if (isOrphan || isFailed) {
            actions += '<button class="btn-doc-reindex" data-fn="' + esc(d.filename) + '"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>' + (isOrphan ? '重新索引' : '重试') + '</button>';
          }
          actions += '<button class="btn-doc-delete" data-id="' + d.id + '"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>删除</button>';
          return '<div class="doc-item">' +
            '<span class="doc-icon" data-type="' + esc(d.doc_type || 'txt') + '">' + (d.doc_type === "pdf" ? "PDF" : d.doc_type === "docx" ? "DOC" : d.doc_type === "md" ? "MD" : "TXT") + '</span>' +
            '<div class="doc-info">' +
              '<span class="doc-name">' + esc(d.filename) + '</span>' +
              '<span class="doc-meta">' + statusBadge(d.status || "ready") + (meta ? ' <span class="doc-meta-text">' + meta + '</span>' : '') + '</span>' +
            '</div>' +
            '<div class="doc-actions">' + actions + '</div>' +
          '</div>';
        }).join("");
        di.querySelectorAll(".btn-doc-delete").forEach(function(b) {
          b.addEventListener("click", function() { if (!_deletingIds[b.dataset.id]) dld(b.dataset.id); });
        });
        di.querySelectorAll(".btn-doc-reindex").forEach(function(b) {
          b.addEventListener("click", function() { rdx(b.dataset.fn); });
        });
      }
    } catch(e) { console.error(e); }
  }

  async function rdx(fn) {
    // Show retrying state on the existing row
    var safe = fn.replace(/[^a-zA-Z0-9._-]/g, "_");
    var btns = document.querySelectorAll(".btn-doc-reindex"); var row = null; for (var k = 0; k < btns.length; k++) { if (btns[k].dataset.fn === fn) { row = btns[k].closest(".doc-item"); break; } }
    if (row) row = row.closest(".doc-item");
    if (row) { row.classList.add("doc-item-uploading"); var badge = row.querySelector(".badge"); if (badge) { badge.textContent = "重试中..."; badge.className = "badge badge-pending"; } var acts = row.querySelector(".doc-actions"); if (acts) acts.innerHTML = ""; }
    try {
      await fetch("/api/documents/" + encodeURIComponent(fn) + "?kb_id=" + state.docKbId + "&tenant_id=" + state.tenantId, { method: "DELETE" }).catch(function(){});
      var resp = await fetch("/api/documents/reindex/" + encodeURIComponent(fn) + "?kb_id=" + state.docKbId, { method: "POST" });
      if (resp.ok) {
        var rj = await resp.json();
        for (var j = 0; j < 15; j++) {
          await new Promise(function(r) { setTimeout(r, 1500); });
          var sr = await fetch("/api/documents/status/" + rj.task_id + "?kb_id=" + state.docKbId).catch(function(){});
          if (!sr || !sr.ok) continue;
          var st = await sr.json();
          if (st.status === "ready" || st.status === "failed") break;
        }
      }
      rdl(); lkl();
    } catch(e) {
      if (row) { row.classList.remove("doc-item-uploading"); var badge2 = row.querySelector(".badge"); if (badge2) { badge2.textContent = "失败"; badge2.className = "badge badge-failed"; } }
    }
  }

  var _pendingDeleteId = null;
  function showConfirm(title, cb) {
    $("confirmModal").style.display = "flex";
    _pendingDeleteId = cb;
  }
  function hideConfirm() {
    $("confirmModal").style.display = "none"; _pendingDeleteId = null;
  }
  var _deletingIds = {};
  async function dld(did) {
    showConfirm("", function() {
      _deletingIds[did] = true;
      var btn = document.querySelector('.btn-doc-delete[data-id="' + did + '"]');
      var row = btn ? btn.closest('.doc-item') : null;
      if (row) row.style.display = 'none';
      fetch("/api/documents/" + encodeURIComponent(did) + "?kb_id=" + state.docKbId + "&tenant_id=" + state.tenantId, { method: "DELETE" })
        .then(function() {
          delete _deletingIds[did];
          if (row && row.parentNode) row.remove();
          lkl();
        })
        .catch(function(e) { delete _deletingIds[did]; if (row) row.style.display = ''; alert("Delete failed: " + e.message); });
    });
  }
    function _addPendingRow(filename, doctype) {
    var di = $("docItems"); if (!di) return null;
    var row = document.createElement("div"); row.className = "doc-item doc-item-uploading";
    var ext = doctype || filename.split(".").pop() || "txt";
    var icon = ext === "pdf" ? "PDF" : ext === "docx" ? "DOC" : ext === "md" ? "MD" : "TXT";
    row.innerHTML = '<span class="doc-icon" data-type="' + ext + '">' + icon + '</span>' +
      '<div class="doc-info">' +
        '<span class="doc-name">' + esc(filename) + '</span>' +
        '<span class="doc-meta"><span class="badge badge-pending">处理中...</span></span>' +
      '</div>' + '<div class="doc-actions"></div>';
    di.insertBefore(row, di.firstChild); return row;
  }
  function _updatePendingRow(filename, status, chunks) {
    var safe = filename.replace(/[^a-zA-Z0-9._-]/g, "_");
    var row = document.getElementById("pending-" + safe);
    if (!row) return; var badge = row.querySelector(".badge"); if (!badge) return;
    if (status === "ready") { badge.className = "badge badge-ready"; badge.textContent = "就绪";
      row.classList.remove("doc-item-uploading"); }
    else if (status === "failed") { badge.className = "badge badge-failed"; badge.textContent = "失败";
      row.classList.remove("doc-item-uploading");
      var acts = row.querySelector(".doc-actions");
      if (acts) {
        acts.innerHTML = "<button class=\"btn-doc-reindex\" data-fn=\"" + esc(filename) + "\"><svg width=\"12\" height=\"12\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><polyline points=\"23 4 23 10 17 10\"/><path d=\"M20.49 15a9 9 0 1 1-2.12-9.36L23 10\"/></svg>重试</button>";
        acts.querySelector(".btn-doc-reindex").addEventListener("click", function() {
          row.remove(); _retryUpload(filename);
        });
      }
    }
  }
  async function _retryUpload(fn) {
    var kbId = state.docKbId;
    // Fetch the file bytes: need to use the reindex endpoint
    var row = _addPendingRow(fn);
    if (row) row.id = "pending-" + fn.replace(/[^a-zA-Z0-9._-]/g, "_");
    try {
      var resp = await fetch("/api/documents/reindex/" + encodeURIComponent(fn) + "?kb_id=" + kbId);
      if (resp.ok) {
        var rj = await resp.json();
        for (var j = 0; j < 30; j++) {
          await new Promise(function(r) { setTimeout(r, 1500); });
          var sr = await fetch("/api/documents/status/" + rj.task_id + "?kb_id=" + kbId);
          var st = await sr.json();
          if (st.status === "ready" || st.status === "failed") {
            _updatePendingRow(fn, st.status, st.total_chunks); break;
          }
        }
      } else { _updatePendingRow(fn, "failed"); }
    } catch(e) { _updatePendingRow(fn, "failed"); }
    setTimeout(function() { rdl(); lkl(); }, 2000);
  }
  async function upl(files) {
    var fl = Array.from(files);
    for (var i = 0; i < fl.length; i++) {
      var f = fl[i];
      var row = _addPendingRow(f.name);
      if (row) row.id = "pending-" + f.name.replace(/[^a-zA-Z0-9._-]/g, "_");
      try {
        var fd = new FormData();
        fd.append("file", f); fd.append("kb_id", state.docKbId); fd.append("tenant_id", state.tenantId);
        var resp = await fetch("/api/documents/upload", { method: "POST", body: fd });
        if (resp.ok) {
          var rj = await resp.json();
          for (var j = 0; j < 30; j++) {
            await new Promise(function(r) { setTimeout(r, 1500); });
            var sr = await fetch("/api/documents/status/" + rj.task_id + "?kb_id=" + state.docKbId);
            var st = await sr.json();
            if (st.status === "ready" || st.status === "failed") {
              _updatePendingRow(f.name, st.status, st.total_chunks); break;
            }
          }
        } else { _updatePendingRow(f.name, "failed"); }
      } catch(e) { _updatePendingRow(f.name, "failed"); }
    }
    setTimeout(function() { rdl(); lkl(); }, 2000);
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
  on("navGraph", "click", function() { switchView("graph"); renderGraphView(); });
  on("navDocuments", "click", function() { switchView("documents"); rdl(); lkl(); });
  on("btnLogout", "click", function() {
    localStorage.removeItem("kb_token"); localStorage.removeItem("kb_username");
    localStorage.removeItem("kb_user_id"); window.location.href = "/login";
  });
  on("sendBtn", "click", sdm);

  // Graph view controls
  on("graphRefresh", "click", refreshGraph);
  on("graphBuild", "click", buildGraph);
  on("graphSearch", "keydown", function(e) { if (e.key === "Enter") refreshGraph(); });
  on("graphKbSelect", "change", function() { state.kbId = this.value; refreshGraph(); });
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
  on("confirmCancel", "click", function() { hideConfirm(); });
  on("confirmOk", "click", function() { var cb = _pendingDeleteId; hideConfirm(); if (cb) cb(); });
  on("kbDelOk", "click", dkb);

  chk();
  setInterval(chk, 30000);

  if (!state.sessionId) { state.sessionId = gid(); localStorage.setItem("kb_session_id", state.sessionId); }
  uts(state.sessionId); rsl();

  function synSessions() {
    fetch("/api/chat/sessions?user_id=default").then(function(r){ return r.json(); }).then(function(d){
      if (!d || !d.sessions || !d.sessions.length) return;
      var local = gsl(); var localMap = {};
      local.forEach(function(s) { localMap[s.id] = s; });
      d.sessions.forEach(function(ss) {
        if (localMap[ss.id]) {
          if (ss.title && !localMap[ss.id].label) localMap[ss.id].label = ss.title;
        } else {
          local.push({ id: ss.id, created: ss.created_at*1000 || Date.now(), updated: ss.created_at*1000 || Date.now(), label: ss.title || "" });
        }
      });
      local.sort(function(a,b) { return (b.updated||0) - (a.updated||0); });
      if (local.length > 100) local = local.slice(0, 100);
      ssl(local);
      rsl();
    }).catch(function(){});
  }

  synSessions(); lkl().then(function() { rdl(); lch(); });
  setInterval(function() { rdl(); lkl(); }, 60000);


  // ====================================================================
  // Knowledge Graph visualization (vis-network)
  // ====================================================================
  var graphNetwork = null;

  async function renderGraphView() {
    var kbId = (document.getElementById("graphKbSelect") ? document.getElementById("graphKbSelect").value : null) || state.docKbId || "default";
    var search = $("graphSearch") ? $("graphSearch").value : "";

    var url = "/api/graph/data?kb_id=" + encodeURIComponent(kbId) + "&limit=200";
    if (search) url += "&search=" + encodeURIComponent(search);

    try {
      var resp = await fetch(url);
      var data = await resp.json();
      var stats = $("graphStats"); if (stats) stats.textContent = data.total_entities + " entities, " + data.total_relations + " relations";

      var container = $("graphContainer");
      if (!container) return;
      container.innerHTML = "";

      if (!data.nodes || data.nodes.length === 0) {
        container.innerHTML = '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#94a3b8;font-size:15px">该知识库暂无图谱数据，请先上传文档</div>';
        return;
      }

      var nodes = new vis.DataSet(data.nodes.map(function(n) {
        return {
          id: n.id, label: n.label, title: n.title, group: n.group,
          color: { background: n.color || "#6b7280", border: "#374151" },
          font: { color: "#e2e8f0", size: 14 }
        };
      }));

      var edges = new vis.DataSet(data.edges.map(function(e) {
        return {
          from: e.from, to: e.to, label: e.label, title: e.title, arrows: "to",
          color: { color: "#475569", highlight: "#f59e0b" },
          font: { color: "#94a3b8", size: 11, strokeWidth: 0 }
        };
      }));

      var options = {
        nodes: { shape: "dot", size: 20, borderWidth: 2 },
        edges: { smooth: { type: "continuous" }, width: 1.5 },
        physics: {
          stabilization: { iterations: 100 }, improvedLayout: false,
          barnesHut: { gravitationalConstant: -2000, springConstant: 0.04, springLength: 150 }
        },
        interaction: { hover: true, tooltipDelay: 200, zoomView: true, dragView: true }
      };

      graphNetwork = new vis.Network(container, { nodes: nodes, edges: edges }, options);

      graphNetwork.on("doubleClick", function(params) {
        if (params.nodes.length > 0) {
          var nn = nodes.get(params.nodes[0]);
          var gs = $("graphSearch"); if (gs) gs.value = nn.label;
          renderGraphView();
        }
      });
    } catch(e) {
      console.error("Graph render error:", e);
    }
  }

  function refreshGraph() {
    if (graphNetwork) { graphNetwork.destroy(); graphNetwork = null; }
    renderGraphView();
  }

  async function buildGraph() {
    var btn = document.getElementById("graphBuild");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "构建中...";
    try {
      var kbId = (document.getElementById("graphKbSelect") ? document.getElementById("graphKbSelect").value : null) || state.docKbId || "default";
      var resp = await fetch("/api/graph/build?kb_id=" + encodeURIComponent(kbId) + "&max_chunks=0", { method: "POST" });
      var data = await resp.json();
      console.log("Graph build:", data);
      btn.textContent = "构建中...等待5分钟后刷新";
      setTimeout(function() { btn.textContent = "构建图谱"; btn.disabled = false; refreshGraph(); }, 300000);
    } catch(e) {
      console.error(e);
      btn.textContent = "构建图谱";
      btn.disabled = false;
    }
  }

  window.refreshGraph = refreshGraph;
  window.buildGraph = buildGraph;
  window.renderGraphView = renderGraphView;
})();

