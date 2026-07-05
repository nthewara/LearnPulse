/* LearnPulse — vanilla JS. Fetch JSON feeds, render summary + filterable timeline.
   All record fields are untrusted text: only ever assigned via textContent. */
(function () {
  "use strict";

  var KINDS = [
    { id: "new-feature", badge: "\u{1F195} New" },
    { id: "ga", badge: "✅ GA" },
    { id: "preview", badge: "\u{1F9EA} Preview" },
    { id: "deprecation", badge: "⚠️ Deprecation" },
    { id: "breaking-change", badge: "\u{1F4A5} Breaking" },
    { id: "doc-update", badge: "\u{1F4DD} Update" }
  ];
  var SHORT_NAMES = {
    "aks": "Azure Kubernetes Service",
    "kubernetes-fleet": "Fleet Manager",
    "application-network": "App Networking"
  };
  var RENDER_CAP = 100;

  var state = {
    records: [],            // merged, newest first
    productNames: {},       // id -> full name
    product: "all",
    kinds: new Set(),       // empty set = all kinds
    limit: RENDER_CAP
  };

  // ---------- tiny DOM helpers ----------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function link(href, text, className) {
    var a = el("a", className, text);
    a.href = href;
    a.rel = "noopener";
    return a;
  }

  function badgeFor(kind) {
    var def = KINDS.find(function (k) { return k.id === kind; });
    return el("span", "badge badge-" + (def ? def.id : "doc-update"),
      def ? def.badge : kind);
  }

  function formatDate(iso) { // "2026-07-02" -> "2 July 2026"
    var d = new Date(iso + "T00:00:00Z");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("en-GB",
      { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  }

  function relativeTime(iso) {
    var ms = Date.now() - new Date(iso).getTime();
    if (isNaN(ms)) return "";
    var hours = Math.floor(ms / 3600000);
    if (hours < 1) return "generated less than an hour ago";
    if (hours < 48) return "generated " + hours + (hours === 1 ? " hour ago" : " hours ago");
    return "generated " + Math.floor(hours / 24) + " days ago";
  }

  function recordUrl(rec) {
    return (rec.doc_urls && rec.doc_urls[0]) || rec.commit_url || "#";
  }

  // ---------- rendering ----------

  function renderSummary(summary) {
    var tiles = document.getElementById("stat-tiles");
    var counts = summary.counts_by_kind || {};
    var total = Object.keys(counts).reduce(function (sum, k) {
      return sum + (counts[k] || 0);
    }, 0);

    function tile(value, label) {
      var t = el("div", "tile");
      t.appendChild(el("div", "value", String(value)));
      t.appendChild(el("div", "label", label));
      return t;
    }

    tiles.appendChild(tile(total, "changes, last " + (summary.window_days || 7) + " days"));
    KINDS.forEach(function (k) {
      if (counts[k.id] > 0) tiles.appendChild(tile(counts[k.id], k.badge));
    });

    var top = summary.top_changes || [];
    if (top.length) {
      var list = document.getElementById("notable");
      top.forEach(function (rec) {
        var li = el("li");
        li.appendChild(badgeFor(rec.kind));
        li.appendChild(link(recordUrl(rec), rec.title || "(untitled)", "title"));
        li.appendChild(el("span", "date", formatDate(rec.date)));
        list.appendChild(li);
      });
      document.getElementById("notable-block").hidden = false;
    }
    document.getElementById("summary").hidden = false;
  }

  function chip(label, pressed, onClick) {
    var b = el("button", "chip", label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(pressed));
    b.addEventListener("click", onClick);
    return b;
  }

  function renderFilters(products) {
    var row = document.getElementById("product-filters");
    var options = [{ id: "all", name: "All" }].concat(products.map(function (p) {
      return { id: p.id, name: SHORT_NAMES[p.id] || p.name };
    }));
    options.forEach(function (opt) {
      row.appendChild(chip(opt.name, state.product === opt.id, function () {
        state.product = opt.id;
        history.replaceState(null, "",
          opt.id === "all" ? location.pathname + location.search : "#" + opt.id);
        syncChips(row, function (i) { return options[i].id === state.product; });
        applyFilters();
      }));
    });

    var kindsPresent = KINDS.filter(function (k) {
      return state.records.some(function (r) { return r.kind === k.id; });
    });
    if (kindsPresent.length > 1) {
      var kindRow = document.getElementById("kind-filters");
      kindsPresent.forEach(function (k) {
        kindRow.appendChild(chip(k.badge, false, function (ev) {
          if (state.kinds.has(k.id)) state.kinds.delete(k.id);
          else state.kinds.add(k.id);
          ev.currentTarget.setAttribute("aria-pressed", String(state.kinds.has(k.id)));
          applyFilters();
        }));
      });
    }
    document.getElementById("filters").hidden = false;
  }

  function syncChips(row, isPressed) {
    Array.prototype.forEach.call(row.children, function (b, i) {
      b.setAttribute("aria-pressed", String(isPressed(i)));
    });
  }

  function filteredRecords() {
    return state.records.filter(function (r) {
      if (state.product !== "all" && r.product !== state.product) return false;
      if (state.kinds.size && !state.kinds.has(r.kind)) return false;
      return true;
    });
  }

  function renderRecord(rec) {
    var card = el("article", "record");

    var head = el("div", "head");
    head.appendChild(badgeFor(rec.kind));
    head.appendChild(link(recordUrl(rec), rec.title || "(untitled)"));
    card.appendChild(head);

    if (rec.summary && rec.summary !== rec.title) {
      card.appendChild(el("p", "summary-text", rec.summary));
    }

    var meta = el("div", "meta");
    meta.appendChild(el("span", null, state.productNames[rec.product] || rec.product));
    meta.appendChild(el("span", "sep", "·"));
    meta.appendChild(el("span", null, formatDate(rec.date)));
    if (rec.commit_url) {
      meta.appendChild(el("span", "sep", "·"));
      meta.appendChild(link(rec.commit_url, "commit ↗"));
    }
    (rec.reasons || []).forEach(function (reason) {
      meta.appendChild(el("span", "tag", reason));
    });
    card.appendChild(meta);
    return card;
  }

  function renderTimeline() {
    var listEl = document.getElementById("timeline-list");
    var statusEl = document.getElementById("status");
    var moreBtn = document.getElementById("show-more");
    listEl.textContent = "";

    var records = filteredRecords();
    if (!records.length) {
      statusEl.textContent = state.records.length
        ? "No changes match this filter."
        : "No changes yet — the pipeline hasn't run.";
      statusEl.hidden = false;
      moreBtn.hidden = true;
      return;
    }
    statusEl.hidden = true;

    var shown = records.slice(0, state.limit);
    var frag = document.createDocumentFragment();
    var currentDate = null;
    shown.forEach(function (rec) {
      if (rec.date !== currentDate) {
        currentDate = rec.date;
        frag.appendChild(el("h3", "date-heading", formatDate(rec.date)));
      }
      frag.appendChild(renderRecord(rec));
    });
    listEl.appendChild(frag);

    var remaining = records.length - shown.length;
    moreBtn.hidden = remaining <= 0;
    if (remaining > 0) {
      moreBtn.textContent = "Show " + remaining + " more";
    }
  }

  function applyFilters() {
    state.limit = RENDER_CAP;
    renderTimeline();
  }

  // ---------- load ----------

  function getJSON(path) {
    return fetch(path).then(function (res) {
      if (!res.ok) throw new Error(path + " -> " + res.status);
      return res.json();
    });
  }

  function init() {
    var statusEl = document.getElementById("status");

    document.getElementById("show-more").addEventListener("click", function () {
      state.limit = Infinity;
      renderTimeline();
    });

    Promise.allSettled([getJSON("data/summary.json"), getJSON("data/products.json")])
      .then(function (results) {
        var summary = results[0].status === "fulfilled" ? results[0].value : null;
        var productsDoc = results[1].status === "fulfilled" ? results[1].value : null;

        if (summary && summary.generated_at) {
          document.getElementById("generated-at").textContent =
            relativeTime(summary.generated_at);
        }

        var products = (productsDoc && productsDoc.products) || [];
        if (!products.length) {
          statusEl.textContent = "Couldn't load data.";
          return;
        }
        products.forEach(function (p) { state.productNames[p.id] = p.name; });

        return Promise.allSettled(products.map(function (p) {
          return getJSON("data/" + p.id + ".json");
        })).then(function (feeds) {
          feeds.forEach(function (feed) {
            if (feed.status === "fulfilled" && Array.isArray(feed.value.records)) {
              state.records = state.records.concat(feed.value.records);
            }
          });
          if (feeds.every(function (f) { return f.status === "rejected"; })) {
            statusEl.textContent = "Couldn't load data.";
            return;
          }
          // Newest first; stable sort keeps each feed's internal order on ties.
          state.records.sort(function (a, b) {
            return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
          });

          var hash = decodeURIComponent(location.hash.slice(1));
          if (state.productNames[hash]) state.product = hash;

          if (summary) renderSummary(summary);
          renderFilters(products);
          renderTimeline();
        });
      })
      .catch(function () {
        statusEl.textContent = "Couldn't load data.";
      });
  }

  init();
})();
