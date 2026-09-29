/*
 * Chicano Cruise Admin — behaviour. Plain JavaScript, no libraries, no build step.
 * Everything is progressive enhancement: every page, link and form works without it.
 *
 *  Shell       sidebar compact mode (remembered), mobile drawer, "/" focuses search, page progress bar
 *  Select      every <select> becomes a styled listbox: keyboard, type-to-search (long lists), clear
 *              button (filters), bottom sheet on phones. The real <select> stays in the form, so
 *              Django receives exactly the same values. Opt out with <select data-native>.
 *  Menus       <details class="menu"> account / notifications / row actions: animated, positioned
 *              inside the viewport, arrow keys, Escape, click outside
 *  Dialogs     <button data-dialog="id"> opens <dialog id="id">; [data-close], Escape or the backdrop close it
 *  Confirm     <form data-confirm="Question? Detail."> asks in a styled dialog (never window.confirm)
 *              optional data-confirm-ok="Button text", data-confirm-danger
 *  Forms       submit buttons show a spinner and lock (no double refunds/broadcasts);
 *              <form data-autosubmit> submits when a select/radio changes
 *  Tables      <table class="responsive"> gets data-label per cell (phone cards); <tr data-href> is clickable;
 *              <table data-bulk="formId"> adds select-all + a floating bulk action bar
 *  Live        <div data-poll="URL" data-every="ms"> re-fetches a server-rendered fragment
 *  Feedback    Django messages → toasts; [data-tip] tooltips; <input data-preview="#el"> live preview
 */
