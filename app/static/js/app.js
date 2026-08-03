(function() {
  "use strict";

  var state = {
    sessionId: localStorage.getItem("kb_session_id") || "",
    tenantId: "default",
    graphRagEnabled: true,
    graphMode: localStorage.getItem("kb_graph_mode") || "2d",
    graphDesign: localStorage.getItem("kb_graph_design") || "stellar",
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

  // Session view helper: returns or creates session-specific DOM container
  function cv(sid) {
    sid = sid || state.sessionId;
    var cm = document.getElementById("chatMessages");
    if (!cm) return null;
    var view = cm.querySelector('.session-view[data-sid="' + sid + '"]');
    if (!view) {
      view = document.createElement("div");
      view.className = "session-view";
      view.setAttribute("data-sid", sid);
      cm.appendChild(view);
    }
    return view;
  }

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
    if (sid === state.sessionId) return;
    // Hide all views, show target
    var cm = document.getElementById("chatMessages");
    if (cm) {
      cm.querySelectorAll(".session-view").forEach(function(v) {
        v.style.display = (v.getAttribute("data-sid") === sid) ? "" : "none";
      });
    }
    state.sessionId = sid; localStorage.setItem("kb_session_id", sid);
    // Load history if view is empty
    var view = cv(sid);
    if (!view.querySelector(".message") && !view.querySelector(".chat-empty")) {
      lch();
    }
    rsl(); scv();
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
    cv(state.sessionId).innerHTML = '<div class="chat-empty"><div class="chat-empty-icon">💬</div><p>开始一段新的对话吧</p><p class="chat-empty-hint">在下方输入你的问题，AI 会基于知识库为你解答</p></div>';
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

  function ams(role, txt, sid) {
    var d = document.createElement("div");
    d.className = "message " + role;
    d.innerHTML = '<div class="message-avatar">' + (role === "assistant" ? "AI" : "Me") +
      '</div><div class="message-content"><p></p></div>';
    if (txt) d.querySelector("p").textContent = txt;
    var v = cv(sid);
    if (v) {
      var ce = v.querySelector(".chat-empty");
      if (ce) v.innerHTML = "";
      v.appendChild(d);
    }
    stb();
    return d;
  }

  function atb(name, sid) {
    var d = document.createElement("div");
    d.className = "message-tool";
    d.innerHTML = '<span class="tool-icon">&#9881;</span>' +
      '<span class="tool-label">[Tool] ' + esc(name) + '</span>';
    var v = cv(sid);
    if (v) v.appendChild(d);
    stb();
  }

  async function sdm() {
    var inp = $("chatInput"); var txt = inp.value.trim();
    if (!txt || state.streaming) return;
    inp.value = ""; inp.style.height = "auto";
    var mySid = state.sessionId;
    ams("user", txt, mySid);

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
      var v = cv(mySid);
      if (v) { v.appendChild(d); stb(); }
      return d;
    }

    function mkAnswer() {
      var d = document.createElement("div");
      d.className = "message assistant";
      d.innerHTML = '<div class="message-avatar">AI</div>' +
        '<div class="message-content"><p class="stream-text"></p></div>';
      var v = cv(mySid);
      if (v) { v.appendChild(d); stb(); }
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
            try { var srcs = JSON.parse(dt.slice(12)); console.log("SOURCES raw length:", dt.length, "parsed:", srcs.length, "items"); renderSources(srcs); } catch(e) { console.warn("SOURCES parse failed, raw prefix:", dt.substring(0,30)); }
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
    var v = cv(state.sessionId); if (!v) return;
    try {
      var resp = await fetch("/api/chat/history/" + state.sessionId + "?offset=0&limit=20");
      if (!resp.ok) {
        v.innerHTML = '<div class="chat-empty"><div class="chat-empty-icon">💬</div><p>开始一段新的对话吧</p><p class="chat-empty-hint">在下方输入你的问题，AI 会基于知识库为你解答</p></div>';
        return;
      }
      var data = await resp.json();
      cm._paginateHasMore = data.has_more;
      cm._paginateOffset = (data.history || []).length;
      v.innerHTML = "";
      (data.history || []).forEach(function(m) {
        // Show tool calls as thinking bubble (same style as streaming)
        if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
          var thinkDiv = document.createElement("div");
          thinkDiv.className = "message assistant thinking";
          var toolNames = m.tool_calls.map(function(tc) { return tc.tool_name; }).join(", ");
          thinkDiv.innerHTML = '<div class="message-avatar">&#128269;</div><div class="message-content"><div class="thinking-label">调用工具: ' + esc(toolNames) + '</div></div>';
          if (v) v.appendChild(thinkDiv);
        }
        // Answer bubble
        var el = makeMsg(m.role === "user" ? "user" : "assistant", "");
        if (v) v.appendChild(el);
        rmd(el.querySelector("p"), m.content || "");
      });
      if (cm._paginateHasMore) {
        var moreDiv = document.createElement("div");
        moreDiv.className = "load-more-indicator";
        moreDiv.textContent = "↑ 向上滑动加载更多...";
        v.insertBefore(moreDiv, v.firstChild);
      }
      cm.onscroll = function() {
        if (cm.scrollTop < 80 && cm._paginateHasMore && !cm._loadingMore) {
          cm.onscroll = null;
          lchMore();
        }
      };
      stb();
    } catch(e) { if (v) v.innerHTML = ""; }
  }

  async function lchMore() {
    var cm = document.getElementById("chatMessages");
    if (!cm || !cm._paginateHasMore || cm._loadingMore) return;
    var v = cv(state.sessionId); if (!v) return;
    var ce = v.querySelector(".chat-empty");
    if (ce) v.innerHTML = "";
    cm._loadingMore = true;
    try {
      var offset = cm._paginateOffset;
      var resp = await fetch("/api/chat/history/" + state.sessionId + "?offset=" + offset + "&limit=20");
      if (!resp.ok) { cm._loadingMore = false; return; }
      var data = await resp.json();
      cm._paginateHasMore = data.has_more;
      cm._paginateOffset += (data.history || []).length;
      var oldHeight = cm.scrollHeight;
      var indicator = v.querySelector(".load-more-indicator");
      if (indicator) indicator.remove();
      var msgs = data.history || [];
      for (var i = msgs.length - 1; i >= 0; i--) {
        var m = msgs[i];
        if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
          for (var j = 0; j < m.tool_calls.length; j++) {
            var td = document.createElement("div");
            td.className = "message-tool";
            td.innerHTML = '<span class="tool-icon">&#9881;</span><span class="tool-label">[Tool] ' + esc(m.tool_calls[j].tool_name) + '</span>';
            v.insertBefore(td, v.firstChild);
          }
        }
        var el = makeMsg(m.role === "user" ? "user" : "assistant", "");
        rmd(el.querySelector("p"), m.content || "");
        v.insertBefore(el, v.firstChild);
      }
      cm.scrollTop = cm.scrollHeight - oldHeight;
      if (cm._paginateHasMore) {
        var moreDiv = document.createElement("div");
        moreDiv.className = "load-more-indicator";
        moreDiv.textContent = "↑ 向上滑动加载更多...";
        v.insertBefore(moreDiv, v.firstChild);
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
  on("graphFit", "click", function() { fitGraph(true); });
  on("graphZoomIn", "click", function() { zoomGraph(1); });
  on("graphZoomOut", "click", function() { zoomGraph(-1); });
  on("graphMode2d", "click", function() { setGraphMode("2d"); });
  on("graphMode3d", "click", function() { setGraphMode("3d"); });
  on("graphDesign", "change", function() { setGraphDesign(this.value); });
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
  syncGraphModeButtons();

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
  var graph3D = null;

  function setGraphLoading(on) {
    var l = $("graphLoading");
    if (l) l.classList.toggle("hidden", !on);
  }

  async function renderGraphView() {
    var kbId = (document.getElementById("graphKbSelect") ? document.getElementById("graphKbSelect").value : null) || state.docKbId || "default";
    var search = $("graphSearch") ? $("graphSearch").value : "";

    var url = "/api/graph/data?kb_id=" + encodeURIComponent(kbId) + "&limit=200";
    if (search) url += "&search=" + encodeURIComponent(search);

    setGraphLoading(true);
    try {
      var resp = await fetch(url);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();

      var connectedIds = {};
      (data.edges || []).forEach(function(e) { connectedIds[e.from] = true; connectedIds[e.to] = true; });
      var visibleNodes = (data.nodes || []).filter(function(n) { return !!connectedIds[n.id]; });

      var stats = $("graphStats");
      if (stats) {
        var isolated = data.isolated_count || 0;
        stats.textContent = visibleNodes.length + " 个实体 · " + (data.edges || []).length + " 条关系" +
          (isolated > 0 ? " · " + isolated + " 个孤立点已隐藏" : "");
      }

      var container = $("graphContainer");
      var legend = $("graphLegend");
      var controls = $("graphControls");
      if (!container) { setGraphLoading(false); return; }
      if (graphNetwork) { graphNetwork.destroy(); graphNetwork = null; }
      destroyGraph3D();
      container.innerHTML = "";

      if (visibleNodes.length === 0) {
        if (legend) legend.classList.add("hidden");
        if (controls) controls.classList.add("hidden");
        container.innerHTML = '<div class="graph-empty"><div class="graph-empty-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5"/><path d="M6.5 17.5 11 12"/><path d="M13 12l4.5 5.5"/></svg></div><p>该知识库暂无图谱数据</p><p class="graph-empty-hint">请先上传文档并构建图谱</p></div>';
        setGraphLoading(false);
        return;
      }

      var degree = {};
      (data.edges || []).forEach(function(e) {
        degree[e.from] = (degree[e.from] || 0) + 1;
        degree[e.to] = (degree[e.to] || 0) + 1;
      });
      var maxDeg = 1;
      Object.keys(degree).forEach(function(k) { if (degree[k] > maxDeg) maxDeg = degree[k]; });

      var nodes = new vis.DataSet(visibleNodes.map(function(n) {
        return {
          id: n.id, label: n.label, title: n.title, group: n.group,
          color: {
            background: n.color || "#6b7280", border: "#e2e8f0",
            highlight: { background: n.color || "#6b7280", border: "#fbbf24" },
            hover: { background: n.color || "#6b7280", border: "#f8fafc" }
          },
          size: 12 + Math.round(14 * ((degree[n.id] || 0) / maxDeg)),
          font: { color: "#e2e8f0", size: 13, face: "Segoe UI, Noto Sans SC, sans-serif", strokeWidth: 4, strokeColor: "#0b1220" },
          shadow: { enabled: true, color: "rgba(0,0,0,0.35)", size: 8, x: 0, y: 2 }
        };
      }));

      var edges = new vis.DataSet(data.edges.map(function(e) {
        return {
          from: e.from, to: e.to,
          label: e.dashes ? "" : (e.label || ""),
          title: e.title, arrows: e.arrows || undefined,
          dashes: !!e.dashes, width: e.dashes ? 1 : 1.5,
          color: { color: e.dashes ? "#475569" : "#7c8ba1", highlight: "#fbbf24", hover: "#f8fafc" },
          font: { color: "#94a3b8", size: 10, strokeWidth: 3, strokeColor: "#0b1220", face: "Segoe UI, Noto Sans SC, sans-serif" }
        };
      }));

      var options = {
        autoResize: true,
        nodes: { shape: "dot", borderWidth: 2, borderWidthSelected: 3 },
        edges: { smooth: { type: "continuous", roundness: 0.25 }, selectionWidth: 2 },
        layout: { improvedLayout: false, randomSeed: 7 },
        physics: {
          enabled: true,
          stabilization: { enabled: true, iterations: 220, updateInterval: 25, onlyDynamicEdges: false, fit: true },
          barnesHut: {
            gravitationalConstant: -3200, centralGravity: 0.08,
            springLength: 150, springConstant: 0.035,
            damping: 0.7, avoidOverlap: 0.45
          }
        },
        interaction: { hover: true, tooltipDelay: 200, zoomView: true, dragView: true }
      };

      buildGraphLegend(visibleNodes);
      if (controls) controls.classList.remove("hidden");
      if (state.graphMode === "3d") {
        if (typeof THREE === "undefined" || !THREE.OrbitControls) {
          container.innerHTML = '<div class="graph-empty error"><div class="graph-empty-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div><p>3D 资源加载失败</p><p class="graph-empty-hint">请检查网络后刷新页面</p></div>';
          setGraphLoading(false);
          return;
        }
        renderGraph3D(visibleNodes, data.edges || [], container);
        setGraphLoading(false);
        return;
      }
      graphNetwork = new vis.Network(container, { nodes: nodes, edges: edges }, options);

      graphNetwork.on("doubleClick", function(params) {
        if (params.nodes.length > 0) {
          var nn = nodes.get(params.nodes[0]);
          var gs = $("graphSearch"); if (gs) gs.value = nn.label;
          renderGraphView();
        }
      });
      graphNetwork.once("stabilizationIterationsDone", function() { fitGraph(true); });
      setGraphLoading(false);
    } catch(e) {
      console.error("Graph render error:", e);
      var container = $("graphContainer");
      if (container) {
        container.innerHTML = '<div class="graph-empty error"><div class="graph-empty-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div><p>图谱加载失败</p><p class="graph-empty-hint">' + esc(e.message || "未知错误") + '</p></div>';
      }
      var legend = $("graphLegend"); if (legend) legend.classList.add("hidden");
      var controls = $("graphControls"); if (controls) controls.classList.add("hidden");
      setGraphLoading(false);
    }
  }

  function buildGraphLegend(nodes) {
    var legend = $("graphLegend");
    if (!legend) return;
    var counts = {}, colors = {};
    nodes.forEach(function(n) {
      var key = n.group || "其他";
      counts[key] = (counts[key] || 0) + 1;
      if (!colors[key]) colors[key] = n.color || "#6b7280";
    });
    var keys = Object.keys(counts).sort(function(a, b) { return counts[b] - counts[a]; });
    var top = keys.slice(0, 6), rest = keys.slice(6);
    var html = ['<div class="legend-title">实体类型</div>'];
    function row(key, count) {
      return '<div class="legend-item"><span class="legend-dot" style="background:' + (colors[key] || "#6b7280") + '"></span><span class="legend-label">' + esc(key) + '</span><span class="legend-count">' + count + '</span></div>';
    }
    top.forEach(function(key) { html.push(row(key, counts[key])); });
    if (rest.length) {
      var total = rest.reduce(function(sum, key) { return sum + counts[key]; }, 0);
      html.push(row("其他", total));
    }
    html.push('<div class="legend-title">关系</div>');
    html.push('<div class="legend-item"><span class="legend-edge"><span class="legend-line"></span></span><span class="legend-label">显式关系</span></div>');
    html.push('<div class="legend-item"><span class="legend-edge"><span class="legend-line dashed"></span></span><span class="legend-label">片段共现</span></div>');
    legend.innerHTML = html.join("");
    legend.classList.remove("hidden");
  }

  function makeLabelSprite(text, light) {
    var canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 64;
    var ctx = canvas.getContext("2d");
    ctx.font = "600 26px 'Segoe UI','Noto Sans SC','Microsoft YaHei',sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    var label = (text || "").replace(/\s+/g, " ").trim();
    var maxW = 246;
    if (ctx.measureText(label).width > maxW) {
      while (label.length > 1 && ctx.measureText(label + "...").width > maxW) {
        label = label.slice(0, -1);
      }
      label += "...";
    }
    var textW = ctx.measureText(label).width;
    if (light) {
      ctx.strokeStyle = "rgba(255,255,255,0.92)";
      ctx.lineWidth = 6;
      ctx.lineJoin = "round";
    } else {
      ctx.strokeStyle = "rgba(11,18,32,0.92)";
      ctx.lineWidth = 5;
      ctx.lineJoin = "round";
    }
    ctx.strokeText(label, 128, 32);
    ctx.fillStyle = light ? "#1e293b" : "#e2e8f0";
    ctx.fillText(label, 128, 32);
    var tex = new THREE.CanvasTexture(canvas);
    tex.minFilter = THREE.LinearFilter;
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: tex, transparent: true, depthWrite: false, depthTest: true
    }));
    sprite.scale.set(Math.max(0.7, (textW + 18) / 128), 0.52, 1);
    return sprite;
  }

  function makeRadialTexture(inner, mid) {
    var c = document.createElement("canvas");
    c.width = 128;
    c.height = 128;
    var ctx = c.getContext("2d");
    var g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, inner);
    g.addColorStop(0.35, mid);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }

  function makeGlowSprite(inner, mid, scale, opacity) {
    var s = new THREE.Sprite(new THREE.SpriteMaterial({
      map: makeRadialTexture(inner, mid),
      transparent: true,
      opacity: opacity == null ? 0.55 : opacity,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    }));
    s.scale.set(scale, scale, 1);
    return s;
  }

  function addStarField(scene, count, radius) {
    var positions = new Float32Array(count * 3);
    var colors = new Float32Array(count * 3);
    var palette = [
      new THREE.Color(0xbfdbfe), new THREE.Color(0xffffff),
      new THREE.Color(0x93c5fd), new THREE.Color(0xc7d2fe)
    ];
    for (var i = 0; i < count; i++) {
      var x = (Math.random() - 0.5) * 2;
      var y = (Math.random() - 0.5) * 2;
      var z = (Math.random() - 0.5) * 2;
      var len = Math.sqrt(x * x + y * y + z * z) || 1;
      var d = radius * (0.55 + Math.random() * 0.55);
      positions[i * 3] = x / len * d;
      positions[i * 3 + 1] = y / len * d;
      positions[i * 3 + 2] = z / len * d;
      var col = palette[Math.floor(Math.random() * palette.length)];
      colors[i * 3] = col.r;
      colors[i * 3 + 1] = col.g;
      colors[i * 3 + 2] = col.b;
    }
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    var pts = new THREE.Points(g, new THREE.PointsMaterial({
      size: 1.5,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      sizeAttenuation: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    }));
    scene.add(pts);
    return pts;
  }

  function hashNum(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h / 4294967296;
  }

  function graphSpherePositions(nodes, R, degree, maxDeg) {
    var pos = {};
    var count = nodes.length;
    var golden = Math.PI * (3 - Math.sqrt(5));
    nodes.forEach(function(n, i) {
      var y = count > 1 ? 1 - (i / (count - 1)) * 2 : 0;
      var rad = Math.sqrt(Math.max(0, 1 - y * y));
      var theta = golden * i;
      var degFactor = degree && degree[n.id] ? Math.min(1, degree[n.id] / (maxDeg || 1)) : 0.12;
      var r = R * (0.86 + 0.32 * degFactor);
      var jit = R * 0.075;
      pos[n.id] = new THREE.Vector3(
        Math.cos(theta) * rad * r + (hashNum(n.id || String(i)) - 0.5) * jit,
        y * r + (hashNum("y" + (n.id || i)) - 0.5) * jit,
        Math.sin(theta) * rad * r + (hashNum("z" + (n.id || i)) - 0.5) * jit
      );
    });
    return pos;
  }

  function addGraphEdges(scene, edges, pos, style) {
    var solid = [];
    edges.forEach(function(e) {
      var from = pos[e.from], to = pos[e.to];
      if (!from || !to) return;
      if (e.dashes) {
        var g = new THREE.BufferGeometry().setFromPoints([from, to]);
        var m = new THREE.LineDashedMaterial({
          color: style.dashColor,
          dashSize: style.dashSize || 4,
          gapSize: style.gapSize || 3,
          transparent: true,
          opacity: style.dashOpacity || 0.55
        });
        var line = new THREE.Line(g, m);
        line.computeLineDistances();
        scene.add(line);
      } else {
        solid.push(from.x, from.y, from.z, to.x, to.y, to.z);
      }
    });
    if (solid.length) {
      var sg = new THREE.BufferGeometry();
      sg.setAttribute("position", new THREE.Float32BufferAttribute(solid, 3));
      scene.add(new THREE.LineSegments(sg, new THREE.LineBasicMaterial({
        color: style.color,
        transparent: true,
        opacity: style.opacity || 0.5,
        blending: style.additive ? THREE.AdditiveBlending : THREE.NormalBlending
      })));
    }
  }

  function buildDesignScene3D(design, scene, nodes, edges, pos, degree, maxDeg, R) {
    var nodeMeshes = [];
    var decorations = [];

    function nodeRadius(n) {
      return 4.5 + 8 * ((degree[n.id] || 0) / maxDeg);
    }

    if (design === "stellar") {
      scene.background = new THREE.Color(0x05070f);
      var stars = addStarField(scene, 900, R * 3.4);
      decorations.push(stars);
      var nebula = makeGlowSprite("rgba(129,140,248,0.5)", "rgba(56,189,248,0.16)", R * 3.4, 0.62);
      scene.add(nebula);
      decorations.push(nebula);
      var nebula2 = makeGlowSprite("rgba(244,114,182,0.22)", "rgba(139,92,246,0.1)", R * 2.3, 0.4);
      nebula2.position.set(R * 0.55, -R * 0.32, -R * 0.45);
      scene.add(nebula2);
      decorations.push(nebula2);
      var shell = new THREE.Mesh(
        new THREE.SphereGeometry(R * 1.03, 48, 32),
        new THREE.MeshBasicMaterial({ color: 0x3b82f6, wireframe: true, transparent: true, opacity: 0.07 })
      );
      scene.add(shell);
      decorations.push(shell);
      nodes.forEach(function(n) {
        var base = new THREE.Color(n.color || "#6b7280");
        var radius = nodeRadius(n);
        var mesh = new THREE.Mesh(
          new THREE.SphereGeometry(radius, 20, 16),
          new THREE.MeshPhongMaterial({
            color: base,
            emissive: base.clone().multiplyScalar(0.26),
            specular: 0x223047,
            shininess: 48
          })
        );
        mesh.position.copy(pos[n.id]);
        mesh.userData = { node: n, baseColor: base.clone(), emissiveFactor: 0.26 };
        scene.add(mesh);
        nodeMeshes.push(mesh);
        if ((degree[n.id] || 0) >= maxDeg * 0.55) {
          var glow = makeGlowSprite("#93c5fd", "rgba(59,130,246,0.18)", radius * 7, 0.34);
          glow.position.copy(pos[n.id]);
          scene.add(glow);
          decorations.push(glow);
        }
      });
      addGraphEdges(scene, edges, pos, {
        color: 0x8aa5ff, opacity: 0.42, additive: true,
        dashColor: 0x60a5fa, dashOpacity: 0.5
      });
      return {
        nodeMeshes: nodeMeshes,
        labels: nodes.map(function(n) { return { id: n.id, text: n.label, position: pos[n.id] }; }),
        tick: function(t) {
          stars.rotation.y = t * 0.00012;
          stars.rotation.x = Math.sin(t * 0.00004) * 0.08;
          nebula.material.opacity = 0.58 + Math.sin(t * 0.0008) * 0.1;
          shell.rotation.y = t * 0.00006;
        }
      };
    }

    if (design === "hologram") {
      scene.background = new THREE.Color(0x04151b);
      var grid = new THREE.GridHelper(R * 3.1, 28, 0x22d3ee, 0x155e75);
      grid.position.y = -R - 46;
      scene.add(grid);
      decorations.push(grid);
      var ringA = new THREE.Mesh(
        new THREE.TorusGeometry(R * 1.02, 1.5, 8, 128),
        new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.28 })
      );
      ringA.rotation.x = Math.PI / 2.3;
      scene.add(ringA);
      decorations.push(ringA);
      var ringB = new THREE.Mesh(
        new THREE.TorusGeometry(R * 1.08, 1.1, 8, 128),
        new THREE.MeshBasicMaterial({ color: 0x818cf8, transparent: true, opacity: 0.2 })
      );
      ringB.rotation.z = Math.PI / 2.7;
      scene.add(ringB);
      decorations.push(ringB);
      nodes.forEach(function(n) {
        var base = new THREE.Color(n.color || "#6b7280");
        var radius = nodeRadius(n);
        var mesh = new THREE.Mesh(
          new THREE.OctahedronGeometry(radius, 0),
          new THREE.MeshStandardMaterial({
            color: base,
            emissive: base.clone().multiplyScalar(0.5),
            emissiveIntensity: 0.38,
            flatShading: true,
            metalness: 0.32,
            roughness: 0.34
          })
        );
        mesh.position.copy(pos[n.id]);
        mesh.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, 0);
        mesh.userData = { node: n, baseColor: base.clone(), emissiveFactor: 0.19 };
        scene.add(mesh);
        nodeMeshes.push(mesh);
      });
      addGraphEdges(scene, edges, pos, {
        color: 0x2dd4bf, opacity: 0.4, additive: true,
        dashColor: 0x22d3ee, dashOpacity: 0.45
      });
      return {
        nodeMeshes: nodeMeshes,
        labels: nodes.map(function(n) { return { id: n.id, text: n.label, position: pos[n.id] }; }),
        tick: function(t) {
          ringA.rotation.z = t * 0.00012;
          ringB.rotation.y = t * 0.0001;
          grid.position.y = -R - 46 - Math.sin(t * 0.0004) * 5;
          nodeMeshes.forEach(function(m) { m.rotation.y += 0.0018; });
        }
      };
    }

    if (design === "neon") {
      scene.background = new THREE.Color(0x09070f);
      var stars2 = addStarField(scene, 420, R * 3.2);
      decorations.push(stars2);
      var shell2 = new THREE.Mesh(
        new THREE.IcosahedronGeometry(R * 1.06, 1),
        new THREE.MeshBasicMaterial({ color: 0xff2d95, wireframe: true, transparent: true, opacity: 0.1 })
      );
      scene.add(shell2);
      decorations.push(shell2);
      var pulseLight = new THREE.PointLight(0xff2d95, 1.1, R * 3);
      scene.add(pulseLight);
      decorations.push(pulseLight);
      nodes.forEach(function(n) {
        var base = new THREE.Color(n.color || "#6b7280");
        var radius = nodeRadius(n);
        var mesh = new THREE.Mesh(
          new THREE.SphereGeometry(radius, 20, 16),
          new THREE.MeshStandardMaterial({
            color: base,
            emissive: base.clone().multiplyScalar(0.8),
            emissiveIntensity: 0.85,
            metalness: 0.28,
            roughness: 0.22
          })
        );
        mesh.position.copy(pos[n.id]);
        mesh.userData = { node: n, baseColor: base.clone(), emissiveFactor: 0.68 };
        scene.add(mesh);
        nodeMeshes.push(mesh);
        if ((degree[n.id] || 0) >= maxDeg * 0.35) {
          var glow2 = makeGlowSprite("#f472b6", "rgba(255,45,149,0.22)", radius * 6.2, 0.5);
          glow2.position.copy(pos[n.id]);
          scene.add(glow2);
          decorations.push(glow2);
        }
      });
      addGraphEdges(scene, edges, pos, {
        color: 0xff4d9e, opacity: 0.5, additive: true,
        dashColor: 0x22d3ee, dashOpacity: 0.55
      });
      return {
        nodeMeshes: nodeMeshes,
        labels: nodes.map(function(n) { return { id: n.id, text: n.label, position: pos[n.id] }; }),
        tick: function(t) {
          shell2.rotation.x = t * 0.00012;
          shell2.rotation.y = t * 0.00018;
          pulseLight.intensity = 1.15 + Math.sin(t * 0.0014) * 0.45;
          stars2.rotation.y = t * 0.00008;
        }
      };
    }

    scene.background = new THREE.Color(0xf3f5f9);
    scene.fog = new THREE.Fog(0xf3f5f9, R * 2.6, R * 4.2);
    var ring = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints((function() {
        var pts = [];
        for (var a = 0; a <= 160; a++) {
          var ang = a / 160 * Math.PI * 2;
          pts.push(new THREE.Vector3(Math.cos(ang) * R * 0.99, 0, Math.sin(ang) * R * 0.99));
        }
        return pts;
      })()),
      new THREE.LineBasicMaterial({ color: 0xd8dee8, transparent: true, opacity: 0.8 })
    );
    scene.add(ring);
    decorations.push(ring);
    nodes.forEach(function(n) {
      var base = new THREE.Color(n.color || "#64748b");
      var radius = nodeRadius(n);
      var mesh = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 20, 16),
        new THREE.MeshStandardMaterial({
          color: base,
          roughness: 0.55,
          metalness: 0.05
        })
      );
      mesh.position.copy(pos[n.id]);
      mesh.userData = { node: n, baseColor: base.clone(), emissiveFactor: 0 };
      scene.add(mesh);
      nodeMeshes.push(mesh);
    });
    addGraphEdges(scene, edges, pos, {
      color: 0xa3afbf, opacity: 0.48,
      dashColor: 0x94a3b8, dashOpacity: 0.5
    });
    return {
      nodeMeshes: nodeMeshes,
      labels: nodes.map(function(n) { return { id: n.id, text: n.label, position: pos[n.id] }; }),
      labelStyle: "light",
      tick: function(t) {
        ring.rotation.z = t * 0.00004;
      }
    };
  }

  function renderGraph3D(nodes, edges, container) {
    destroyGraph3D();
    var W = container.clientWidth || 800;
    var H = container.clientHeight || 600;
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b1220);
    var camera = new THREE.PerspectiveCamera(55, W / H, 1, 5000);
    camera.position.set(340, 260, 520);

    var renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(W, H);
    container.appendChild(renderer.domElement);

    var design = state.graphDesign || "stellar";
    var controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 170;
    controls.maxDistance = 1600;
    controls.target.set(0, 0, 0);

    scene.add(new THREE.AmbientLight(0xffffff, design === "minimal" ? 1.4 : (design === "neon" ? 0.55 : 1.0)));
    var dir1 = new THREE.DirectionalLight(0xffffff, design === "minimal" ? 1.7 : (design === "neon" ? 0.85 : 1.25));
    dir1.position.set(300, 420, 560);
    scene.add(dir1);
    var dir2 = new THREE.DirectionalLight(0x60a5fa, design === "minimal" ? 0.25 : 0.45);
    dir2.position.set(-360, -260, 320);
    scene.add(dir2);

    var degree = {};
    edges.forEach(function(e) {
      degree[e.from] = (degree[e.from] || 0) + 1;
      degree[e.to] = (degree[e.to] || 0) + 1;
    });
    var maxDeg = 1;
    Object.keys(degree).forEach(function(k) { if (degree[k] > maxDeg) maxDeg = degree[k]; });

    var R = 245;
    var pos = graphSpherePositions(nodes, R, degree, maxDeg);
    var built = buildDesignScene3D(design, scene, nodes, edges, pos, degree, maxDeg, R);
    var nodeMeshes = built.nodeMeshes;

    var labelLayer = document.createElement("div");
    labelLayer.className = "graph3d-labels design-" + design;
    container.appendChild(labelLayer);
    var labelItems = [];
    var labelById = {};
    (built.labels || []).forEach(function(li) {
      var span = document.createElement("span");
      span.className = "graph3d-label" + (built.labelStyle === "light" ? " light" : "");
      span.textContent = li.text;
      labelLayer.appendChild(span);
      var item = { el: span, pos: li.position };
      labelItems.push(item);
      if (li.id) labelById[li.id] = item;
      else if (li.text) labelById[li.text] = item;
    });
    nodeMeshes.forEach(function(mesh) {
      var n = mesh.userData && mesh.userData.node;
      if (n && labelById[n.id]) mesh.userData.labelEl = labelById[n.id].el;
    });

    var proj = new THREE.Vector3();
    function updateLabels() {
      var w = renderer.domElement.clientWidth || container.clientWidth || 800;
      var h = renderer.domElement.clientHeight || container.clientHeight || 600;
      labelItems.forEach(function(item) {
        proj.copy(item.pos).project(camera);
        if (proj.z > 1) {
          item.el.style.opacity = "0";
          return;
        }
        item.el.style.left = ((proj.x * 0.5 + 0.5) * w) + "px";
        item.el.style.top = ((-proj.y * 0.5 + 0.5) * h) + "px";
        item.el.style.opacity = "1";
      });
    }

    var raycaster = new THREE.Raycaster();
    var mouse = new THREE.Vector2();
    var hovered = null;

    function pick(ev) {
      var rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(mouse, camera);
      var hits = raycaster.intersectObjects(nodeMeshes, false);
      return hits.length ? hits[0].object : null;
    }

    function setHover(obj) {
      if (hovered === obj) return;
      if (hovered) {
        var f = hovered.userData.emissiveFactor || 0;
        if (hovered.material.emissive) {
          hovered.material.emissive.copy(hovered.userData.baseColor).multiplyScalar(f);
        }
        if (hovered.userData.labelEl) hovered.userData.labelEl.classList.remove("active");
      }
      hovered = obj;
      if (hovered && hovered.material.emissive) hovered.material.emissive.setHex(0xfbbf24);
      if (hovered && hovered.userData.labelEl) hovered.userData.labelEl.classList.add("active");
      renderer.domElement.style.cursor = hovered ? "pointer" : "grab";
    }

    renderer.domElement.addEventListener("pointermove", function(ev) {
      setHover(pick(ev));
    });
    renderer.domElement.addEventListener("pointerleave", function() {
      setHover(null);
    });
    renderer.domElement.addEventListener("pointerdown", function() {
      renderer.domElement.style.cursor = "grabbing";
    });
    renderer.domElement.addEventListener("pointerup", function() {
      renderer.domElement.style.cursor = hovered ? "pointer" : "grab";
    });
    renderer.domElement.addEventListener("dblclick", function(ev) {
      var obj = pick(ev);
      if (obj && obj.userData.node) {
        var gs = $("graphSearch");
        if (gs) gs.value = obj.userData.node.label;
        renderGraphView();
      }
    });

    var resizeObserver = new ResizeObserver(function() {
      var w = container.clientWidth, h = container.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    resizeObserver.observe(container);

    graph3D = {
      scene: scene, camera: camera, renderer: renderer, controls: controls,
      resizeObserver: resizeObserver, rafId: 0, fitRafId: 0, tick: built.tick,
      labelLayer: labelLayer
    };
    function animate() {
      if (!graph3D || graph3D.renderer !== renderer) return;
      graph3D.rafId = requestAnimationFrame(animate);
      controls.update();
      if (graph3D.tick) graph3D.tick(performance.now());
      updateLabels();
      renderer.render(scene, camera);
    }
    graph3D.rafId = requestAnimationFrame(animate);
  }

  function destroyGraph3D() {
    if (!graph3D) return;
    if (graph3D.rafId) cancelAnimationFrame(graph3D.rafId);
    if (graph3D.fitRafId) cancelAnimationFrame(graph3D.fitRafId);
    if (graph3D.resizeObserver) graph3D.resizeObserver.disconnect();
    if (graph3D.controls) graph3D.controls.dispose();
    if (graph3D.scene) {
      graph3D.scene.traverse(function(obj) {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          if (obj.material.map) obj.material.map.dispose();
          obj.material.dispose();
        }
      });
    }
    if (graph3D.renderer) {
      graph3D.renderer.dispose();
      var el = graph3D.renderer.domElement;
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }
    if (graph3D.labelLayer && graph3D.labelLayer.parentNode) {
      graph3D.labelLayer.parentNode.removeChild(graph3D.labelLayer);
    }
    graph3D = null;
  }

  function fitGraph3D(animated) {
    if (!graph3D || !graph3D.camera) return;
    var cam = graph3D.camera, controls = graph3D.controls;
    var aspect = cam.aspect || 1.5;
    var base = 245 / Math.tan(cam.fov * Math.PI / 360) * 1.45;
    var dist = base / Math.min(1, aspect);
    var to = new THREE.Vector3(0, 0, dist);
    if (animated && graph3D) {
      var from = cam.position.clone();
      var t0 = performance.now();
      function step(t) {
        if (!graph3D || graph3D.camera !== cam) return;
        var k = Math.min(1, (t - t0) / 400);
        var e = 1 - Math.pow(1 - k, 3);
        cam.position.lerpVectors(from, to, e);
        controls.target.set(0, 0, 0);
        controls.update();
        if (k < 1) graph3D.fitRafId = requestAnimationFrame(step);
      }
      graph3D.fitRafId = requestAnimationFrame(step);
    } else {
      cam.position.copy(to);
      controls.target.set(0, 0, 0);
      controls.update();
    }
  }

  function zoomGraph3D(direction) {
    if (!graph3D || !graph3D.camera) return;
    var cam = graph3D.camera;
    var next = cam.position.length() * (direction > 0 ? 0.82 : 1.22);
    next = Math.max(170, Math.min(1600, next));
    cam.position.setLength(next);
    graph3D.controls.update();
  }

  function setGraphMode(mode) {
    state.graphMode = mode;
    localStorage.setItem("kb_graph_mode", mode);
    syncGraphModeButtons();
    renderGraphView();
  }

  function setGraphDesign(design) {
    state.graphDesign = design || "stellar";
    localStorage.setItem("kb_graph_design", state.graphDesign);
    if (state.graphMode === "3d") renderGraphView();
  }

  function syncGraphModeButtons() {
    var b2 = $("graphMode2d"), b3 = $("graphMode3d");
    if (b2) b2.classList.toggle("active", state.graphMode === "2d");
    if (b3) b3.classList.toggle("active", state.graphMode === "3d");
    var dw = $("graphDesignWrap");
    if (dw) dw.classList.toggle("hidden", state.graphMode !== "3d");
  }

  function fitGraph(animated) {
    if (state.graphMode === "3d") { fitGraph3D(animated); return; }
    if (!graphNetwork) return;
    try {
      graphNetwork.fit({
        animation: animated ? { duration: 350, easingFunction: "easeInOutQuad" } : false,
        offset: { x: -20, y: -10 }
      });
    } catch(e) {}
  }

  function zoomGraph(direction) {
    if (state.graphMode === "3d") { zoomGraph3D(direction); return; }
    if (!graphNetwork) return;
    try {
      var next = Math.max(0.15, Math.min(4, graphNetwork.getScale() + direction * 0.25));
      graphNetwork.moveTo({
        scale: next,
        position: graphNetwork.getViewPosition(),
        animation: { duration: 200, easingFunction: "easeInOutQuad" }
      });
    } catch(e) {}
  }

  function refreshGraph() {
    if (graphNetwork) { graphNetwork.destroy(); graphNetwork = null; }
    destroyGraph3D();
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

