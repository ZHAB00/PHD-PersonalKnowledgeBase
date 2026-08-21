(function() {
  "use strict";

  var _origFetch = window.fetch;
  var _authRefreshing = null;

  function _refreshAuth() {
    if (_authRefreshing) return _authRefreshing;
    _authRefreshing = _origFetch("/api/auth/local-token").then(function(r) {
      if (!r.ok) throw new Error("local-token failed");
      return r.json();
    }).then(function(d) {
      if (!d || !d.access_token) throw new Error("local-token empty");
      localStorage.setItem("kb_token", d.access_token);
      localStorage.setItem("kb_username", d.username || "local");
      localStorage.setItem("kb_user_id", d.user_id || "local");
      state.userId = d.user_id || "local";
    }).catch(function(e) {
      localStorage.removeItem("kb_token");
      if (window.location.pathname !== "/login") window.location.href = "/login";
      throw e;
    }).finally(function() { _authRefreshing = null; });
    return _authRefreshing;
  }

  window.fetch = function(url, opts) {
    opts = opts || {};
    var isApi = typeof url === "string" && url.indexOf("/api/") === 0;
    var isPublicAuth = typeof url === "string" && (
      url.indexOf("/api/auth/login") === 0 ||
      url.indexOf("/api/auth/local-token") === 0 ||
      url.indexOf("/api/auth/login-local") === 0 ||
      url.indexOf("/api/settings/public") === 0
    );
    if (isApi && !isPublicAuth) {
      var token = localStorage.getItem("kb_token");
      if (token) {
        if (!opts.headers) opts.headers = {};
        if (!(opts.headers instanceof Headers)) opts.headers = new Headers(opts.headers);
        if (!opts.headers.has("Authorization")) opts.headers.set("Authorization", "Bearer " + token);
      }
    }
    var req = _origFetch(url, opts);
    if (!isApi || isPublicAuth) return req;
    return req.then(function(resp) {
      if (resp.status !== 401) return resp;
      return _refreshAuth().then(function() {
        var retryOpts = Object.assign({}, opts);
        retryOpts.headers = opts.headers ? new Headers(opts.headers) : new Headers();
        var newToken = localStorage.getItem("kb_token");
        if (newToken) retryOpts.headers.set("Authorization", "Bearer " + newToken);
        return _origFetch(url, retryOpts);
      });
    });
  };

  var state = {
    sessionId: localStorage.getItem("kb_session_id") || "",
    tenantId: "default",
    graphRagEnabled: true,
    graphMode: localStorage.getItem("kb_graph_mode") || "2d",
    graphDesign: localStorage.getItem("kb_graph_design") || "stellar",
    graphKbId: localStorage.getItem("kb_graph_kb_id") || "default",
    chatKbId: localStorage.getItem("kb_chat_kb_id") || "default",
    docKbId: localStorage.getItem("kb_doc_kb_id") || "default",
    userId: localStorage.getItem("kb_user_id") || "default",
    streaming: false,
    _rdlAbort: null,
    _rdlTimer: null,
    _lklAbort: null
  }
  var _settingsLoading = false;
  window.toggleGraphRag = toggleGraphRag;

  function $(id) { return document.getElementById(id); }

  // 会话视图辅助函数：返回或创建会话专属 DOM 容器
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
  function gid() { return "sess_" + Date.now() + "_" + Math.random().toString(36).substr(2, 9); }
  function gsl() { try { return JSON.parse(localStorage.getItem("kb_sessions") || "[]"); } catch(e) { return []; } }
  function ssl(l) { localStorage.setItem("kb_sessions", JSON.stringify(l)); }

  function uts(sid) {
    var l = gsl(), f = l.find(function(s) { return s.id === sid; });
    if (f) f.updated = Date.now(); else { l.push({ id: sid, created: Date.now(), updated: Date.now(), label: "" }); fetch("/api/chat/sessions/save", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({id: sid, title: "", user_id: state.userId}) }).catch(function(){}); }
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
        body: JSON.stringify({id: sid, title: newTitle, user_id: state.userId})
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
      var rawLb = (s.label || s.title || "") === "???" ? "" : (s.label || s.title || "新建对话");
      var lb = stripMd(rawLb);
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
    if (sid === state.sessionId) { scv(); return; }
    // 隐藏所有视图，显示目标视图
    var cm = document.getElementById("chatMessages");
    if (cm) {
      cm.querySelectorAll(".session-view").forEach(function(v) {
        v.style.display = (v.getAttribute("data-sid") === sid) ? "" : "none";
      });
    }
    state.sessionId = sid; localStorage.setItem("kb_session_id", sid);
    // 视图为空时加载历史记录
    var view = cv(sid);
    if (!view.querySelector(".message") && !view.querySelector(".chat-empty")) {
      lch();
    }
    rsl(); scv(); stbLater();
  }

  function dls(sid) {
    var list = gsl();
    var f = list.find(function(s) { return s.id === sid; });
    var rawLabel = ((f && (f.label || f.title)) || "") === "???" ? "" : ((f && (f.label || f.title)) || "新建对话");
    var label = stripMd(rawLabel);
    showConfirm("确定要删除对话“" + label + "”吗？", function() {
      fetch("/api/chat/clear/" + sid, { method: "POST" }).catch(function(){});
      var deletedView = document.querySelector('.session-view[data-sid="' + sid + '"]');
      if (deletedView && deletedView.parentNode) deletedView.remove();
      var l = gsl().filter(function(s) { return s.id !== sid; });
      l.sort(function(a,b) { return (b.updated||0) - (a.updated||0); });
      ssl(l);
      if (sid === state.sessionId) {
        if (l.length > 0) {
          state.sessionId = l[0].id;
          localStorage.setItem("kb_session_id", state.sessionId);
          var cm = document.getElementById("chatMessages");
          if (cm) {
            cm.querySelectorAll(".session-view").forEach(function(v) {
              v.style.display = (v.getAttribute("data-sid") === state.sessionId) ? "" : "none";
            });
          }
          lch();
        } else {
          showNoSession();
        }
      }
      rsl();
    });
  }

  function sns() {
    state.sessionId = gid(); localStorage.setItem("kb_session_id", state.sessionId);
    var cm = document.getElementById("chatMessages");
    if (cm) {
      cm.querySelectorAll(".session-view").forEach(function(v) { v.style.display = "none"; });
    }
    var view = cv(state.sessionId);
    view.style.display = "";
    view.innerHTML = '<div class="chat-empty"><div class="chat-empty-icon">💬</div><p>开始一段新的对话吧</p><p class="chat-empty-hint">在下方输入你的问题，AI 会基于知识库为你解答</p></div>';
    uts(state.sessionId); rsl(); scv(); $("chatInput").focus();
  }

  function showNoSession() {
    state.sessionId = ""; localStorage.removeItem("kb_session_id");
    var cm = document.getElementById("chatMessages"); if (!cm) return;
    cm.querySelectorAll(".session-view").forEach(function(v) { v.style.display = "none"; });
    var view = cv("");
    view.style.display = "";
    view.innerHTML = '<div class="chat-empty"><div class="chat-empty-icon">💬</div><p>开始一段新的对话吧</p><p class="chat-empty-hint">在下方输入你的问题，AI 会基于知识库为你解答</p></div>';
    rsl();
  }

  function scv() { switchView("chat"); }
  function switchView(name) {
    // 隐藏所有视图
    var views = document.querySelectorAll(".view");
    views.forEach(function(v) { v.classList.remove("active"); });
        var target = document.getElementById("view-" + name);
    if (target) target.classList.add("active");
    if (name !== "graph") stopGraphBackground();
    // 高亮导航
    var navs = document.querySelectorAll(".nav-item-side");
    navs.forEach(function(n) { n.classList.remove("active"); });
    if (name === "chat") {
      var nc = document.getElementById("navChat"); if (nc) nc.classList.add("active");
    } else if (name === "documents") {
      var nd = document.getElementById("navDocuments"); if (nd) nd.classList.add("active");
      rdl(); lkl();
    } else if (name === "graph") {
      var ng = document.getElementById("navGraph"); if (ng) ng.classList.add("active");
    } else if (name === "settings") {
      var ns = document.getElementById("navSettings"); if (ns) ns.classList.add("active");
      loadSettings(false);
    }
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
  function stbLater() { stb(); requestAnimationFrame(stb); setTimeout(stb, 80); }


  function rmd(el, text) {
    try {
      el.innerHTML = marked.parse(text);
      try { renderMathInElement(el, { delimiters: [{left: "$\$", right: "$\$", display: true}, {left: "$", right: "$", display: false}] }); } catch(e) {}
    } catch(e) { el.textContent = text; }
  }
  function makeMsg(role, txt) {
    var d = document.createElement("div");
    d.className = "message " + role;
    d.innerHTML = '<div class="message-avatar">' + (role === "assistant" ? "AI" : "我") +
      '</div><div class="message-content"><p></p></div>';
    if (txt) d.querySelector("p").textContent = txt;
    return d;
  }

  function ams(role, txt, sid) {
    var d = document.createElement("div");
    d.className = "message " + role;
    d.innerHTML = '<div class="message-avatar">' + (role === "assistant" ? "AI" : "我") +
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

  function buildToolSourceList(sources) {
    if (!sources || !sources.length) return "";
    return sources.map(function(s) {
      var score = s.score != null ? Math.round(s.score * 100) + "%" : "";
      return '<div class="tool-source">' +
        '<div class="tool-source-head"><span class="tool-source-file">' + esc(s.filename || s.doc_id || "文档") + '</span>' +
        (score ? '<span class="tool-source-score">' + score + '</span>' : '') + '</div>' +
        '<div class="tool-source-text">' + esc((s.content || "").slice(0, 140)) + '</div></div>';
    }).join("");
  }

  function parseToolResult(resultStr) {
    try { return JSON.parse(resultStr || ""); } catch(e) { return null; }
  }

  function isToolResultError(tc) {
    var data = parseToolResult(tc && tc.result);
    return !!(data && typeof data === "object" && (data.status === "error" || data.error));
  }

  function buildToolDetail(tc) {
    var name = tc && tc.tool_name || "";
    var data = parseToolResult(tc && tc.result);
    var sources = (tc && tc.sources) || [];
    if (name === "search_knowledge_base") {
      if (!sources.length && data && data.results) sources = data.results;
      var graphSources = sources.filter(function(s) {
        return s.filename === "[知识图谱]" || s.doc_id === "graph_rag" || s.is_graph;
      });
      var docSources = sources.filter(function(s) {
        return s.filename !== "[知识图谱]" && s.doc_id !== "graph_rag" && !s.is_graph;
      });
      var html = "";
      if (data && data.error) {
        html += '<div class="tool-detail-text">' + esc(data.error) + '</div>';
      }
      if (docSources.length) {
        html += '<div class="tool-detail-block rag-detail"><div class="tool-detail-title">文档检索（RAG）</div>' +
          buildToolSourceList(docSources) + '</div>';
      } else if (!data || !data.error) {
        html += '<div class="tool-detail-block rag-detail"><div class="tool-detail-title">文档检索（RAG）</div><div class="tool-detail-text">未检索到相关片段</div></div>';
      }
      if (graphSources.length) {
        html += '<div class="tool-detail-block graph-detail"><div class="tool-detail-title">知识图谱检索</div>' +
          graphSources.map(function(s) {
            return '<div class="tool-source graph-source"><div class="tool-source-text">' +
              esc((s.content || s.text || "").slice(0, 300)) + '</div></div>';
          }).join("") + '</div>';
      }
      return html;
    }
    if (name === "web_search") {
      if (data.error) return '<div class="tool-detail-text">' + esc(data.error) + '</div>';
      var webResults = data.results || [];
      if (!webResults.length) return '<div class="tool-detail-text">未检索到相关网页</div>';
      return '<div class="tool-detail-block web-detail"><div class="tool-detail-title">网页结果 ' + (data.count != null ? "（" + data.count + " 条）" : "") + '</div>' +
        webResults.map(function(r) {
          return '<div class="tool-source web-result"><div class="tool-source-head">' +
            '<a class="web-result-title" href="' + esc(r.url || "#") + '" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">' + esc(r.title || r.url || "") + '</a>' +
            '<span class="web-result-url">' + esc(r.url || "") + '</span></div>' +
            (r.content ? '<div class="tool-source-text">' + esc(r.content) + '</div>' : '') +
            '</div>';
        }).join("") + '</div>';
    }
    if (!data || typeof data !== "object") return "";
    if (name === "doc_stats") {
      var lines = [];
      if (data.doc_count != null) lines.push("文档数: " + data.doc_count);
      if (data.file_count != null) lines.push("文件数: " + data.file_count);
      if (data.total_chunks != null) lines.push("分块数: " + data.total_chunks);
      if (data.error) lines.push("错误: " + data.error);
      var files = (data.file_list || []).slice(0, 10);
      if (files.length) lines.push("文件: " + files.join("、"));
      return lines.length ? '<div class="tool-detail-text">' + esc(lines.join("<br>")) + '</div>' : "";
    }
    if (name === "memory_search") {
      if (data.memories && data.memories.length) {
        return data.memories.map(function(mem) {
          return '<div class="tool-source"><div class="tool-source-text">' + esc(mem.content || "") + '</div></div>';
        }).join("");
      }
      return data.error ? '<div class="tool-detail-text">' + esc(data.error) + '</div>' : "";
    }
    if (name === "calculator") {
      if (data.result != null) {
        return '<div class="tool-detail-text">' + esc(String(data.expression != null ? data.expression + " = " : "") + data.result) + '</div>';
      }
      return data.error ? '<div class="tool-detail-text">' + esc(data.error) + '</div>' : "";
    }
    if (name === "remember") {
      var parts = [];
      if (data.action) parts.push("动作: " + data.action);
      if (data.content_preview) parts.push("内容: " + data.content_preview);
      if (data.importance != null) parts.push("重要性: " + data.importance);
      return parts.length ? '<div class="tool-detail-text">' + esc(parts.join("<br>")) + '</div>' : "";
    }
    var generic = [];
    Object.keys(data).slice(0, 6).forEach(function(k) {
      var v = data[k];
      if (v && typeof v === "object") v = JSON.stringify(v).slice(0, 80);
      generic.push(k + ": " + String(v));
    });
    return generic.length ? '<div class="tool-detail-text">' + esc(generic.join("<br>")) + '</div>' : "";
  }

  function toggleToolBubble(div) {
    var w = div.querySelector(".tool-sources-wrap");
    if (!w) return;
    var open = w.classList.toggle("open");
    var btn = div.querySelector(".tool-toggle-btn");
    if (btn) btn.textContent = open ? "收起" : "展开";
  }

  function makeToolBubble(tc) {
    var d = document.createElement("div");
    d.className = "message-tool";
    var detail = buildToolDetail(tc);
    d.innerHTML = '<span class="tool-icon">&#9881;</span>' +
      '<span class="tool-label">' + esc(tc && tc.tool_name || "tool") + '</span>' +
      '<span class="tool-toggle">' + (detail ? "&#9662;" : "") + '</span>' +
      '<div class="tool-sources-wrap">' + detail + '</div>';
    if (detail) d.classList.add("has-sources");
    d.addEventListener("click", function(ev) {
      var wrap = d.querySelector(".tool-sources-wrap");
      if (wrap) {
        var open = wrap.classList.toggle("open");
        var tg = d.querySelector(".tool-toggle");
        if (tg) tg.innerHTML = open ? "&#9652;" : "&#9662;";
      }
    });
    return d;
  }

  function makeToolRoundBubble(toolCalls) {
    var d = document.createElement("div");
    d.className = "message assistant thinking";
    var names = (toolCalls || []).map(function(tc) { return tc.tool_name; }).filter(Boolean);
    var details = "";
    (toolCalls || []).forEach(function(tc) {
      var html = buildToolDetail(tc);
      if (html) {
        details += '<div class="tool-detail-block"><div class="tool-detail-title">' + esc(tc.tool_name || "工具") + '</div>' + html + '</div>';
      }
    });
    d.innerHTML = '<div class="message-avatar">&#128269;</div>' +
      '<div class="message-content"><div class="thinking-label">调用工具: ' + esc(names.join(", ")) +
      (details ? ' <button type="button" class="tool-toggle-btn">展开</button>' : '') + '</div>' +
      (details ? '<div class="tool-sources-wrap">' + details + '</div>' : '') + '</div>';
    if (details) {
      d.classList.add("has-sources");
      var btn = d.querySelector(".tool-toggle-btn");
      d.addEventListener("click", function() { toggleToolBubble(d); });
      if (btn) btn.addEventListener("click", function(ev) {
        ev.stopPropagation();
        toggleToolBubble(d);
      });
    }
    return d;
  }

  function makeToolCallBubble(tc, done) {
    var d = document.createElement("div");
    d.className = "message assistant thinking tool-call-bubble" + (done ? " tool-done" : " thinking-pending");
    var name = tc && tc.tool_name || "tool";
    var args = tc && tc.arguments ? JSON.stringify(tc.arguments) : "";
    if (args.length > 80) args = args.slice(0, 80) + "...";
    var statusHtml = done
      ? '<span class="tool-call-status done">&#10003; 已完成</span>'
      : '<span class="tool-call-status"><span class="tool-spinner"></span> 执行中...</span>';
    d.innerHTML = '<div class="message-avatar">&#128269;</div>' +
      '<div class="message-content"><div class="thinking-label">' + esc(name) +
      (args ? ' <span class="tool-call-args" title="' + esc(args) + '">' + esc(args) + '</span>' : '') +
      statusHtml + '</div></div>';
    d._toolName = name;
    return d;
  }

  function markToolCallDone(div) {
    if (!div) return;
    div.classList.remove("thinking-pending");
    div.classList.add("tool-done");
    var status = div.querySelector(".tool-call-status");
    if (status) {
      status.className = "tool-call-status done";
      status.innerHTML = '&#10003; 已完成';
    }
  }

  function makeToolResultBubble(tc) {
    var d = document.createElement("div");
    var name = tc && tc.tool_name || "tool";
    var parsedResult = parseToolResult(tc && tc.result);
    var isError = isToolResultError(tc) || (!parsedResult && !!(tc && tc.status === "error"));
    d.className = "message assistant thinking tool-result-bubble" + (isError ? " tool-error" : "");
    var detail = buildToolDetail(tc);
    if (!detail) {
      var rawResult = tc && tc.result ? String(tc.result).slice(0, 500) : "";
      detail = rawResult
        ? '<div class="tool-detail-text">' + esc(rawResult) + '</div>'
        : '<div class="tool-detail-text">\u65e0\u8fd4\u56de\u5185\u5bb9</div>';
    }
    var label = isError ? name + " \u6267\u884c\u5931\u8d25" : name + " \u7ed3\u679c";
    d.innerHTML = '<div class="message-avatar">' + (isError ? '&#9888;' : '&#9989;') + '</div>' +
      '<div class="message-content"><div class="thinking-label">' + esc(label) +
      (detail ? ' <button type="button" class="tool-toggle-btn">展开</button>' : '') + '</div>' +
      (detail ? '<div class="tool-sources-wrap">' + detail + '</div>' : '') + '</div>';
    if (detail) {
      d.classList.add("has-sources");
      d.addEventListener("click", function() { toggleToolBubble(d); });
      var btn = d.querySelector(".tool-toggle-btn");
      if (btn) btn.addEventListener("click", function(ev) {
        ev.stopPropagation();
        toggleToolBubble(d);
      });
    }
    return d;
  }

  function renderHistoryMessage(v, m, prepend) {
    if (!v || !m) return;
    var frag = document.createDocumentFragment();
    if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
      for (var j = 0; j < m.tool_calls.length; j++) {
        var tci = m.tool_calls[j];
        frag.appendChild(makeToolCallBubble({tool_name: tci.tool_name, arguments: tci.arguments || {}}, true));
        if (tci.result || (tci.sources && tci.sources.length)) {
          frag.appendChild(makeToolResultBubble({tool_name: tci.tool_name, result: tci.result || "", sources: tci.sources || [], status: tci.status}));
        }
      }
    }
    var el = makeMsg(m.role === "user" ? "user" : "assistant", "");
    rmd(el.querySelector("p"), m.content || "");
    frag.appendChild(el);
    if (prepend) v.insertBefore(frag, v.firstChild); else v.appendChild(frag);
  }

  function mkThinkingBubble(sid) {
    var d = document.createElement("div");
    d.className = "message assistant thinking thinking-pending";
    d.innerHTML = '<div class="message-avatar">&#128269;</div>' +
      '<div class="message-content"><div class="thinking-label">正在思考...</div></div>';
    var v = cv(sid);
    if (v) { v.appendChild(d); stb(); }
    return d;
  }

  async function sdm() {
    var inp = $("chatInput"); var txt = inp.value.trim();
    if (!txt || state.streaming) return;
    if (!state.sessionId) {
      state.sessionId = gid();
      localStorage.setItem("kb_session_id", state.sessionId);
      uts(state.sessionId);
    }
    var chatBox = document.getElementById("chatMessages");
    if (chatBox) {
      chatBox.querySelectorAll(".session-view").forEach(function(v) {
        v.style.display = (v.getAttribute("data-sid") === state.sessionId) ? "" : "none";
      });
    }
    inp.value = ""; inp.style.height = "auto";
    var mySid = state.sessionId;
    ams("user", txt, mySid);

    var l = gsl(); var f = l.find(function(s) { return s.id === state.sessionId; });
    if (f && (!f.label || f.label === "???")) {
      fetch("/api/chat/title", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({session_id: state.sessionId, message: txt})
      }).then(function(r){ return r.json(); }).then(function(d){
        if (d && d.title) { f.label = d.title; ssl(l); rsl(); fetch("/api/chat/sessions/save", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({id: state.sessionId, title: d.title, user_id: "default"}) }).catch(function(){}); }
      }).catch(function(){});
    }

    // --- 动态气泡：每次工具调用一个气泡 + 思考指示 ---
    var thinkingPending = mkThinkingBubble(mySid);
    var toolCallQueue = [];
    var answerDiv = null;
    var inAnswer = false;
    var full = "";

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
    var stp = document.getElementById("stopBtn"); if (stp) { stp.disabled = false; stp.style.display = ""; }

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
          var raw = ln.slice(6);
          var dt = raw;
          try { dt = JSON.parse(raw); } catch(e) { dt = raw; }
          if (dt === "[DONE]") break;
          if (typeof dt !== "string") continue;
          if (dt.startsWith("__SOURCES__:")) continue;
          if (dt.startsWith("__REASONING__:")) {
            if (!thinkingPending) thinkingPending = mkThinkingBubble(mySid);
            continue;
          }
          if (dt.startsWith("__TOOL_CALL__:")) {
            if (!thinkingPending) thinkingPending = mkThinkingBubble(mySid);
            try {
              var tc = JSON.parse(dt.slice(14));
              var bubble = makeToolCallBubble({tool_name: tc.name, arguments: tc.args || {}});
              var v = cv(mySid);
              if (v) { v.appendChild(bubble); stb(); }
              toolCallQueue.push(bubble);
            } catch(e) {}
            continue;
          }
          if (dt.startsWith("__TOOL_RESULT__:")) {
            try {
              var tr = JSON.parse(dt.slice(16));
              var target = null;
              for (var qi = 0; qi < toolCallQueue.length; qi++) {
                if (toolCallQueue[qi]._toolName === tr.name) {
                  target = toolCallQueue[qi];
                  toolCallQueue.splice(qi, 1);
                  break;
                }
              }
              if (!target) target = toolCallQueue.shift();
              if (target) markToolCallDone(target);
              var rb = makeToolResultBubble({
                tool_name: tr.name,
                result: tr.result || "",
                status: tr.status || (isToolResultError(tr) ? "error" : "ok")
              });
              var rv = cv(mySid);
              if (rv) { rv.appendChild(rb); stb(); }
            } catch(e) {}
            continue;
          }
          // 文本内容 -> 回答气泡
          if (thinkingPending) { thinkingPending.remove(); thinkingPending = null; }
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
      if (thinkingPending) { thinkingPending.remove(); thinkingPending = null; }
      uts(state.sessionId);

    } catch(e) {
      if (thinkingPending) { thinkingPending.remove(); thinkingPending = null; }
      if (state.abortController && state.abortController.signal.aborted) { /* user navigated away */ }
      else { if (!answerDiv) { answerDiv = mkAnswer(); } rmd(answerDiv.querySelector("p"), "**Error:** " + e.message); }
    } finally {
      if (thinkingPending) { thinkingPending.remove(); thinkingPending = null; }
      state.abortController = null;
      state.streaming = false; if (sb) sb.disabled = false;
      if (stp) { stp.disabled = true; stp.style.display = "none"; }
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
        renderHistoryMessage(v, m, false);
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
      stbLater();
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
        renderHistoryMessage(v, m, true);
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
    // 取消上一次进行中的请求
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
         var graphSel = state.graphKbId || "default";
         var graphSelFound = false;
         (data || []).forEach(function(kb) {
           var o = document.createElement("option");
           o.value = kb.id; o.textContent = kb.name;
           if (kb.id === graphSel) { o.selected = true; graphSelFound = true; }
           gks.appendChild(o);
         });
         if (!graphSelFound) { gks.value = "default"; state.graphKbId = "default"; localStorage.setItem("kb_graph_kb_id", "default"); }
       }
      updateKbButtons();
    } catch(e) {}
  }

  function loadSettings(firstRun) {
    if (_settingsLoading) return;
    _settingsLoading = true;
    var loading = $("settingsLoading");
    if (loading) loading.style.display = "flex";
    fetch("/api/settings").then(function(r){ return r.json(); }).then(function(s) {
      $("setChatProvider").value = s.chat_provider || "deepseek";
      $("setChatBaseUrl").value = s.chat_base_url || "";
      $("setChatApiKey").value = s.chat_api_key || "";
      $("setChatModel").value = s.chat_model || "";
      $("setChatThinking").checked = !!s.chat_thinking;
      var cw = $("setChatContextWindow"); if (cw) cw.value = s.chat_context_window || 0;
      $("setEmbeddingProvider").value = s.embedding_provider || "ollama";
      $("setEmbeddingModel").value = s.embedding_model || "";
      $("setEmbeddingBaseUrl").value = s.embedding_base_url || "";
      $("setEmbeddingApiKey").value = s.embedding_api_key || "";
      $("setSearchProvider").value = s.search_provider || "auto";
      $("setSearchBaseUrl").value = s.search_base_url || "";
      $("setSearchApiKey").value = s.search_api_key || "";
      $("setNeo4jEnabled").checked = !!s.neo4j_enabled;
      $("setNeo4jUri").value = s.neo4j_uri || "";
      $("setNeo4jUser").value = s.neo4j_user || "";
      $("setNeo4jPassword").value = s.neo4j_password || "";
      $("setNeo4jDatabase").value = s.neo4j_database || "";
      $("setRequirePassword").checked = !!s.require_password;
      var appPort = $("setAppPort"); if (appPort) appPort.value = s.app_port || 8001;
      var closeTray = $("setCloseToTray"); if (closeTray) closeTray.checked = !!s.close_to_tray;
      state._savedPort = parseInt(s.app_port || "8001", 10);
      var dd = $("setDataDir"); if (dd) dd.textContent = s.data_dir || "";
      if (firstRun && !s.configured) showOnboarding();
    }).catch(function(){}).finally(function() {
      _settingsLoading = false;
      if (loading) loading.style.display = "none";
    });
  }

  function collectSettings() {
    return {
      chat_provider: $("setChatProvider").value,
      chat_base_url: $("setChatBaseUrl").value.trim(),
      chat_api_key: $("setChatApiKey").value,
      chat_model: $("setChatModel").value.trim(),
      chat_thinking: $("setChatThinking").checked,
      chat_context_window: Math.max(0, parseInt($("setChatContextWindow").value || "0", 10) || 0),
      embedding_provider: $("setEmbeddingProvider").value,
      embedding_model: $("setEmbeddingModel").value.trim(),
      embedding_base_url: $("setEmbeddingBaseUrl").value.trim(),
      embedding_api_key: $("setEmbeddingApiKey").value,
      search_provider: $("setSearchProvider").value,
      search_base_url: $("setSearchBaseUrl").value.trim(),
      search_api_key: $("setSearchApiKey").value,
      neo4j_enabled: $("setNeo4jEnabled").checked,
      neo4j_uri: $("setNeo4jUri").value.trim(),
      neo4j_user: $("setNeo4jUser").value.trim(),
      neo4j_password: $("setNeo4jPassword").value,
      neo4j_database: $("setNeo4jDatabase").value.trim(),
      require_password: $("setRequirePassword").checked,
      password: $("setPassword").value,
      app_port: parseInt($("setAppPort").value || "8001", 10),
      close_to_tray: $("setCloseToTray").checked
    };
  }

  function saveSettings() {
    var body = JSON.stringify(collectSettings());
    return fetch("/api/settings", { method: "PUT", headers: {"Content-Type":"application/json"}, body: body })
      .then(function(r){ return r.json(); })
      .then(function(s) {
        var el = $("settingsSaveResult"); if (el) { el.textContent = "已保存"; el.style.color = "#16a34a"; }
        $("setChatApiKey").value = s.chat_api_key || "";
        $("setEmbeddingApiKey").value = s.embedding_api_key || "";
        $("setSearchApiKey").value = s.search_api_key || "";
        $("setNeo4jPassword").value = s.neo4j_password || "";
        $("setPassword").value = "";
        var newPort = parseInt($("setAppPort").value || "8001", 10);
        if (state._savedPort && state._savedPort !== newPort) {
          var hint = $("appPortHint");
          if (hint) hint.textContent = "端口已保存，点击“保存并重启”立即生效";
        }
        state._savedPort = newPort;
      })
      .catch(function(e) {
        var el = $("settingsSaveResult"); if (el) { el.textContent = "保存失败: " + (e.message || e); el.style.color = "#dc2626"; }
        throw e;
      });
  }

  function testChat() {
    var s = collectSettings();
    var btn = $("testChat"), el = $("chatTestResult");
    if (btn) btn.disabled = true;
    if (el) { el.textContent = "测试中..."; el.style.color = "#6366f1"; el.classList.add("settings-hint-loading"); }
    fetch("/api/settings/test/chat", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(s) })
      .then(function(r){ return r.json(); }).then(function(d) {
        el.textContent = d.ok ? ("连接成功 " + d.elapsed + "s：" + (d.reply||"")) : ("连接失败: " + (d.error||""));
        el.style.color = d.ok ? "#16a34a" : "#dc2626";
      }).catch(function(e){ el.textContent="测试失败: "+e.message; el.style.color="#dc2626"; })
      .finally(function(){ if (btn) btn.disabled = false; if (el) el.classList.remove("settings-hint-loading"); });
  }

  function testEmbedding() {
    var btn = $("testEmbedding"), el = $("embeddingTestResult");
    if (btn) btn.disabled = true;
    if (el) { el.textContent = "测试中..."; el.style.color = "#6366f1"; el.classList.add("settings-hint-loading"); }
    fetch("/api/settings/test/embedding", { method: "POST" }).then(function(r){ return r.json(); }).then(function(d) {
      el.textContent = d.ok ? ("连接成功 " + d.elapsed + "s，维度 " + d.dim) : ("连接失败: " + (d.error||""));
      el.style.color = d.ok ? "#16a34a" : "#dc2626";
    }).catch(function(e){ el.textContent="测试失败: "+e.message; el.style.color="#dc2626"; })
      .finally(function(){ if (btn) btn.disabled = false; if (el) el.classList.remove("settings-hint-loading"); });
  }

  function testSearch() {
    var s = collectSettings();
    var btn = $("testSearch"), el = $("searchTestResult");
    if (btn) btn.disabled = true;
    if (el) { el.textContent = "测试中..."; el.style.color = "#6366f1"; el.classList.add("settings-hint-loading"); }
    fetch("/api/settings/test/search", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({
      query: "最新 AI 新闻",
      provider: s.search_provider,
      api_key: s.search_api_key,
      base_url: s.search_base_url
    }) })
      .then(function(r){ return r.json(); }).then(function(d) {
        if (d.ok) {
          el.textContent = "连接成功：" + d.provider + "，返回 " + d.count + " 条，首条 " + (d.first || "");
          el.style.color = "#16a34a";
        } else {
          el.textContent = "连接失败: " + (d.error || "");
          el.style.color = "#dc2626";
        }
      }).catch(function(e){ el.textContent="测试失败: "+e.message; el.style.color="#dc2626"; })
      .finally(function(){ if (btn) btn.disabled = false; if (el) el.classList.remove("settings-hint-loading"); });
  }

  function testNeo4j() {
    var uri = $("setNeo4jUri").value.trim();
    var user = $("setNeo4jUser").value.trim();
    var pwd = $("setNeo4jPassword").value;
    var btn = $("testNeo4j"), el = $("neo4jTestResult");
    if (btn) btn.disabled = true;
    if (el) { el.textContent = "测试中..."; el.style.color = "#6366f1"; el.classList.add("settings-hint-loading"); }
    fetch("/api/settings/test/neo4j?uri=" + encodeURIComponent(uri) + "&user=" + encodeURIComponent(user) + "&password=" + encodeURIComponent(pwd), { method: "POST" })
      .then(function(r){ return r.json(); }).then(function(d) {
        el.textContent = d.ok ? "连接成功" : ("连接失败: " + (d.error||""));
        el.style.color = d.ok ? "#16a34a" : "#dc2626";
      }).catch(function(e){ el.textContent="测试失败: "+e.message; el.style.color="#dc2626"; })
      .finally(function(){ if (btn) btn.disabled = false; if (el) el.classList.remove("settings-hint-loading"); });
  }

  function loadSystemStatus() {
    fetch("/api/settings/status").then(function(r){ return r.json(); }).then(function(d) {
      state.systemStatus = d;
      if (!d.ocr_available) {
        var w = $("ocrWarning"); if (w) w.style.display = "flex";
      }
      var ocr = $("setOcrStatus"); if (ocr) ocr.textContent = d.ocr_available ? "可用" : "不可用（扫描版 PDF 将跳过识别）";
      var bm = $("setBundleStatus"); if (bm) bm.textContent = d.bundled_model ? "已内置" : "未内置";
      var port = $("setAppPort"), pp = $("applyPortRestart"), hint = $("appPortHint");
      if (d.debug_mode) {
        if (port) { port.value = d.service_port || 8001; port.disabled = true; }
        if (pp) pp.disabled = true;
        if (hint) hint.textContent = "调试模式下端口由 .env 的 SERVICE_PORT 控制";
      }
    }).catch(function(){});
  }

  function applyPortRestart() {
    var btn = $("applyPortRestart"), el = $("appPortResult");
    if (btn) btn.disabled = true;
    if (el) { el.textContent = "正在重启后端..."; el.style.color = "#6366f1"; }
    saveSettings().then(function() {
      return fetch("/api/settings/apply-port", { method: "POST" }).then(function(r){ return r.json(); });
    })
      .then(function(d) {
        if (!d.ok) throw new Error(d.error || "重启请求失败");
        return waitForServer(d.port, 40000);
      })
      .then(function(port) {
        if (el) { el.textContent = "后端已在新端口启动，正在跳转..."; el.style.color = "#16a34a"; }
        window.location.href = "http://127.0.0.1:" + port;
      })
      .catch(function(e) {
        if (el) { el.textContent = "重启失败: " + (e.message || e); el.style.color = "#dc2626"; }
        if (btn) btn.disabled = false;
      });
  }

  function waitForServer(port, timeout) {
    var t0 = Date.now();
    return new Promise(function(resolve, reject) {
      function ping() {
        fetch("http://127.0.0.1:" + port + "/health", { cache: "no-store" })
          .then(function(r){ if (r.ok) { resolve(port); } else { retry(); } })
          .catch(retry);
      }
      function retry() {
        if (Date.now() - t0 > timeout) { reject(new Error("等待服务重启超时")); return; }
        setTimeout(ping, 800);
      }
      ping();
    });
  }

  function reindexKb() {
    showConfirm("将清理当前知识库的全部向量索引并重新解析文档，是否继续？", function() {
      var btn = $("btnKbReindex");
      if (btn) { btn.disabled = true; btn.classList.add("btn-loading"); }
      fetch("/api/kb/" + encodeURIComponent(state.docKbId) + "/reindex?tenant_id=" + state.tenantId, { method: "POST" })
        .then(function(r){ return r.json(); })
        .then(function(d) {
          if (!d || d.status !== "reindexing") throw new Error((d && d.detail) || "重建失败");
          setTimeout(function(){ rdl(); lkl(); }, 2000);
          if (btn) setTimeout(function(){ btn.disabled = false; btn.classList.remove("btn-loading"); }, 1500);
        })
        .catch(function(e) {
          if (btn) { btn.disabled = false; btn.classList.remove("btn-loading"); }
          alert("重建失败: " + (e.message || e));
        });
    });
  }

  function showOnboarding() {
    var ov = $("onboardingOverlay");
    if (!ov) return;
    ov.style.display = "flex";
    updateOnbSteps(1);
  }
  function hideOnboarding() {
    var ov = $("onboardingOverlay");
    if (ov) ov.style.display = "none";
  }
  function updateOnbSteps(step) {
    document.querySelectorAll(".onboarding-step").forEach(function(el) {
      el.classList.toggle("active", parseInt(el.getAttribute("data-step"), 10) === step);
    });
    var prev = $("onbPrev"), next = $("onbNext"), finish = $("onbFinish");
    if (prev) prev.style.display = step <= 1 ? "none" : "";
    if (next) next.style.display = step >= 3 ? "none" : "";
    if (finish) finish.style.display = step >= 3 ? "" : "none";
  }
  function onbNext() {
    var active = document.querySelector(".onboarding-step.active");
    var step = active ? parseInt(active.getAttribute("data-step"), 10) : 1;
    updateOnbSteps(step + 1);
  }
  function onbPrev() {
    var active = document.querySelector(".onboarding-step.active");
    var step = active ? parseInt(active.getAttribute("data-step"), 10) : 1;
    updateOnbSteps(step - 1);
  }
  function updateOnbChatConfig() {
    var val = document.querySelector('input[name="onbChat"]:checked');
    val = val ? val.value : "deepseek";
    var cfg = $("onbChatConfig");
    if (!cfg) return;
    var apiKey = $("onbChatApiKey"), base = $("onbChatBaseUrl"), model = $("onbChatModel");
    if (val === "ollama") {
      cfg.style.display = "block";
      if (base) base.value = "http://127.0.0.1:11434/v1";
      if (model) model.placeholder = "例如 qwen2.5:7b";
      if (apiKey) { apiKey.placeholder = "本地无需 Key"; apiKey.value = ""; }
    } else if (val === "lmstudio") {
      cfg.style.display = "block";
      if (base) base.value = "http://localhost:1234/v1";
      if (model) model.placeholder = "例如 local-model";
      if (apiKey) { apiKey.placeholder = "本地无需 Key"; apiKey.value = ""; }
    } else if (val === "openai_compatible") {
      cfg.style.display = "block";
      if (apiKey) apiKey.placeholder = "API Key";
    } else {
      cfg.style.display = "block";
      if (base) base.value = "";
      if (model) model.placeholder = "留空使用默认";
      if (apiKey) apiKey.placeholder = "DeepSeek API Key";
    }
  }
  function onbFinish() {
    var emb = document.querySelector('input[name="onbEmbedding"]:checked');
    var chat = document.querySelector('input[name="onbChat"]:checked');
    emb = emb ? emb.value : "local";
    chat = chat ? chat.value : "deepseek";
    $("setEmbeddingProvider").value = emb;
    if (emb === "local") {
      $("setEmbeddingModel").value = "BAAI/bge-small-zh-v1.5";
      $("setEmbeddingBaseUrl").value = "";
      $("setEmbeddingApiKey").value = "";
    } else if (emb === "ollama") {
      $("setEmbeddingModel").value = "qwen3-embedding:4b";
      $("setEmbeddingBaseUrl").value = "http://127.0.0.1:11434/v1";
    } else if (emb === "openai_compatible") {
      if (!$("setEmbeddingModel").value) $("setEmbeddingModel").value = "text-embedding-3-small";
    }
    $("setChatProvider").value = chat;
    $("setChatApiKey").value = $("onbChatApiKey").value || "";
    $("setChatBaseUrl").value = $("onbChatBaseUrl").value || "";
    $("setChatModel").value = $("onbChatModel").value || "";
    saveSettings().then(function() {
      hideOnboarding();
      scv();
      loadSystemStatus();
    }).catch(function(){});
  }
  function onbSkip() {
    saveSettings().then(function() {
      hideOnboarding();
      scv();
      loadSystemStatus();
    }).catch(function(){});
  }

  function updateKbButtons() {
    var bd = $("btnKbDel"), be = $("btnKbEdit");
    if (bd) bd.style.display = "";
    if (be) be.style.display = "";
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
    // 防抖：清除待执行定时器，设置新的 200ms 定时器
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
    // 取消上一次进行中的请求
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
    // 在现有行上显示重试状态
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
    var t = $("confirmText"); if (t && title) t.textContent = title;
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
    // 获取文件字节：需要使用重新索引接口
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
    var id = state.docKbId;
    $("kbModalTitle").textContent = "编辑知识库";
    $("kbName").value = "";
    $("kbDesc").value = "";
    $("kbModal").style.display = "flex";
    var cb = $("kbCreate");
    if (cb) {
      cb.textContent = "保存";
      cb.onclick = async function() {
        var nm = $("kbName").value.trim(); if (!nm) return;
        try {
          var resp = await fetch("/api/kb/" + encodeURIComponent(id), {
            method: "PUT", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({name: nm, description: $("kbDesc").value.trim()})
          });
          if (resp.ok) { $("kbModal").style.display = "none"; await lkl(); if (cb) { cb.textContent = "创建"; cb.onclick = ckb; } }
        } catch(e) {}
      };
    }
    try {
      var resp = await fetch("/api/kb/" + encodeURIComponent(id));
      if (resp.ok) {
        var kb = await resp.json();
        $("kbName").value = kb.name || "";
        $("kbDesc").value = kb.description || "";
      }
    } catch(e) {}
  }
  async function dkb() {
    if ($("kbDelCode").value.trim() !== "A1B2C3D4") {
      var h = $("kbDelHint"); if (h) { h.textContent = "Type A1B2C3D4 to confirm"; h.style.display = "block"; }
      return;
    }
    try {
      var resp = await fetch("/api/kb/" + state.docKbId + "?confirmation=A1B2C3D4&tenant_id=" + state.tenantId, { method: "DELETE" });
      if (!resp.ok) {
        var hint = $("kbDelHint");
        if (hint) {
          var msg = "Delete failed";
          try { var err = await resp.json(); if (err && err.detail) msg = String(err.detail); } catch(e2) {}
          hint.textContent = msg;
          hint.style.display = "block";
        }
        return;
      }
      $("kbDelModal").style.display = "none";
      $("kbDelCode").value = "";
      try {
        var listResp = await fetch("/api/kb/list?tenant_id=default");
        var kbs = await listResp.json();
        if (kbs && kbs.length) {
          var nextId = kbs[0].id;
          state.docKbId = nextId;
          state.chatKbId = nextId;
          localStorage.setItem("kb_doc_kb_id", nextId);
          localStorage.setItem("kb_chat_kb_id", nextId);
        }
      } catch(e) {}
      await lkl(); rdl();
    } catch(e) {
      var hint = $("kbDelHint");
      if (hint) { hint.textContent = "Delete failed: " + (e && e.message ? e.message : e); hint.style.display = "block"; }
    }
  }

  async function chk() {
    try {
      var resp = await fetch("/health");
      if (resp.ok) {
        var ci = $("chatInput"); if (ci) ci.disabled = false;
        var sb = $("sendBtn"); if (sb) sb.disabled = false;
      }
    } catch(e) {}
  }

  function on(id, evt, fn) { var el = $(id); if (el) el.addEventListener(evt, fn); }

  on("btnNewChat", "click", sns);
  on("navGraph", "click", function() { switchView("graph"); renderGraphView(); initGraphBuildState(); });
  on("navDocuments", "click", function() { switchView("documents"); rdl(); lkl(); });
  on("navSettings", "click", function() { switchView("settings"); });
  on("settingsBack", "click", function() { scv(); });
  on("saveSettings", "click", saveSettings);
  on("applyPortRestart", "click", applyPortRestart);
  on("btnKbReindex", "click", reindexKb);
  on("onbNext", "click", onbNext);
  on("onbPrev", "click", onbPrev);
  on("onbFinish", "click", onbFinish);
  on("onbSkip", "click", onbSkip);
  document.querySelectorAll('input[name="onbChat"]').forEach(function(r) { r.addEventListener("change", updateOnbChatConfig); });
  on("testChat", "click", testChat);
  on("testEmbedding", "click", testEmbedding);
  on("testSearch", "click", testSearch);
  on("testNeo4j", "click", testNeo4j);
  on("sendBtn", "click", sdm);
  on("stopBtn", "click", function() {
    if (state.abortController) state.abortController.abort();
  });

  // 图谱视图控件
  on("graphRefresh", "click", refreshGraph);
  on("graphBuild", "click", buildGraph);
  on("graphSearch", "keydown", function(e) { if (e.key === "Enter") refreshGraph(); });
  on("graphKbSelect", "change", function() { state.graphKbId = this.value; localStorage.setItem("kb_graph_kb_id", this.value); refreshGraph(); initGraphBuildState(); });
  on("graphFit", "click", function() { fitGraph(true); });
  on("graphZoomIn", "click", function() { zoomGraph(1); });
  on("graphZoomOut", "click", function() { zoomGraph(-1); });
  on("graphMode2d", "click", function() { setGraphMode("2d"); });
  on("graphMode3d", "click", function() { setGraphMode("3d"); });
  on("graphDesign", "change", function() { setGraphDesign(this.value); });
  on("chatInput", "keydown", function(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sdm(); } });
  on("chatInput", "input", function() { var ci = $("chatInput"); ci.style.height = "auto"; ci.style.height = Math.min(ci.scrollHeight, 120) + "px"; });
  document.querySelectorAll(".settings-nav-item").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var target = document.getElementById(btn.getAttribute("data-target"));
      var body = document.querySelector("#view-settings .settings-body");
      if (target && body) {
        var top = target.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 20;
        body.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      }
      document.querySelectorAll(".settings-nav-item").forEach(function(x) { x.classList.remove("active"); });
      btn.classList.add("active");
    });
  });
  var settingsScrollBody = document.querySelector("#view-settings .settings-body");
  if (settingsScrollBody) {
    settingsScrollBody.addEventListener("scroll", function() {
      var body = this;
      var sections = Array.prototype.slice.call(document.querySelectorAll("#view-settings .settings-group"));
      var current = sections[0];
      var pos = body.scrollTop + 120;
      sections.forEach(function(sec) {
        var top = sec.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
        if (top <= pos) current = sec;
      });
      document.querySelectorAll(".settings-nav-item").forEach(function(n) {
        n.classList.toggle("active", n.getAttribute("data-target") === current.id);
      });
    });
  }
  on("chatKbSelect", "change", function() { swChatKb($("chatKbSelect").value); });
  on("kbSelect", "change", function() { swDocKb($("kbSelect").value); });
  on("uploadZone", "click", function(e) { if (e.target.tagName !== "A") { var fi = $("fileInput"); if (fi) fi.click(); } });
  on("uploadZone", "dragover", function(e) { e.preventDefault(); });
  on("uploadZone", "drop", function(e) { e.preventDefault(); if (e.dataTransfer.files.length) upl(e.dataTransfer.files); });
  on("fileInput", "change", function() { var fi = $("fileInput"); if (fi.files.length) upl(fi.files); fi.value = ""; });
  on("btnKbAdd", "click", function() { $("kbModalTitle").textContent = "新建知识库"; $("kbName").value = ""; $("kbDesc").value = ""; var cb = $("kbCreate"); if(cb) { cb.textContent = "创建"; cb.onclick = ckb; } $("kbModal").style.display = "flex"; });
  on("btnKbEdit", "click", ekb);
  on("btnKbDel", "click", function() { $("kbDelCode").value = ""; var h = $("kbDelHint"); if(h) h.style.display = "none"; $("kbDelModal").style.display = "flex"; });
  on("kbCancel", "click", function() { $("kbModal").style.display = "none"; });
  on("kbCreate", "click", function() { var cb = $("kbCreate"); if (cb && cb.onclick) cb.onclick(); });
  on("kbDelCancel", "click", function() { $("kbDelModal").style.display = "none"; });
  on("confirmCancel", "click", function() { hideConfirm(); });
  on("confirmOk", "click", function() { var cb = _pendingDeleteId; hideConfirm(); if (cb) cb(); });
  on("kbDelOk", "click", dkb);

  if (!localStorage.getItem("kb_token")) {
    fetch("/api/auth/local-token").then(function(r){ return r.json(); }).then(function(d) {
      if (d && d.access_token) {
        localStorage.setItem("kb_token", d.access_token);
        localStorage.setItem("kb_username", d.username || "local");
        localStorage.setItem("kb_user_id", d.user_id || "local");
      }
      window.location.reload();
    }).catch(function() { window.location.href = "/login"; });
  } else {
    chk();
    setInterval(chk, 30000);
    syncGraphModeButtons();
    loadSettings(true);
    loadSystemStatus();
    if (state.sessionId) { rsl(); } else { showNoSession(); }
    synSessions().then(function() {
      if (!state.sessionId) {
        var list = gsl();
        if (list.length) {
          state.sessionId = list[0].id;
          localStorage.setItem("kb_session_id", state.sessionId);
          var chatBox = document.getElementById("chatMessages");
          if (chatBox) {
            chatBox.querySelectorAll(".session-view").forEach(function(v) {
              v.style.display = (v.getAttribute("data-sid") === state.sessionId) ? "" : "none";
            });
          }
        } else {
          showNoSession();
        }
      }
      rsl();
      return lkl().then(function() { rdl(); if (state.sessionId) lch(); });
    });
  }

  function synSessions() {
    return fetch("/api/chat/sessions?user_id=" + encodeURIComponent(state.userId)).then(function(r){ return r.json(); }).then(function(d){
      if (!d || !d.sessions || !d.sessions.length) return;
      var local = gsl(); var localMap = {};
      local.forEach(function(s) { localMap[s.id] = s; });
      d.sessions.forEach(function(ss) {
        if (localMap[ss.id]) {
          if (ss.title && (!localMap[ss.id].label || localMap[ss.id].label === "???")) localMap[ss.id].label = ss.title;
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

  setInterval(function() { rdl(); lkl(); }, 60000);


  // ====================================================================
  // 知识图谱可视化（vis-network）
  // ====================================================================
  var graphNetwork = null;
  var graph3D = null;

  function setGraphLoading(on) {
    var l = $("graphLoading");
    if (l) l.classList.toggle("hidden", !on);
  }

  function graph2dPalette(design) {
    if (design === "hologram") {
      return {
        nodeBg: "#67e8f9", nodeBorder: "#22d3ee", hoverBorder: "#a5f3fc",
        font: "#cffafe", fontStroke: "#062a33", shadow: "rgba(34,211,238,0.28)",
        edge: "#2dd4bf", edgeDashed: "#155e75", edgeFont: "#67e8f9",
        legendBg: "rgba(2,20,26,0.88)", legendBorder: "rgba(34,211,238,0.35)", legendText: "#a5f3fc"
      };
    }
    if (design === "neon") {
      return {
        nodeBg: "#f9a8d4", nodeBorder: "#ff2d95", hoverBorder: "#fbcfe8",
        font: "#fbcfe8", fontStroke: "#1d0717", shadow: "rgba(255,45,149,0.4)",
        edge: "#f472b6", edgeDashed: "#7c2d92", edgeFont: "#f9a8d4",
        legendBg: "rgba(24,8,20,0.9)", legendBorder: "rgba(244,114,182,0.4)", legendText: "#fbcfe8"
      };
    }
    if (design === "minimal") {
      return {
        nodeBg: "#64748b", nodeBorder: "#cbd5e1", hoverBorder: "#94a3b8",
        font: "#1e293b", fontStroke: "#ffffff", shadow: "rgba(15,23,42,0.14)",
        edge: "#94a3b8", edgeDashed: "#cbd5e1", edgeFont: "#475569",
        legendBg: "rgba(255,255,255,0.94)", legendBorder: "#d8dee8", legendText: "#334155"
      };
    }
    return {
      nodeBg: "#93c5fd", nodeBorder: "#818cf8", hoverBorder: "#e0e7ff",
      font: "#e2e8f0", fontStroke: "#0b1220", shadow: "rgba(0,0,0,0.5)",
      edge: "#8aa5ff", edgeDashed: "#475569", edgeFont: "#a5b4fc",
      legendBg: "rgba(10,15,30,0.9)", legendBorder: "rgba(129,140,248,0.4)", legendText: "#c7d2fe"
    };
  }
  var graphBgState = null;
  function stopGraphBackground() {
    if (!graphBgState) return;
    if (graphBgState.raf) cancelAnimationFrame(graphBgState.raf);
    if (graphBgState.canvas && graphBgState.canvas.parentNode) {
      graphBgState.canvas.parentNode.removeChild(graphBgState.canvas);
    }
    graphBgState = null;
  }

  function graphBackgroundSpec(design) {
    if (design === "hologram") {
      return {
        baseTop: "#062936", baseBottom: "#021319",
        grid: "rgba(34,211,238,0.08)", majorGrid: "rgba(34,211,238,0.13)",
        star: "#67e8f9", starHot: "#a5f3fc",
        ring: "rgba(103,232,249,0.20)", ring2: "rgba(129,140,248,0.16)",
        cross: "rgba(165,243,252,0.26)", scan: "rgba(34,211,238,0.12)",
        vignette: "rgba(1,15,20,0.28)"
      };
    }
    if (design === "neon") {
      return {
        baseTop: "#170818", baseBottom: "#080410",
        grid: "rgba(255,45,149,0.09)", majorGrid: "rgba(244,114,182,0.15)",
        star: "#f9a8d4", starHot: "#fbcfe8",
        ring: "rgba(255,45,149,0.20)", ring2: "rgba(34,211,238,0.14)",
        diamond: "rgba(249,168,212,0.22)", ray: "rgba(255,45,149,0.08)",
        glow: "rgba(255,45,149,0.14)", scan: "rgba(255,45,149,0.07)",
        vignette: "rgba(8,2,12,0.32)"
      };
    }
    if (design === "minimal") {
      return {
        baseTop: "#f8fafd", baseBottom: "#edf1f8",
        grid: "rgba(100,116,139,0.09)", majorGrid: "rgba(100,116,139,0.14)",
        star: "#64748b", starHot: "#818cf8",
        ring: "rgba(100,116,139,0.20)", ring2: "rgba(129,140,248,0.18)",
        wash1: "rgba(129,140,248,0.10)", wash2: "rgba(56,189,248,0.08)",
        accent: "rgba(100,116,139,0.16)", vignette: "rgba(51,65,85,0.08)"
      };
    }
    return {
      baseTop: "#0b1026", baseBottom: "#05070f",
      grid: "rgba(129,140,248,0.08)", majorGrid: "rgba(148,163,184,0.13)",
      star: "#dbeafe", starHot: "#a5f3fc",
      ring: "rgba(148,163,184,0.18)", ring2: "rgba(129,140,248,0.14)",
      nebula1: "rgba(99,102,241,0.17)", nebula2: "rgba(56,189,248,0.13)", nebula3: "rgba(217,70,239,0.09)",
      glow: "rgba(139,92,246,0.13)", vignette: "rgba(2,3,12,0.34)"
    };
  }

  function graphBgHash(seed, x, y) {
    var h = (seed | 0) ^ (Math.imul(x | 0, 374761393)) ^ (Math.imul(y | 0, 668265263));
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967295;
  }

  function graphBgCenter(network) {
    try {
      var ids = network.getNodeIds ? network.getNodeIds() : [];
      if (!ids || !ids.length) return { x: 0, y: 0 };
      var pos = network.getPositions(ids) || {};
      var sx = 0, sy = 0, n = 0;
      for (var k in pos) {
        if (pos[k] && typeof pos[k].x === "number") {
          sx += pos[k].x; sy += pos[k].y; n++;
        }
      }
      if (n) return { x: sx / n, y: sy / n };
    } catch(e) {}
    return { x: 0, y: 0 };
  }

  function drawGraphBgGrid(ctx, spec, left, top, right, bottom, scale, step, major) {
    ctx.save();
    ctx.lineWidth = 1 / scale;
    var x0 = Math.floor(left / step) * step;
    var y0 = Math.floor(top / step) * step;
    ctx.strokeStyle = spec.grid;
    ctx.beginPath();
    for (var x = x0; x <= right; x += step) { ctx.moveTo(x, top); ctx.lineTo(x, bottom); }
    for (var y = y0; y <= bottom; y += step) { ctx.moveTo(left, y); ctx.lineTo(right, y); }
    ctx.stroke();
    ctx.strokeStyle = spec.majorGrid || spec.grid;
    ctx.beginPath();
    var mx0 = Math.floor(left / major) * major;
    var my0 = Math.floor(top / major) * major;
    for (var mx = mx0; mx <= right; mx += major) { ctx.moveTo(mx, top); ctx.lineTo(mx, bottom); }
    for (var my = my0; my <= bottom; my += major) { ctx.moveTo(left, my); ctx.lineTo(right, my); }
    ctx.stroke();
    ctx.restore();
  }

  function drawGraphBgStars(ctx, spec, left, top, right, bottom, scale, seed) {
    var spacing = 170;
    var gx0 = Math.floor(left / spacing), gx1 = Math.floor(right / spacing);
    var gy0 = Math.floor(top / spacing), gy1 = Math.floor(bottom / spacing);
    var stepX = Math.max(1, Math.ceil((gx1 - gx0 + 1) / 38));
    var stepY = Math.max(1, Math.ceil((gy1 - gy0 + 1) / 26));
    ctx.save();
    for (var gx = gx0; gx <= gx1; gx += stepX) {
      for (var gy = gy0; gy <= gy1; gy += stepY) {
        var r1 = graphBgHash(seed, gx, gy);
        var r2 = graphBgHash(seed + 31, gx, gy);
        var sx = (gx + r1 * 0.85) * spacing;
        var sy = (gy + r2 * 0.85) * spacing;
        var radius = Math.max(0.45, (0.45 + r1 * 0.65)) / scale;
        ctx.globalAlpha = 0.16 + r2 * 0.58;
        ctx.fillStyle = r2 > 0.82 ? spec.starHot : spec.star;
        ctx.beginPath();
        ctx.arc(sx, sy, radius, 0, Math.PI * 2);
        ctx.fill();
        if (r1 > 0.88) {
          ctx.globalAlpha = 0.05;
          ctx.fillStyle = spec.starHot;
          ctx.beginPath();
          ctx.arc(sx, sy, radius * 3.2, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
    ctx.restore();
  }

  function drawGraphBgRing(ctx, color, cx, cy, radius, scale, dashed) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1 / scale;
    if (dashed) ctx.setLineDash([7 / scale, 12 / scale]);
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  function drawGraphBgNebula(ctx, color, left, top, right, bottom, cx, cy) {
    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(right - left, bottom - top) * 0.42);
    g.addColorStop(0, color);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(left, top, right - left, bottom - top);
  }

  function drawGraphBackground(state, now) {
    var canvas = state.canvas, network = state.network;
    if (!canvas || !canvas.parentNode || !network) return;
    var dpr = Math.max(1, window.devicePixelRatio || 1);
    var w = canvas.clientWidth || canvas.parentNode.clientWidth || 0;
    var h = canvas.clientHeight || canvas.parentNode.clientHeight || 0;
    if (!w || !h) return;
    var pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    var scale, view;
    try {
      scale = network.getScale();
      view = network.getViewPosition();
    } catch(e) { return; }
    if (!scale || !view) return;
    if (!state.center || now - state.centerAt > 700) {
      state.center = graphBgCenter(network);
      state.centerAt = now;
    }
    var cx = state.center.x, cy = state.center.y;
    var spec = state.spec, design = state.design;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(w / 2, h / 2);
    ctx.scale(scale, scale);
    ctx.translate(-view.x, -view.y);
    var halfW = w / (2 * scale), halfH = h / (2 * scale);
    var left = view.x - halfW - 160, right = view.x + halfW + 160;
    var top = view.y - halfH - 160, bottom = view.y + halfH + 160;
    var worldW = right - left, worldH = bottom - top;
    var base = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(worldW, worldH) * 0.72);
    base.addColorStop(0, spec.baseTop);
    base.addColorStop(1, spec.baseBottom);
    ctx.fillStyle = base;
    ctx.fillRect(left, top, worldW, worldH);

    if (design === "hologram") {
      drawGraphBgGrid(ctx, spec, left, top, right, bottom, scale, 110, 440);
      drawGraphBgStars(ctx, spec, left, top, right, bottom, scale, 37);
      drawGraphBgRing(ctx, spec.ring, cx, cy, 460, scale, false);
      drawGraphBgRing(ctx, spec.ring2, cx, cy, 720, scale, true);
      ctx.save();
      ctx.strokeStyle = spec.cross;
      ctx.lineWidth = 1 / scale;
      ctx.beginPath();
      ctx.moveTo(cx - 300, cy); ctx.lineTo(cx - 90, cy);
      ctx.moveTo(cx + 90, cy); ctx.lineTo(cx + 300, cy);
      ctx.moveTo(cx, cy - 300); ctx.lineTo(cx, cy - 90);
      ctx.moveTo(cx, cy + 90); ctx.lineTo(cx, cy + 300);
      ctx.stroke();
      ctx.setLineDash([4 / scale, 6 / scale]);
      for (var a = 0; a < 12; a++) {
        var ang = a * Math.PI / 6;
        var r0 = 555, r1 = 575;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(ang) * r0, cy + Math.sin(ang) * r0);
        ctx.lineTo(cx + Math.cos(ang) * r1, cy + Math.sin(ang) * r1);
        ctx.stroke();
      }
      ctx.restore();
      var t = ((now - state.t0) % 8000) / 8000;
      var sy = cy + (t * 2 - 1) * 900;
      var scan = ctx.createLinearGradient(cx - 680, sy, cx + 680, sy);
      scan.addColorStop(0, "rgba(34,211,238,0)");
      scan.addColorStop(0.5, spec.scan);
      scan.addColorStop(1, "rgba(34,211,238,0)");
      ctx.fillStyle = scan;
      ctx.fillRect(cx - 680, sy - 2 / scale, 1360, 4 / scale);
    } else if (design === "neon") {
      drawGraphBgGrid(ctx, spec, left, top, right, bottom, scale, 120, 480);
      drawGraphBgStars(ctx, spec, left, top, right, bottom, scale, 23);
      var glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, 720);
      glow.addColorStop(0, spec.glow);
      glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(left, top, worldW, worldH);
      ctx.save();
      ctx.strokeStyle = spec.diamond;
      ctx.lineWidth = 1 / scale;
      ctx.translate(cx, cy);
      ctx.rotate(Math.PI / 4);
      ctx.strokeRect(-330, -330, 660, 660);
      ctx.setLineDash([8 / scale, 14 / scale]);
      ctx.strokeRect(-480, -480, 960, 960);
      ctx.restore();
      drawGraphBgRing(ctx, spec.ring2, cx, cy, 700, scale, false);
      ctx.save();
      ctx.strokeStyle = spec.ray;
      ctx.lineWidth = 1 / scale;
      ctx.beginPath();
      for (var i = 0; i < 8; i++) {
        var ra = i * Math.PI / 4 + 0.18;
        ctx.moveTo(cx + Math.cos(ra) * 90, cy + Math.sin(ra) * 90);
        ctx.lineTo(cx + Math.cos(ra) * 640, cy + Math.sin(ra) * 640);
      }
      ctx.stroke();
      ctx.restore();
      var nt = ((now - state.t0) % 6000) / 6000;
      var nw = cx + (nt * 2 - 1) * 900;
      var ns = ctx.createLinearGradient(nw, cy - 650, nw, cy + 650);
      ns.addColorStop(0, "rgba(255,45,149,0)");
      ns.addColorStop(0.5, spec.scan);
      ns.addColorStop(1, "rgba(255,45,149,0)");
      ctx.fillStyle = ns;
      ctx.fillRect(nw - 2 / scale, cy - 650, 4 / scale, 1300);
    } else if (design === "minimal") {
      drawGraphBgNebula(ctx, spec.wash1, left, top, right, bottom, cx - 460, cy - 340);
      drawGraphBgNebula(ctx, spec.wash2, left, top, right, bottom, cx + 420, cy + 300);
      drawGraphBgGrid(ctx, spec, left, top, right, bottom, scale, 140, 560);
      drawGraphBgStars(ctx, spec, left, top, right, bottom, scale, 89);
      drawGraphBgRing(ctx, spec.ring, cx, cy, 430, scale, false);
      drawGraphBgRing(ctx, spec.ring2, cx, cy, 660, scale, true);
      ctx.save();
      ctx.strokeStyle = spec.accent;
      ctx.lineWidth = 1 / scale;
      ctx.setLineDash([10 / scale, 16 / scale]);
      ctx.beginPath();
      ctx.moveTo(cx - 620, cy + 330);
      ctx.lineTo(cx + 620, cy + 330);
      ctx.stroke();
      ctx.restore();
    } else {
      drawGraphBgNebula(ctx, spec.nebula1, left, top, right, bottom, cx - 520, cy - 260);
      drawGraphBgNebula(ctx, spec.nebula2, left, top, right, bottom, cx + 430, cy + 300);
      drawGraphBgNebula(ctx, spec.nebula3, left, top, right, bottom, cx + 70, cy - 620);
      drawGraphBgGrid(ctx, spec, left, top, right, bottom, scale, 150, 600);
      drawGraphBgStars(ctx, spec, left, top, right, bottom, scale, 71);
      drawGraphBgRing(ctx, spec.ring, cx, cy, 520, scale, false);
      drawGraphBgRing(ctx, spec.ring2, cx, cy, 760, scale, true);
      var gg = ctx.createRadialGradient(cx, cy, 0, cx, cy, 620);
      gg.addColorStop(0, spec.glow);
      gg.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = gg;
      ctx.fillRect(left, top, worldW, worldH);
    }
    ctx.restore();

    var vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.28, w / 2, h / 2, Math.max(w, h) * 0.72);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, spec.vignette);
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, w, h);
  }

  function startGraphBackground(container, network, design, pal, canvas) {
    stopGraphBackground();
    var state = {
      canvas: canvas, network: network, design: design, pal: pal,
      spec: graphBackgroundSpec(design), t0: performance.now(),
      center: null, centerAt: 0, raf: 0
    };
    graphBgState = state;
    function draw(now) {
      if (graphBgState !== state || !canvas.isConnected) return;
      drawGraphBackground(state, now);
      state.raf = requestAnimationFrame(draw);
    }
    state.raf = requestAnimationFrame(draw);
  }
  async function renderGraphView() {
    var kbId = graphBuildCurrentKb();
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
      var design = state.graphDesign || "stellar";
      var stage = $("graphStage");
      ["stellar", "hologram", "neon", "minimal"].forEach(function(d) {
        if (stage) stage.classList.remove("design-" + d);
        if (container) container.classList.remove("design-" + d);
      });
      if (stage) stage.classList.add("design-" + design);
      if (container) container.classList.add("design-" + design);
      var pal = graph2dPalette(design);
      if (!container) { setGraphLoading(false); return; }
      stopGraphBackground();
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
            background: n.color || pal.nodeBg, border: pal.nodeBorder,
            highlight: { background: n.color || pal.nodeBg, border: "#fbbf24" },
            hover: { background: n.color || pal.nodeBg, border: pal.hoverBorder }
          },
          size: 12 + Math.round(14 * ((degree[n.id] || 0) / maxDeg)),
          font: { color: pal.font, size: 13, face: "Segoe UI, Noto Sans SC, sans-serif", strokeWidth: 4, strokeColor: pal.fontStroke },
          shadow: { enabled: true, color: pal.shadow, size: 8, x: 0, y: 2 }
        };
      }));

      var edges = new vis.DataSet(data.edges.map(function(e) {
        return {
          from: e.from, to: e.to,
          label: e.dashes ? "" : (e.label || ""),
          title: e.title, arrows: e.arrows || undefined,
          dashes: !!e.dashes, width: e.dashes ? 1 : 1.5,
          color: { color: e.dashes ? pal.edgeDashed : pal.edge, highlight: "#fbbf24", hover: pal.hoverBorder },
          font: { color: pal.edgeFont, size: 10, strokeWidth: 3, strokeColor: pal.fontStroke, face: "Segoe UI, Noto Sans SC, sans-serif" }
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
      var bgCanvas = document.createElement("canvas");
      bgCanvas.className = "graph-bg-canvas";
      container.appendChild(bgCanvas);
      startGraphBackground(container, graphNetwork, design, pal, bgCanvas);

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

  function addStarField(scene, count, radius, palette, size, opacity) {
    var positions = new Float32Array(count * 3);
    var colors = new Float32Array(count * 3);
    var pal = palette || [
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
      var col = pal[Math.floor(Math.random() * pal.length)];
      colors[i * 3] = col.r;
      colors[i * 3 + 1] = col.g;
      colors[i * 3 + 2] = col.b;
    }
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    var pts = new THREE.Points(g, new THREE.PointsMaterial({
      size: size || 1.5,
      vertexColors: true,
      transparent: true,
      opacity: opacity == null ? 0.85 : opacity,
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
    var bend = style.bend || 16;
    var segments = style.segments || 12;
    function buildPoints(from, to) {
      var pts = [];
      var mx = (from.x + to.x) / 2;
      var my = (from.y + to.y) / 2;
      var mz = (from.z + to.z) / 2;
      var len = Math.sqrt(mx * mx + my * my + mz * mz) || 1;
      if (len < 0.001) { mx = 1; my = 0; mz = 0; len = 1; }
      for (var i = 0; i <= segments; i++) {
        var t = i / segments;
        var p = new THREE.Vector3().lerpVectors(from, to, t);
        var k = 4 * t * (1 - t) * bend;
        p.x += mx / len * k;
        p.y += my / len * k;
        p.z += mz / len * k;
        pts.push(p);
      }
      return pts;
    }
    edges.forEach(function(e) {
      var from = pos[e.from], to = pos[e.to];
      if (!from || !to) return;
      var g = new THREE.BufferGeometry().setFromPoints(buildPoints(from, to));
      if (e.dashes) {
        var m = new THREE.LineDashedMaterial({
          color: style.dashColor,
          dashSize: style.dashSize || 4,
          gapSize: style.gapSize || 3,
          transparent: true,
          opacity: style.dashOpacity || 0.55,
          blending: style.additive ? THREE.AdditiveBlending : THREE.NormalBlending
        });
        var line = new THREE.Line(g, m);
        line.computeLineDistances();
        scene.add(line);
      } else {
        var m2 = new THREE.LineBasicMaterial({
          color: style.color,
          transparent: true,
          opacity: style.opacity || 0.5,
          blending: style.additive ? THREE.AdditiveBlending : THREE.NormalBlending
        });
        var line2 = new THREE.Line(g, m2);
        line2.renderOrder = 1;
        scene.add(line2);
      }
    });
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
        color: 0x8aa5ff, opacity: 0.5, additive: true, bend: 24,
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
        color: 0x2dd4bf, opacity: 0.48, additive: true, bend: 18,
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
        color: 0xff4d9e, opacity: 0.58, additive: true, bend: 26,
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
      color: 0xa3afbf, opacity: 0.5, bend: 10,
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

  function addNodeGlows3D(scene, nodeMeshes, design) {
    var isLight = design === "minimal";
    nodeMeshes.forEach(function(mesh) {
      var base = mesh.userData && mesh.userData.baseColor;
      if (!base) return;
      var geo = mesh.geometry;
      var radius = (geo && geo.parameters && geo.parameters.radius) || 5;
      var inner = "#" + base.getHexString();
      var mid = design === "neon" ? "rgba(255,45,149,0.14)" : design === "hologram" ? "rgba(34,211,238,0.12)" : isLight ? "rgba(100,116,139,0.08)" : "rgba(99,102,241,0.10)";
      var opacity = isLight ? 0.16 : 0.26;
      var glow = makeGlowSprite(inner, mid, radius * (isLight ? 4.4 : 5.6), opacity);
      glow.position.copy(mesh.position);
      scene.add(glow);
    });
  }

  function addGraphPolish(scene, design, R) {
    function ring(color, opacity, radiusFactor, tube, rot) {
      var mesh = new THREE.Mesh(
        new THREE.TorusGeometry(R * radiusFactor, tube || 1.1, 8, 128),
        new THREE.MeshBasicMaterial({
          color: color, transparent: true, opacity: opacity,
          blending: THREE.AdditiveBlending, depthWrite: false
        })
      );
      if (rot) {
        if (rot.x) mesh.rotation.x = rot.x;
        if (rot.y) mesh.rotation.y = rot.y;
        if (rot.z) mesh.rotation.z = rot.z;
      }
      scene.add(mesh);
      return mesh;
    }
    var core, r1, r2, dust;
    if (design === "stellar") {
      core = makeGlowSprite("rgba(147,197,253,0.42)", "rgba(99,102,241,0.12)", R * 2.25, 0.3);
      r1 = ring(0x818cf8, 0.2, 1.14, 1.25, { x: Math.PI / 2.4 });
      r2 = ring(0x38bdf8, 0.15, 1.22, 0.9, { x: -Math.PI / 2.8, z: 0.42 });
    } else if (design === "hologram") {
      core = makeGlowSprite("rgba(103,232,249,0.38)", "rgba(34,211,238,0.12)", R * 2.15, 0.3);
      core.scale.y = R * 3.4;
      r1 = ring(0x22d3ee, 0.22, 1.12, 1.1, { x: Math.PI / 2.2, z: 0.25 });
      r2 = ring(0x818cf8, 0.18, 1.2, 0.8, { x: -Math.PI / 2.6, z: -0.2 });
      dust = addStarField(scene, 180, R * 2.8, [new THREE.Color(0x67e8f9), new THREE.Color(0xa5f3fc), new THREE.Color(0x818cf8)], 1.2, 0.4);
    } else if (design === "neon") {
      core = makeGlowSprite("rgba(249,168,212,0.4)", "rgba(255,45,149,0.14)", R * 2.2, 0.3);
      r1 = ring(0xff2d95, 0.2, 1.12, 1.2, { x: Math.PI / 2.3, z: 0.2 });
      r2 = ring(0x22d3ee, 0.16, 1.2, 0.9, { x: -Math.PI / 2.7, z: -0.3 });
    } else {
      core = makeGlowSprite("rgba(129,140,248,0.22)", "rgba(100,116,139,0.08)", R * 2.0, 0.18);
      core.material.blending = THREE.NormalBlending;
      r1 = ring(0x94a3b8, 0.35, 1.08, 1.0, { x: Math.PI / 2.5 });
      r2 = ring(0x818cf8, 0.28, 1.16, 0.7, { x: -Math.PI / 2.9, z: 0.35 });
      dust = addStarField(scene, 220, R * 3.0, [new THREE.Color(0x94a3b8), new THREE.Color(0xcbd5e1), new THREE.Color(0x818cf8)], 1.5, 0.45);
    }
    if (core) core.userData.baseOpacity = core.material.opacity;
    return {
      tick: function(t) {
        if (core) core.material.opacity = (core.userData.baseOpacity || 0.3) + Math.sin(t * 0.0009) * 0.035;
        if (r1) r1.rotation.z = t * 0.00008;
        if (r2) r2.rotation.z = -t * 0.00006;
        if (dust) dust.rotation.y = t * 0.00005;
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
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = design === "minimal" ? 1.0 : 1.12;
    var controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 170;
    controls.maxDistance = 1600;
    controls.target.set(0, 0, 0);

    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.55;
    controls.enablePan = false;
    controls.addEventListener("start", function() { controls.autoRotate = false; });

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
    addNodeGlows3D(scene, nodeMeshes, design);
    var polish = addGraphPolish(scene, design, R);
    var polishTick = polish.tick;

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
        var sx = ((proj.x * 0.5 + 0.5) * w);
        var sy = ((-proj.y * 0.5 + 0.5) * h);
        var dist = camera.position.distanceTo(item.pos);
        var scale = Math.max(0.56, Math.min(1.08, 380 / (dist * 0.62)));
        var opacity = Math.max(0.38, Math.min(1, 1.28 - dist / (R * 4.2)));
        var depth = Math.max(0, Math.min(1, dist / (R * 4.2)));
        item.el.style.left = sx + "px";
        item.el.style.top = sy + "px";
        item.el.style.transform = "translate(-50%, -118%) scale(" + scale.toFixed(3) + ")";
        item.el.style.opacity = opacity.toFixed(3);
        item.el.style.zIndex = String(Math.round((1 - depth) * 100));
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
      resizeObserver: resizeObserver, rafId: 0, fitRafId: 0,
      tick: function(t) { if (built.tick) built.tick(t); if (polishTick) polishTick(t); },
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
    renderGraphView();
  }
  function syncGraphModeButtons() {
    var b2 = $("graphMode2d"), b3 = $("graphMode3d");
    if (b2) b2.classList.toggle("active", state.graphMode === "2d");
    if (b3) b3.classList.toggle("active", state.graphMode === "3d");
    var dw = $("graphDesignWrap");
    if (dw) dw.classList.remove("hidden");
    var ds = $("graphDesign");
    if (ds) ds.value = state.graphDesign || "stellar";
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
    initGraphBuildState();
  }

  var graphBuildTimer = null;

  function graphBuildBtn() {
    return document.getElementById("graphBuild");
  }

  function graphBuildCurrentKb() {
    var sel = document.getElementById("graphKbSelect");
    return state.graphKbId || (sel && sel.value) || state.docKbId || "default";
  }

  function graphBuildStopPoll() {
    if (graphBuildTimer) { clearInterval(graphBuildTimer); graphBuildTimer = null; }
  }

  function graphBuildReset() {
    graphBuildStopPoll();
    var b = graphBuildBtn();
    if (b) { b.textContent = "构建图谱"; b.disabled = false; }
  }

  function graphBuildStartPoll(kbId, btn) {
    graphBuildStopPoll();
    btn.disabled = true;
    graphBuildTimer = setInterval(async function() {
      try {
        var sr = await fetch("/api/graph/build/status?kb_id=" + encodeURIComponent(kbId));
        var st = await sr.json();
        if (!st || st.status === "idle") return;
        if (st.status === "running") {
          var total = st.total || 0;
          var done = st.done || 0;
          btn.textContent = total ? "构建中 " + done + "/" + total : "构建中...";
        } else if (st.status === "success") {
          graphBuildStopPoll();
          btn.textContent = "构建完成";
          btn.disabled = true;
          setTimeout(function() { graphBuildReset(); refreshGraph(); }, 1200);
        } else if (st.status === "error") {
          graphBuildStopPoll();
          btn.textContent = "构建图谱";
          btn.disabled = false;
          alert("图谱构建失败: " + (st.error || st.message || "未知错误"));
        }
      } catch(e) {
        console.error(e);
        graphBuildReset();
      }
    }, 3000);
  }

  async function initGraphBuildState() {
    var btn = graphBuildBtn();
    if (!btn) return;
    var kbId = graphBuildCurrentKb();
    try {
      var sr = await fetch("/api/graph/build/status?kb_id=" + encodeURIComponent(kbId));
      var st = await sr.json();
      if (!st || st.status === "idle") {
        graphBuildReset();
        return;
      }
      if (st.status === "running") {
        var total = st.total || 0;
        var done = st.done || 0;
        btn.disabled = true;
        btn.textContent = total ? "构建中 " + done + "/" + total : "构建中...";
        graphBuildStartPoll(kbId, btn);
        return;
      }
      if (st.status === "success") {
        graphBuildReset();
        return;
      }
      if (st.status === "error") {
        graphBuildReset();
        alert("图谱构建失败: " + (st.error || st.message || "未知错误"));
      }
    } catch(e) {
      console.error(e);
      graphBuildReset();
    }
  }

  async function buildGraph() {
    var btn = graphBuildBtn();
    if (!btn) return;
    var kbId = graphBuildCurrentKb();
    graphBuildReset();
    btn.disabled = true;
    btn.textContent = "构建中...";
    try {
      var resp = await fetch("/api/graph/build?kb_id=" + encodeURIComponent(kbId) + "&max_chunks=0", { method: "POST" });
      var data = {};
      try { data = await resp.json(); } catch(e) {}
      if (!resp.ok) {
        graphBuildReset();
        alert("图谱构建失败: " + (data.detail || data.message || resp.statusText || "未知错误"));
        return;
      }
      console.log("Graph build:", data);
      btn.textContent = "构建中...等待状态";
      graphBuildStartPoll(kbId, btn);
      setTimeout(function() { graphBuildReset(); }, 300000);
    } catch(e) {
      console.error(e);
      graphBuildReset();
    }
  }

  initGraphBuildState();

  window.refreshGraph = refreshGraph;
  window.buildGraph = buildGraph;
  window.renderGraphView = renderGraphView;
})();