(function () {
  "use strict";
  var root = document.documentElement;
  var phone = window.matchMedia("(max-width: 768px)");
  var SVG = {
    chev: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6l6-6"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5l9-10"/></svg>',
    x: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    search: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M11 18a7 7 0 1 0 0-14a7 7 0 0 0 0 14zM21 21l-5-5"/></svg>',
    alert: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l10 18H2zM12 10v5M12 18h.01"/></svg>',
    info: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM12 11v6M12 7.5h.01"/></svg>'
  };
  var uid = 0;
  function nextId(prefix) { uid += 1; return prefix + "-" + uid; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; }
  }
  function typing(el) { return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)); }

  /* Put a floating panel next to its anchor, inside the viewport (flips above when there's no room). */
  function place(panel, anchor, align, minToAnchor) {
    var r = anchor.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight, m = 8, gap = 6;
    panel.style.maxHeight = "";
    if (minToAnchor) panel.style.minWidth = Math.min(Math.max(r.width, 180), vw - 2 * m) + "px";
    var pw = panel.offsetWidth, ph = panel.scrollHeight;
    var left = align === "end" ? r.right - pw : r.left;
    left = Math.max(m, Math.min(left, vw - pw - m));
    var below = vh - r.bottom - m - gap, above = r.top - m - gap;
    var up = ph > below && above > below;
    var room = up ? above : below;
    if (ph > room) panel.style.maxHeight = Math.max(room, 140) + "px";
    var h = Math.min(ph, Math.max(room, 140));
    panel.style.left = left + "px";
    panel.style.top = (up ? Math.max(m, r.top - gap - h) : r.bottom + gap) + "px";
    panel.classList.toggle("is-up", up);
  }

  /* ================================================================ shell */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-collapse]");
    if (!btn) return;
    var compact = root.classList.toggle("sidebar-compact");
    store("cc-sidebar-compact", compact ? "1" : "0");
    btn.setAttribute("aria-expanded", compact ? "false" : "true");
  });

  var menuBtn = document.querySelector("[data-menu]");
  function setDrawer(open) {
    root.classList.toggle("nav-open", open);
    if (menuBtn) menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var active = document.querySelector(".sidebar .nav-item.active") || document.querySelector(".sidebar a");
      if (active) { active.scrollIntoView({ block: "nearest" }); active.focus({ preventScroll: true }); }
    } else if (menuBtn && document.activeElement && document.activeElement.closest(".sidebar")) {
      menuBtn.focus();
    }
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-menu]")) { setDrawer(!root.classList.contains("nav-open")); return; }
    if (e.target.closest(".scrim")) { setDrawer(false); return; }
    if (root.classList.contains("nav-open") && e.target.closest(".sidebar a")) setDrawer(false);
  });

  // "/" jumps to the global search (like most admin tools)
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || typing(document.activeElement)) return;
    var s = document.getElementById("global-search");
    if (s && s.offsetParent) { e.preventDefault(); s.focus(); s.select(); }
  });

  // thin progress bar while the next page loads
  var progress = document.createElement("div");
  progress.className = "page-progress";
  document.body.appendChild(progress);
  function startProgress() { progress.classList.remove("run"); void progress.offsetWidth; progress.classList.add("run"); }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute("download") || a.origin !== location.origin) return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || (a.pathname === location.pathname && a.search === location.search && a.hash)) return;
    if (/\/file\/?(\?|$)/.test(a.pathname)) return;           // private document files open inline
    startProgress();
  });
  window.addEventListener("pageshow", function () { progress.classList.remove("run"); });

  /* ================================================================ custom select */
  var openSelect = null;

  function Select(sel) {
    this.sel = sel;
    var wrap = this.wrap = document.createElement("div");
    wrap.className = "select";
    var inline = sel.style.width === "auto" || sel.hasAttribute("data-inline") ||
      !!sel.closest(".range, .map-toolbar, .card-head, .page-actions, .pager-size, .toolbar-inline");
    if (inline) wrap.classList.add("inline");
    sel.style.width = "";
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.tabIndex = -1;
    sel.setAttribute("aria-hidden", "true");

    var listId = nextId("sel-list");
    var t = this.trigger = document.createElement("button");
    t.type = "button";
    t.className = "select-trigger";
    t.setAttribute("aria-haspopup", "listbox");
    t.setAttribute("aria-expanded", "false");
    t.setAttribute("aria-controls", listId);
    var label = sel.id && document.querySelector('label[for="' + sel.id + '"]');
    this.valueId = nextId("sel-val");
    if (label) {
      if (!label.id) label.id = nextId("sel-lbl");
      t.setAttribute("aria-labelledby", label.id + " " + this.valueId);
      label.addEventListener("click", function (e) { e.preventDefault(); t.focus(); });
      this.labelText = label.textContent.replace(/\*|\(required\)/g, "").trim();
    } else if (sel.getAttribute("aria-label")) {
      t.setAttribute("aria-label", sel.getAttribute("aria-label"));
    }
    this.clearable = !!sel.closest("form.filters") && sel.options.length && sel.options[0].value === "" && !sel.required;
    t.innerHTML = '<span class="select-value" id="' + this.valueId + '"></span>' +
      (this.clearable ? '<span class="select-clear" role="button" tabindex="-1" aria-label="Clear">' + SVG.x + "</span>" : "") +
      '<span class="select-chev">' + SVG.chev + "</span>";
    wrap.appendChild(t);

    var panel = this.panel = document.createElement("div");
    panel.className = "select-panel";
    var many = sel.options.length > 8 || sel.hasAttribute("data-search");
    panel.innerHTML = '<div class="sheet-title">' + esc(this.labelText || "Choose") + "</div>" +
      (many ? '<div class="select-search">' + SVG.search + '<input type="text" autocomplete="off" spellcheck="false" placeholder="Search…" aria-label="Search options" aria-controls="' + listId + '"></div>' : "") +
      '<ul class="select-list" role="listbox" id="' + listId + '" tabindex="-1"></ul>';
    wrap.appendChild(panel);
    this.list = panel.querySelector(".select-list");
    this.search = panel.querySelector(".select-search input");
    if (label) this.list.setAttribute("aria-labelledby", label.id);
    this.active = -1;
    this.items = [];
    if (sel.disabled) { wrap.classList.add("is-disabled"); t.disabled = true; }
    this.sync();
    this.bind();
    sel._select = this;
  }

  Select.prototype.options = function () {
    var out = [];
    Array.prototype.forEach.call(this.sel.children, function (node) {
      if (node.tagName === "OPTGROUP") {
        out.push({ group: node.label });
        Array.prototype.forEach.call(node.children, function (o) { out.push({ opt: o }); });
      } else if (node.tagName === "OPTION") out.push({ opt: node });
    });
    return out;
  };

  Select.prototype.sync = function () {
    var o = this.sel.options[this.sel.selectedIndex];
    var v = this.trigger.querySelector(".select-value");
    var empty = !o || o.value === "";
    v.textContent = o ? o.text : "";
    v.classList.toggle("is-placeholder", empty);
    this.wrap.classList.toggle("has-value", !empty);
    var c = this.trigger.querySelector(".select-clear");
    if (c) c.hidden = empty;
  };

  Select.prototype.render = function (q) {
    var self = this, html = "", n = 0, needle = (q || "").trim().toLowerCase();
    this.items = [];
    this.options().forEach(function (entry) {
      if (entry.group !== undefined) { if (!needle) html += '<li class="select-group" role="presentation">' + esc(entry.group) + "</li>"; return; }
      var o = entry.opt, text = o.text;
      if (needle && text.toLowerCase().indexOf(needle) === -1) return;
      var id = self.list.id + "-" + n;
      var shown = esc(text);
      if (needle) {
        var i = text.toLowerCase().indexOf(needle);
        shown = esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + needle.length)) + "</mark>" + esc(text.slice(i + needle.length));
      }
      html += '<li class="select-option" role="option" id="' + id + '" data-i="' + n + '" aria-selected="' + (o.selected ? "true" : "false") + '"' +
        (o.disabled ? ' aria-disabled="true"' : "") + '><span class="opt-text">' + shown + '</span><span class="opt-check">' + SVG.check + "</span></li>";
      self.items.push(o);
      n += 1;
    });
    this.list.innerHTML = html || '<li class="select-empty" role="presentation">No matches' + (needle ? " for “" + esc(q) + "”" : "") + "</li>";
    var sel = this.items.indexOf(this.sel.options[this.sel.selectedIndex]);
    this.setActive(sel >= 0 ? sel : this.firstEnabled(0, 1));
  };

  Select.prototype.firstEnabled = function (from, step) {
    for (var i = from; i >= 0 && i < this.items.length; i += step) if (!this.items[i].disabled) return i;
    return -1;
  };

  Select.prototype.setActive = function (i) {
    var prev = this.list.querySelector(".is-active");
    if (prev) prev.classList.remove("is-active");
    this.active = i;
    var owner = this.search || this.trigger;
    if (i < 0) { owner.removeAttribute("aria-activedescendant"); return; }
    var li = this.list.querySelector('[data-i="' + i + '"]');
    if (!li) return;
    li.classList.add("is-active");
    owner.setAttribute("aria-activedescendant", li.id);
    var lt = this.list.scrollTop, lh = this.list.clientHeight, top = li.offsetTop - this.list.offsetTop, h = li.offsetHeight;
    if (top < lt) this.list.scrollTop = top - 4;
    else if (top + h > lt + lh) this.list.scrollTop = top + h - lh + 4;
  };

  Select.prototype.move = function (step) {
    if (!this.items.length) return;
    var i = this.active;
    for (var k = 0; k < this.items.length; k++) {
      i = i + step;
      if (i < 0) i = this.items.length - 1;
      if (i >= this.items.length) i = 0;
      if (!this.items[i].disabled) break;
    }
    this.setActive(i);
  };

  Select.prototype.open = function () {
    if (this.wrap.classList.contains("is-open") || this.trigger.disabled) return;
    if (openSelect) openSelect.close(false);
    closeMenus();
    openSelect = this;
    this.render("");
    if (this.search) this.search.value = "";
    var sheet = phone.matches;
    this.panel.classList.toggle("as-sheet", sheet);
    if (sheet) {
      this.scrim = document.createElement("div");
      this.scrim.className = "sheet-scrim";
      this.wrap.insertBefore(this.scrim, this.panel);
      this.panel.style.left = this.panel.style.top = this.panel.style.minWidth = this.panel.style.maxHeight = "";
    } else {
      place(this.panel, this.trigger, "start", true);
    }
    this.wrap.classList.add("is-open");
    this.trigger.setAttribute("aria-expanded", "true");
    if (this.search && !sheet) this.search.focus();
    else if (this.search && sheet) this.list.focus();
    var self = this;
    requestAnimationFrame(function () { self.setActive(self.active); });
  };

  Select.prototype.close = function (focus) {
    if (!this.wrap.classList.contains("is-open")) return;
    this.wrap.classList.remove("is-open");
    this.trigger.setAttribute("aria-expanded", "false");
    this.trigger.removeAttribute("aria-activedescendant");
    if (this.scrim) { this.scrim.remove(); this.scrim = null; }
    if (openSelect === this) openSelect = null;
    if (focus !== false) this.trigger.focus({ preventScroll: true });
  };

  Select.prototype.choose = function (i) {
    var o = this.items[i];
    if (!o || o.disabled) return;
    var changed = !o.selected;
    o.selected = true;
    this.sync();
    this.close();
    if (changed) {
      this.sel.dispatchEvent(new Event("input", { bubbles: true }));
      this.sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };

  Select.prototype.clear = function () {
    if (this.sel.value === "") return;
    this.sel.value = "";
    this.sync();
    this.sel.dispatchEvent(new Event("change", { bubbles: true }));
  };

  Select.prototype.typeahead = function (ch) {
    var self = this;
    this.buffer = (this.buffer || "") + ch.toLowerCase();
    clearTimeout(this.bufferTimer);
    this.bufferTimer = setTimeout(function () { self.buffer = ""; }, 600);
    var opts = this.wrap.classList.contains("is-open") ? this.items : Array.prototype.slice.call(this.sel.options);
    for (var i = 0; i < opts.length; i++) {
      if (!opts[i].disabled && opts[i].text.toLowerCase().indexOf(this.buffer) === 0) {
        if (this.wrap.classList.contains("is-open")) this.setActive(i); else { this.open(); this.setActive(this.items.indexOf(opts[i])); }
        return;
      }
    }
  };

  Select.prototype.keys = function (e) {
    var isOpen = this.wrap.classList.contains("is-open");
    switch (e.key) {
      case "ArrowDown": case "ArrowUp":
        e.preventDefault();
        if (!isOpen) { this.open(); return; }
        this.move(e.key === "ArrowDown" ? 1 : -1); return;
      case "Home": case "End":
        if (!isOpen) return;
        e.preventDefault();
        this.setActive(e.key === "Home" ? this.firstEnabled(0, 1) : this.firstEnabled(this.items.length - 1, -1)); return;
      case "PageDown": case "PageUp":
        if (!isOpen) return;
        e.preventDefault();
        for (var k = 0; k < 8; k++) this.move(e.key === "PageDown" ? 1 : -1); return;
      case "Enter":
        e.preventDefault();
        if (isOpen) this.choose(this.active); else this.open(); return;
      case " ":
        if (e.target === this.search) return;
        e.preventDefault();
        if (isOpen) this.choose(this.active); else this.open(); return;
      case "Escape":
        if (isOpen) { e.preventDefault(); e.stopPropagation(); this.close(); } return;
      case "Tab":
        if (isOpen) this.close(false); return;
      case "Backspace": case "Delete":
        if (!isOpen && this.clearable && e.target === this.trigger) { e.preventDefault(); this.clear(); } return;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.target !== this.search) {
          if (this.search) { this.open(); this.search.value = e.key; this.render(e.key); this.search.focus(); e.preventDefault(); }
          else this.typeahead(e.key);
        }
    }
  };

  Select.prototype.bind = function () {
    var self = this;
    this.trigger.addEventListener("click", function (e) {
      if (e.target.closest(".select-clear")) { e.preventDefault(); self.clear(); return; }
      if (self.wrap.classList.contains("is-open")) self.close(); else self.open();
    });
    this.trigger.addEventListener("keydown", function (e) { self.keys(e); });
    this.list.addEventListener("keydown", function (e) { self.keys(e); });
    if (this.search) {
      this.search.addEventListener("keydown", function (e) { self.keys(e); });
      this.search.addEventListener("input", function () { self.render(self.search.value); });
    }
    this.list.addEventListener("mousedown", function (e) { e.preventDefault(); });     // keep focus where it is
    this.list.addEventListener("click", function (e) {
      var li = e.target.closest(".select-option");
      if (li) self.choose(parseInt(li.getAttribute("data-i"), 10));
    });
    this.list.addEventListener("mousemove", function (e) {
      var li = e.target.closest(".select-option");
      if (li && !li.classList.contains("is-active") && li.getAttribute("aria-disabled") !== "true") self.setActive(parseInt(li.getAttribute("data-i"), 10));
    });
    this.wrap.addEventListener("click", function (e) { if (e.target === self.scrim) self.close(); });
    this.sel.addEventListener("change", function () { self.sync(); });                   // changed by other code
    this.sel.addEventListener("invalid", function () { self.wrap.classList.add("is-invalid"); self.trigger.focus(); });
    var form = this.sel.form;
    if (form) form.addEventListener("reset", function () { setTimeout(function () { self.sync(); }, 0); });
  };

  function enhanceSelects(scope) {
    (scope || document).querySelectorAll("select").forEach(function (sel) {
      if (sel._select || sel.multiple || sel.size > 1 || sel.hasAttribute("data-native")) return;
      new Select(sel);
    });
  }

  document.addEventListener("pointerdown", function (e) {
    if (openSelect && !openSelect.wrap.contains(e.target)) openSelect.close(false);
  });
  function reflow() {
    if (openSelect && !openSelect.panel.classList.contains("as-sheet")) place(openSelect.panel, openSelect.trigger, "start", true);
    document.querySelectorAll("details.menu[open] > .menu-panel").forEach(function (p) {
      if (!p.classList.contains("as-sheet")) place(p, p.parentNode.querySelector("summary"), p.parentNode.getAttribute("data-align") || "end");
    });
  }
  var reflowQueued = false;
  function queueReflow(e) {
    if (e && e.target && e.target.nodeType === 1 && e.target.closest && e.target.closest(".select-panel, .menu-panel")) return;
    if (reflowQueued) return;
    reflowQueued = true;
    requestAnimationFrame(function () { reflowQueued = false; reflow(); });
  }
  window.addEventListener("resize", queueReflow);
  window.addEventListener("scroll", queueReflow, true);

  /* ================================================================ menus (<details class="menu">) */
  function menuItems(menu) { return Array.prototype.slice.call(menu.querySelectorAll(".menu-panel a.menu-item, .menu-panel button.menu-item:not([disabled])")); }
  function openMenu(menu, focusFirst) {
    if (openSelect) openSelect.close(false);
    closeMenus(menu);
    var panel = menu.querySelector(".menu-panel"), summary = menu.querySelector("summary");
    var alreadyOpen = menu._state === "open";
    menu._state = "open";
    menu.setAttribute("open", "");
    summary.setAttribute("aria-expanded", "true");
    if (!alreadyOpen) {
      place(panel, summary, menu.getAttribute("data-align") || "end");
      requestAnimationFrame(function () { if (menu._state === "open") panel.classList.add("is-ready"); });
    }
    if (focusFirst) { var items = menuItems(menu); if (items[0]) items[0].focus(); }
  }
  function closeMenu(menu, focusSummary) {
    if (!menu.hasAttribute("open")) return;
    var panel = menu.querySelector(".menu-panel"), summary = menu.querySelector("summary");
    menu._state = "closed";
    panel.classList.remove("is-ready");
    summary.setAttribute("aria-expanded", "false");
    setTimeout(function () { if (menu._state === "closed") menu.removeAttribute("open"); }, 140);
    if (focusSummary) summary.focus();
  }
  function closeMenus(except) {
    document.querySelectorAll("details.menu[open]").forEach(function (d) { if (d !== except) closeMenu(d); });
  }
  function initMenus(scope) {
    (scope || document).querySelectorAll("details.menu:not(.js)").forEach(function (menu) {
      menu.classList.add("js");
      var summary = menu.querySelector("summary");
      summary.setAttribute("aria-haspopup", "menu");
      summary.setAttribute("aria-expanded", menu.open ? "true" : "false");
      if (menu.open) { menu._state = "open"; menu.querySelector(".menu-panel").classList.add("is-ready"); }
    });
  }
  document.addEventListener("click", function (e) {
    var summary = e.target.closest("details.menu.js > summary");
    if (summary) {
      e.preventDefault();
      var menu = summary.parentNode;
      if (menu._state === "open") closeMenu(menu);
      else openMenu(menu, e.detail === 0);                 // keyboard "click" (Enter/Space) focuses the first item
      return;
    }
    document.querySelectorAll("details.menu[open]").forEach(function (d) {
      if (!d.contains(e.target)) closeMenu(d);
      else if (e.target.closest("a.menu-item, button.menu-item[data-dialog]")) closeMenu(d);
    });
  });
  document.addEventListener("keydown", function (e) {
    var menu = e.target.closest && e.target.closest("details.menu.js");
    if (!menu) return;
    var items = menuItems(menu), i = items.indexOf(document.activeElement);
    if (e.target.tagName === "SUMMARY" && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault(); openMenu(menu, false);
      var list = menuItems(menu); if (list.length) (e.key === "ArrowDown" ? list[0] : list[list.length - 1]).focus();
      return;
    }
    if (!menu.hasAttribute("open")) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      var n = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      items[n].focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault(); if (items.length) items[e.key === "Home" ? 0 : items.length - 1].focus();
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation(); closeMenu(menu, true);
    } else if (e.key === "Tab") {
      closeMenu(menu);
    }
  });

  /* ================================================================ dialogs */
  var lastOpener = null;
  function openDialog(d, opener) {
    if (!d || !d.showModal || d.open) return;
    lastOpener = opener || document.activeElement;
    d.showModal();
    var first = d.querySelector("[autofocus], textarea, input:not([type=hidden]):not([type=checkbox]):not([type=radio]), .select-trigger");
    if (first && !phone.matches) first.focus();
    else { var btn = d.querySelector(".dialog-foot .btn:last-child"); if (btn) btn.focus(); }
  }
  function closeDialog(d) {
    if (!d || !d.open || d.classList.contains("is-closing")) return;
    if (openSelect && d.contains(openSelect.wrap)) openSelect.close(false);
    d.classList.add("is-closing");
    setTimeout(function () {
      d.classList.remove("is-closing");
      d.close();
      if (lastOpener && document.contains(lastOpener)) lastOpener.focus({ preventScroll: true });
    }, 150);
  }
  function initDialogs(scope) {
    (scope || document).querySelectorAll("dialog.modal:not([data-ready])").forEach(function (d) {
      d.setAttribute("data-ready", "");
      d.addEventListener("cancel", function (e) { e.preventDefault(); closeDialog(d); });
    });
  }
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-dialog]");
    if (opener) { e.preventDefault(); openDialog(document.getElementById(opener.getAttribute("data-dialog")), opener); return; }
    var closer = e.target.closest("[data-close]");
    if (closer && closer.closest("dialog")) { e.preventDefault(); closeDialog(closer.closest("dialog")); return; }
    if (e.target.tagName === "DIALOG" && e.target.classList.contains("modal")) {   // click on the backdrop
      var r = e.target.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeDialog(e.target);
    }
  });

  /* ================================================================ confirm (replaces window.confirm) */
  var confirmBox = null, pendingConfirm = null;
  function buildConfirm() {
    confirmBox = document.createElement("dialog");
    confirmBox.className = "modal confirm";
    confirmBox.setAttribute("aria-labelledby", "cc-confirm-title");
    confirmBox.setAttribute("aria-describedby", "cc-confirm-text");
    confirmBox.innerHTML =
      '<div class="dialog-body"><div class="dialog-head"><span class="dialog-icon" aria-hidden="true"></span>' +
      '<h2 id="cc-confirm-title"></h2><button type="button" class="dialog-close" data-close aria-label="Close">' + SVG.x + "</button></div>" +
      '<div class="dialog-main"><p id="cc-confirm-text"></p></div>' +
      '<div class="dialog-foot"><button type="button" class="btn btn-secondary" data-close>Cancel</button>' +
      '<button type="button" class="btn btn-primary" data-ok>Confirm</button></div></div>';
    document.body.appendChild(confirmBox);
    initDialogs(confirmBox.parentNode);
    confirmBox.querySelector("[data-ok]").addEventListener("click", function () {
      var p = pendingConfirm;
      pendingConfirm = null;
      closeDialog(confirmBox);
      if (p) p();
    });
    confirmBox.addEventListener("close", function () { pendingConfirm = null; });
  }
  function askConfirm(message, opts, then) {
    if (!confirmBox) buildConfirm();
    opts = opts || {};
    var text = String(message || "Are you sure?"), title = opts.title, rest = "";
    if (!title) {                                  // "Question? More detail." → title + description
      var m = text.match(/^(.+?\?)\s+(.+)$/);
      if (m) { title = m[1]; rest = m[2]; } else { title = text; }
    } else rest = text;
    confirmBox.querySelector("h2").textContent = title;
    var p = confirmBox.querySelector("#cc-confirm-text");
    p.textContent = rest;
    p.parentNode.hidden = !rest;
    var ok = confirmBox.querySelector("[data-ok]");
    ok.textContent = opts.ok || "Confirm";
    ok.className = "btn " + (opts.danger ? "btn-danger" : "btn-primary");
    var icon = confirmBox.querySelector(".dialog-icon");
    icon.className = "dialog-icon" + (opts.danger ? " danger" : "");
    icon.innerHTML = opts.danger ? SVG.alert : SVG.info;
    pendingConfirm = then;
    openDialog(confirmBox, document.activeElement);
    ok.focus();
  }

  /* ================================================================ forms: confirm, loading, no double submit */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var message = form.getAttribute("data-confirm");
    var submitter = e.submitter || null;
    if (message && !form.dataset.confirmed) {
      e.preventDefault();
      var label = submitter ? submitter.textContent.trim() : "";
      askConfirm(message, {
        title: form.getAttribute("data-confirm-title"),
        ok: form.getAttribute("data-confirm-ok") || label || "Confirm",
        danger: form.hasAttribute("data-confirm-danger") || !!(submitter && submitter.classList.contains("btn-danger"))
      }, function () {
        form.dataset.confirmed = "1";
        if (form.requestSubmit) form.requestSubmit(submitter && form.contains(submitter) ? submitter : undefined); else form.submit();
      });
      return;
    }
    delete form.dataset.confirmed;
    if (form.method.toLowerCase() !== "post") { startProgress(); return; }
    if (form.dataset.sending) { e.preventDefault(); return; }
    form.dataset.sending = "1";
    var btn = submitter || form.querySelector("button[type=submit], button:not([type])");
    if (btn) setTimeout(function () { btn.classList.add("is-loading"); btn.setAttribute("aria-busy", "true"); }, 0);
    setTimeout(function () {            // lock the other buttons after the browser has read the clicked one
      form.querySelectorAll("button").forEach(function (b) { if (b !== btn) b.disabled = true; });
      if (form.id) document.querySelectorAll('button[form="' + form.id + '"]').forEach(function (b) { if (b !== btn) b.disabled = true; });
    }, 0);
    startProgress();
  });
  window.addEventListener("pageshow", function () {   // back button: unlock forms restored from cache
    document.querySelectorAll("form[data-sending]").forEach(function (f) {
      delete f.dataset.sending;
      f.querySelectorAll("button").forEach(function (b) { b.disabled = false; b.classList.remove("is-loading"); b.removeAttribute("aria-busy"); });
    });
  });

  document.addEventListener("change", function (e) {
    var form = e.target.closest("form[data-autosubmit]");
    if (!form) return;
    if (e.target.name === "range") {                  // "Custom range" reveals the date inputs instead of submitting
      var custom = form.querySelector(".custom-range");
      if (custom) custom.classList.toggle("show", e.target.value === "custom");
      if (e.target.value === "custom") { var f = custom && custom.querySelector("input"); if (f) f.focus(); return; }
    }
    if (e.target.tagName === "SELECT" || e.target.type === "radio") { startProgress(); form.submit(); }
  });

  /* ================================================================ tables */
  function labelTables(scope) {
    (scope || document).querySelectorAll("table.responsive").forEach(function (table) {
      var heads = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) { return th.textContent.trim(); });
      table.querySelectorAll("tbody tr").forEach(function (tr) {
        Array.prototype.forEach.call(tr.children, function (td, i) {
          if (heads[i] && !td.hasAttribute("data-label")) td.setAttribute("data-label", heads[i]);
          // On phones each cell is "label | value": keep multi-part values (name + phone, tag + text) together.
          if (!td.classList.contains("primary") && !td.classList.contains("actions") && !td.classList.contains("check") &&
              td.childNodes.length > 1 && !td.querySelector(":scope > .cell")) {
            var wrap = document.createElement("div");
            wrap.className = "cell";
            while (td.firstChild) wrap.appendChild(td.firstChild);
            td.appendChild(wrap);
          }
        });
      });
    });
  }
  document.addEventListener("click", function (e) {
    var row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a, button, form, input, select, textarea, dialog, summary, label, .menu, .select")) {
      if (window.getSelection && String(window.getSelection()).length) return;    // selecting text, not navigating
      startProgress();
      window.location.href = row.getAttribute("data-href");
    }
  });

  // bulk selection: <table data-bulk="formId">, row checkboxes name="ids" form="formId", <div class="bulkbar" id="formId-bar">
  function initBulk(scope) {
    (scope || document).querySelectorAll("table[data-bulk]:not([data-bulk-ready])").forEach(function (table) {
      table.setAttribute("data-bulk-ready", "");
      var formId = table.getAttribute("data-bulk");
      var bar = document.getElementById(formId + "-bar");
      var all = table.querySelector("thead input[type=checkbox]");
      function boxes() { return Array.prototype.slice.call(table.querySelectorAll("tbody input[type=checkbox][name=ids]")); }
      function update() {
        var list = boxes(), n = list.filter(function (b) { return b.checked; }).length;
        list.forEach(function (b) { b.closest("tr").classList.toggle("is-selected", b.checked); });
        if (all) { all.checked = n > 0 && n === list.length; all.indeterminate = n > 0 && n < list.length; }
        if (bar) {
          bar.classList.toggle("show", n > 0);
          var c = bar.querySelector(".count"); if (c) c.textContent = n + " selected";
        }
      }
      table.addEventListener("change", function (e) {
        if (e.target === all) boxes().forEach(function (b) { b.checked = all.checked; });
        update();
      });
      if (bar) bar.addEventListener("click", function (e) {
        if (e.target.closest("[data-bulk-clear]")) { boxes().forEach(function (b) { b.checked = false; }); update(); }
      });
      update();
    });
  }

  /* ================================================================ live refresh of fragments */
  function enhance(scope) {
    enhanceSelects(scope);
    initMenus(scope);
    initDialogs(scope);
    initBulk(scope);
    labelTables(scope);
  }
  function poll(el) {
    var every = parseInt(el.getAttribute("data-every") || "10000", 10);
    setInterval(function () {
      if (document.hidden || el.querySelector("dialog[open], details[open], .select.is-open") || el.contains(document.activeElement)) return;
      fetch(el.getAttribute("data-poll"), { headers: { "X-Fragment": "1" }, credentials: "same-origin" })
        .then(function (r) { return r.redirected || !r.ok ? null : r.text(); })   // signed out / error: keep what's shown
        .then(function (html) {
          if (html === null) { el.setAttribute("data-stale", "1"); return; }
          el.removeAttribute("data-stale");
          el.innerHTML = html;
          enhance(el);
          initTips(el);
        })
        .catch(function () { el.setAttribute("data-stale", "1"); });
    }, every);
  }

  /* ================================================================ toasts */
  function dismissToast(t) {
    if (!t || t.classList.contains("is-leaving")) return;
    t.classList.add("is-leaving");
    setTimeout(function () { t.remove(); }, 200);
  }
  document.querySelectorAll(".toast").forEach(function (t) {
    var ms = t.classList.contains("error") ? 12000 : 6000, timer = setTimeout(function () { dismissToast(t); }, ms);
    t.addEventListener("mouseenter", function () { clearTimeout(timer); });
    t.addEventListener("mouseleave", function () { timer = setTimeout(function () { dismissToast(t); }, 3000); });
  });
  document.addEventListener("click", function (e) {
    var c = e.target.closest(".toast-close");
    if (c) dismissToast(c.closest(".toast"));
  });

  /* ================================================================ tooltips ([data-tip], chart bars) */
  var tip = document.createElement("div");
  tip.className = "tip";
  tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);
  function showTip(el) {
    var text = el.getAttribute("data-tip");
    if (!text) return;
    tip.textContent = text;
    tip.classList.add("show");
    var r = el.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    var bar = el.querySelector(".bar"), ar = bar ? bar.getBoundingClientRect() : r;
    var left = Math.max(8, Math.min(ar.left + ar.width / 2 - tw / 2, window.innerWidth - tw - 8));
    var top = ar.top - th - 8;
    if (top < 8) top = r.bottom + 8;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() { tip.classList.remove("show"); }
  function initTips(scope) {
    (scope || document).querySelectorAll(".chart-bar[title], [data-tip-from-title][title]").forEach(function (el) {
      el.setAttribute("data-tip", el.getAttribute("title"));
      el.removeAttribute("title");
    });
  }
  document.addEventListener("pointerover", function (e) { var el = e.target.closest && e.target.closest("[data-tip]"); if (el) showTip(el); });
  document.addEventListener("pointerout", function (e) { var el = e.target.closest && e.target.closest("[data-tip]"); if (el && !el.contains(e.relatedTarget)) hideTip(); });
  document.addEventListener("focusin", function (e) { var el = e.target.closest && e.target.closest("[data-tip]"); if (el) showTip(el); });
  document.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", hideTip, true);

  /* ================================================================ misc */
  document.querySelectorAll("[data-preview]").forEach(function (input) {
    var target = document.querySelector(input.getAttribute("data-preview"));
    if (!target) return;
    var fallback = target.textContent;
    function sync() { target.textContent = input.value || fallback; }
    input.addEventListener("input", sync);
    sync();
  });

  // Filters fold away on phones: forms with 3+ filters get a "More filters" toggle (see CSS).
  document.querySelectorAll("form.filters").forEach(function (form) {
    var fields = form.querySelectorAll(".f");
    if (fields.length <= 2) return;
    var active = 0;
    Array.prototype.forEach.call(fields, function (f, i) {
      if (i === 0) return;
      var input = f.querySelector("input:not([type=hidden]), select");
      if (input && input.value) active++;
    });
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-secondary filters-toggle";
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18l-7 8v6l-4-2v-4z"/></svg>' +
      (active ? "Filters · " + active + " on" : "More filters");
    btn.addEventListener("click", function () {
      var open = form.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    form.classList.add("foldable");
    fields[0].after(btn);
  });

  // Escape closes the mobile drawer (selects, menus and dialogs handle their own Escape first)
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (document.querySelector("details.menu[open]")) { closeMenus(); return; }
    if (root.classList.contains("nav-open")) setDrawer(false);
  });

  enhance(document);
  initTips(document);
  document.querySelectorAll("[data-poll]").forEach(poll);
  window.ccDashboard = { enhance: enhance, confirm: askConfirm, openDialog: openDialog, closeDialog: closeDialog };
})();
