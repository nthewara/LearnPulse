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
    "application-network": "App Networking",
    "azure-openai": "Azure OpenAI",
    "foundry-agents": "Foundry Agents",
    "azure-ai-search": "AI Search",
    "document-intelligence": "Doc Intelligence",
    "foundry-local": "Foundry Local"
  };
  var RENDER_CAP = 100;
  var PAGE_CATEGORIES = [
    { id: "existing-page", label: "Changes to existing pages" },
    { id: "new-page", label: "New pages added" }
  ];
  var KIND_WEIGHT = {
    "breaking-change": 5,
    "deprecation": 4,
    "ga": 3,
    "new-feature": 2,
    "preview": 1,
    "doc-update": 0
  };

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

  function fileNameOf(file) {
    if (!file) return "";
    return typeof file === "string" ? file : (file.filename || "");
  }

  function statusOf(file) {
    return file && typeof file === "object" ? file.status : "";
  }

  function isMarkdownPage(filename) {
    var low = (filename || "").toLowerCase();
    return low.slice(-3) === ".md" &&
      low.indexOf("/includes/") === -1 &&
      low.indexOf("/media/") === -1;
  }

  function pageChangeCategory(rec) {
    if (rec.page_change_category === "new-page" ||
        rec.page_change_category === "existing-page") {
      return rec.page_change_category;
    }

    var files = rec.files || [];
    var reasons = rec.reasons || [];
    var hasNewFileReason = reasons.indexOf("new-file") !== -1;

    if (files.some(function (file) {
      return statusOf(file) === "added" && isMarkdownPage(fileNameOf(file));
    })) {
      return "new-page";
    }
    if (hasNewFileReason && files.some(function (file) {
      return isMarkdownPage(fileNameOf(file));
    })) {
      return "new-page";
    }
    if (files.some(function (file) {
      var status = statusOf(file);
      var filename = fileNameOf(file);
      return isMarkdownPage(filename) &&
        (!status || status === "modified" || status === "renamed");
    })) {
      return "existing-page";
    }
    return "existing-page";
  }

  function prettyFileName(filename) {
    var name = (filename || "").split("/").pop() || "page";
    name = name.replace(/\.md$/i, "").replace(/[-_]+/g, " ");
    return name.replace(/\b\w/g, function (ch) { return ch.toUpperCase(); });
  }

  function pageNames(rec) {
    var seen = {};
    return (rec.files || []).map(fileNameOf).filter(isMarkdownPage).map(function (filename) {
      return prettyFileName(filename);
    }).filter(function (name) {
      if (seen[name]) return false;
      seen[name] = true;
      return true;
    });
  }

  function normalizeText(text) {
    return (text || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  function cleanSummaryText(text) {
    text = (text || "").trim();
    if (/applies\s+to/i.test(text)) {
      var applies = [];
      ["AKS Automatic", "AKS Standard"].forEach(function (label) {
        if (text.toLowerCase().indexOf(label.toLowerCase()) !== -1) applies.push(label);
      });
      if (applies.length) {
        return "Applies-to matrix now includes " +
          (applies.length === 1 ? applies[0] : applies.slice(0, -1).join(", ") + " and " + applies[applies.length - 1]) +
          ".";
      }
    }
    text = text.replace(/^Notable addition:\s*/i, "");
    text = text.replace(/\[[^\]]+\]\([^)]+\)/g, function (match) {
      return match.replace(/^\[|\]\([^)]+\)$/g, "");
    });
    text = text.replace(/:[a-z0-9_+-]+:/gi, " ");
    text = text.replace(/[*_`]+/g, "");
    text = text.replace(/\s+/g, " ").trim();
    if (text && !/[.!?]"?$/.test(text)) text += ".";
    return text;
  }

  function stripTitlePrefix(summary, title) {
    if (!summary || !title) return summary;
    var lowerSummary = summary.toLowerCase();
    var lowerTitle = title.toLowerCase();
    var prefixes = [lowerTitle + ". ", lowerTitle + ": ", lowerTitle + " — ", lowerTitle + " - "];
    for (var i = 0; i < prefixes.length; i += 1) {
      if (lowerSummary.indexOf(prefixes[i]) === 0) {
        return summary.slice(prefixes[i].length).trim();
      }
    }
    return summary;
  }

  function derivedSummary(rec) {
    var names = pageNames(rec);
    var count = names.length;
    var shown = names.slice(0, 2).join(", ");
    if (count > 2) shown += " and " + (count - 2) + " more";

    if (pageChangeCategory(rec) === "new-page") {
      return count
        ? "Added " + (count === 1 ? "a new page: " : count + " new pages: ") + shown + "."
        : "Added a new documentation page.";
    }

    if ((rec.reasons || []).indexOf("retired-page") !== -1) {
      return count
        ? "Retired " + (count === 1 ? "page: " : count + " pages: ") + shown + "."
        : "Retired a documentation page.";
    }

    return count
      ? "Updated " + (count === 1 ? "existing page: " : count + " existing pages: ") + shown + "."
      : "Updated an existing documentation page.";
  }

  function summaryForRecord(rec) {
    var summary = (rec.change_summary || rec.summary || "").trim();
    var title = (rec.title || "").trim();
    if (summary) {
      var stripped = stripTitlePrefix(summary, title);
      if (stripped && normalizeText(stripped) !== normalizeText(title)) {
        return cleanSummaryText(stripped);
      }
      if (normalizeText(summary) !== normalizeText(title)) {
        return cleanSummaryText(summary);
      }
    }
    return derivedSummary(rec);
  }

  function bestKind(records) {
    return records.reduce(function (best, rec) {
      return (KIND_WEIGHT[rec.kind] || 0) > (KIND_WEIGHT[best] || 0) ? rec.kind : best;
    }, "doc-update");
  }

  function firstUrl(records) {
    for (var i = 0; i < records.length; i += 1) {
      var url = recordUrl(records[i]);
      if (url && url !== "#") return url;
    }
    return "#";
  }

  function pageEntries(records) {
    var seen = {};
    var entries = [];
    records.forEach(function (rec) {
      var names = pageNames(rec);
      var urls = rec.doc_urls || [];
      names.forEach(function (name, i) {
        var key = name.toLowerCase();
        if (seen[key]) return;
        seen[key] = true;
        entries.push({ name: name, url: urls[i] || urls[0] || recordUrl(rec) });
      });
    });
    return entries;
  }

  function batchKey(rec) {
    return [
      pageChangeCategory(rec),
      rec.date || "",
      rec.product || "",
      rec.batch_key || normalizeText(summaryForRecord(rec) || rec.title || "")
    ].join("|");
  }

  function batchRecords(records) {
    var batches = [];
    var byKey = {};
    records.forEach(function (rec) {
      var key = batchKey(rec);
      if (!byKey[key]) {
        byKey[key] = {
          key: key,
          category: pageChangeCategory(rec),
          date: rec.date,
          product: rec.product,
          summary: summaryForRecord(rec),
          records: []
        };
        batches.push(byKey[key]);
      }
      byKey[key].records.push(rec);
    });
    batches.forEach(function (batch) {
      batch.kind = bestKind(batch.records);
      batch.pages = pageEntries(batch.records);
    });
    return batches;
  }

  function batchTitle(batch) {
    return SHORT_NAMES[batch.product] ||
      state.productNames[batch.product] ||
      batch.product ||
      "Documentation change";
  }

  function countLabel(batch) {
    var changes = batch.records.length;
    var pages = batch.pages.length;
    if (changes === 1 && pages <= 1) return "1 change";
    if (pages > 1) return changes + " changes across " + pages + " pages";
    return changes + " changes";
  }

  function appendPageLinks(container, pages) {
    pages.slice(0, 4).forEach(function (page, index) {
      if (index > 0) container.appendChild(document.createTextNode(", "));
      container.appendChild(link(page.url || "#", page.name));
    });
    if (pages.length > 4) {
      container.appendChild(document.createTextNode(" and " + (pages.length - 4) + " more"));
    }
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
    var kindRow = document.getElementById("kind-filters");
    if (kindsPresent.length > 1) {
      kindsPresent.forEach(function (k) {
        kindRow.appendChild(chip(k.badge, false, function (ev) {
          if (state.kinds.has(k.id)) state.kinds.delete(k.id);
          else state.kinds.add(k.id);
          ev.currentTarget.setAttribute("aria-pressed", String(state.kinds.has(k.id)));
          applyFilters();
        }));
      });
    } else if (kindRow.parentNode) {
      kindRow.parentNode.hidden = true;
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

  function renderBatch(batch) {
    var card = el("article", "record batch-record");

    var head = el("div", "head");
    head.appendChild(badgeFor(batch.kind));
    head.appendChild(link(firstUrl(batch.records), batchTitle(batch), "card-title"));
    head.appendChild(el("span", "count-pill", countLabel(batch)));
    card.appendChild(head);

    if (batch.summary) {
      card.appendChild(el("p", "card-summary", batch.summary));
    }

    if (batch.pages.length) {
      var pages = el("p", "card-pages");
      pages.appendChild(el("span", "card-pages-label",
        pageChangeCategory(batch.records[0]) === "new-page" ? "New page: " : "Affected pages: "
      ));
      appendPageLinks(pages, batch.pages);
      card.appendChild(pages);
    }

    var meta = el("div", "meta");
    meta.appendChild(el("span", null, formatDate(batch.date)));
    meta.appendChild(el("span", "sep", "·"));
    meta.appendChild(el("span", null, batch.records.length === 1 ? "1 commit" : batch.records.length + " commits"));
    var reasons = {};
    batch.records.forEach(function (rec) {
      (rec.reasons || []).forEach(function (reason) { reasons[reason] = true; });
    });
    Object.keys(reasons).slice(0, 4).forEach(function (reason) {
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
    var batches = batchRecords(shown);
    var grouped = {};
    PAGE_CATEGORIES.forEach(function (category) {
      grouped[category.id] = [];
    });
    batches.forEach(function (batch) {
      grouped[batch.category].push(batch);
    });

    var frag = document.createDocumentFragment();
    PAGE_CATEGORIES.forEach(function (category) {
      var categoryBatches = grouped[category.id];
      if (!categoryBatches.length) return;
      var section = el("section", "category-section");
      section.appendChild(el("h2", "category-heading", category.label));
      var currentDate = null;
      categoryBatches.forEach(function (batch) {
        if (batch.date !== currentDate) {
          currentDate = batch.date;
          section.appendChild(el("h3", "date-heading", formatDate(batch.date)));
        }
        section.appendChild(renderBatch(batch));
      });
      frag.appendChild(section);
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
