/* LearnPulse — vanilla JS. Fetch JSON feeds, render summary + filterable timeline.
   All record fields are untrusted text: only ever assigned via textContent. */
(function () {
  "use strict";

  var KINDS = [
    { id: "new-feature", badge: "New" },
    { id: "ga", badge: "GA" },
    { id: "preview", badge: "Preview" },
    { id: "deprecation", badge: "Deprecation" },
    { id: "breaking-change", badge: "Breaking" },
    { id: "doc-update", badge: "Update" }
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
  var REASON_LABELS = {
    "keyword:preview": "mentions preview",
    "keyword:breaking-change": "mentions breaking change",
    "keyword:deprecation": "mentions deprecation",
    "keyword:ga": "mentions GA",
    "keyword:new-feature": "mentions new feature",
    "table-edit": "table edit",
    "new-file": "new page",
    "retired-page": "retired page",
    "doc-update": "doc update"
  };
  var SUMMARY_LIMIT = 220;

  var state = {
    records: [],            // merged, newest first
    productNames: {},       // id -> full name
    summary: null,
    product: "all",
    kinds: new Set(),       // empty set = all kinds
    limit: RENDER_CAP,
    pulseRendered: false
  };

  // ---------- tiny DOM helpers ----------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function safeHttpsHref(href) {
    href = typeof href === "string" ? href.trim() : "";
    return href && href !== "#" && /^https:\/\//.test(href) ? href : "";
  }

  function link(href, text, className) {
    href = safeHttpsHref(href);
    if (!href) {
      return el("span", className, text);
    }
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

  function formatDateShort(iso) { // "2026-07-02" -> "2 Jul"
    var d = new Date(iso + "T00:00:00Z");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("en-GB",
      { day: "numeric", month: "short", timeZone: "UTC" });
  }

  function relativeTime(iso) {
    var ms = Date.now() - new Date(iso).getTime();
    if (isNaN(ms)) return "";
    var hours = Math.floor(ms / 3600000);
    if (hours < 1) return "generated less than an hour ago";
    if (hours < 48) return "generated " + hours + (hours === 1 ? " hour ago" : " hours ago");
    return "generated " + Math.floor(hours / 24) + " days ago";
  }

  function isStaleGeneratedAt(iso) {
    var ms = Date.now() - new Date(iso).getTime();
    return !isNaN(ms) && ms > 24 * 3600000;
  }

  function recordUrl(rec) {
    return (rec.doc_urls && rec.doc_urls[0]) || rec.commit_url || "";
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

  function truncateText(text, max) {
    text = (text || "").trim();
    if (text.length <= max) return text;
    var cut = text.lastIndexOf(" ", max - 1);
    if (cut < Math.floor(max * 0.6)) cut = max - 1;
    return text.slice(0, cut).replace(/[.,;:!?-]+$/, "") + "…";
  }

  function firstSentenceEnd(text, max) {
    text = (text || "").trim();
    var end = text.indexOf(". ");
    if (end !== -1 && end + 1 <= max) return end + 1;
    return text.length <= max ? text.length : 0;
  }

  function firstSentence(text) {
    text = (text || "").trim();
    var end = firstSentenceEnd(text, 110);
    return end ? text.slice(0, end).trim() : truncateText(text, 110);
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

  function truncateSummary(text) {
    text = (text || "").trim();
    var listMatch = /(?:^|\s)\d+\.\s/.exec(text);
    if (listMatch) {
      text = text.slice(0, listMatch.index).trim();
      if (text && !/[.!?]"?$/.test(text)) text += ".";
    }
    if (!text || text.length <= SUMMARY_LIMIT) return text;

    var cut = 0;
    var re = /[.!?]["')\]]?(?=\s)/g;
    var match;
    while ((match = re.exec(text)) !== null) {
      if (match.index + match[0].length > SUMMARY_LIMIT) break;
      cut = match.index + match[0].length;
    }
    if (cut > 0) return text.slice(0, cut).trim();

    cut = text.lastIndexOf(" ", SUMMARY_LIMIT);
    if (cut > 0) {
      text = text.slice(0, cut).replace(/[,:;—-]+$/g, "").trim();
      if (text && !/[.!?]"?$/.test(text)) text += ".";
    } else {
      text = text.slice(0, SUMMARY_LIMIT).trim();
    }
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
        return truncateSummary(cleanSummaryText(stripped)) || derivedSummary(rec);
      }
      if (normalizeText(summary) !== normalizeText(title)) {
        return truncateSummary(cleanSummaryText(summary)) || derivedSummary(rec);
      }
    }
    return derivedSummary(rec);
  }

  function reasonLabel(reason) {
    if (REASON_LABELS[reason]) return REASON_LABELS[reason];
    return (reason || "")
      .replace(/^keyword:/, "mentions ")
      .replace(/:/g, " ")
      .replace(/[-_]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function bestKind(records) {
    return records.reduce(function (best, rec) {
      return (KIND_WEIGHT[rec.kind] || 0) > (KIND_WEIGHT[best] || 0) ? rec.kind : best;
    }, "doc-update");
  }

  function firstUrl(records) {
    for (var i = 0; i < records.length; i += 1) {
      var url = safeHttpsHref(recordUrl(records[i]));
      if (url) return url;
    }
    return "";
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
    return productName(batch.product);
  }

  function productName(product) {
    return SHORT_NAMES[product] ||
      state.productNames[product] ||
      product ||
      "Documentation change";
  }

  function cleanTitle(title) {
    title = (title || "").trim();
    if (!title) return "";
    if (/^merge pull request/i.test(title)) return "";
    if (/^updated? documentation page\.?$/i.test(title)) return "";
    return title.length <= 140 ? title : "";
  }

  function batchHeadline(batch) {
    var rec = batch.records.length === 1 ? batch.records[0] : null;
    var title = rec ? cleanTitle(rec.title) : "";
    return title || firstSentence(batch.summary) || "Documentation change";
  }

  function cardSummary(batch, headline) {
    var summary = (batch.summary || "").trim();
    if (!summary || normalizeText(summary) === normalizeText(headline)) return "";
    var end = firstSentenceEnd(summary, 110);
    if (end && normalizeText(summary.slice(0, end)) === normalizeText(headline)) {
      return summary.slice(end).trim();
    }
    return summary;
  }

  function countLabel(batch) {
    var changes = batch.records.length;
    var pages = batch.pages.length;
    if (changes === 1 && pages <= 1) return "1 change";
    return changes + " " + (changes === 1 ? "change" : "changes") +
      (pages > 1 ? " across " + pages + " pages" : "");
  }

  function appendPageLinks(container, pages) {
    pages.slice(0, 4).forEach(function (page, index) {
      if (index > 0) container.appendChild(document.createTextNode(", "));
      container.appendChild(link(page.url, page.name));
    });
    if (pages.length > 4) {
      container.appendChild(document.createTextNode(" and " + (pages.length - 4) + " more"));
    }
  }

  // ---------- rendering ----------

  var SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    }
    return node;
  }

  function dayKey(d) { // Date -> "YYYY-MM-DD" in UTC
    return d.toISOString().slice(0, 10);
  }

  // Per-day change counts over the last `days` days, anchored to today in UTC.
  function pulseBuckets(days, records) {
    var counts = {};
    records.forEach(function (r) {
      if (!r.date) return;
      counts[r.date] = (counts[r.date] || 0) + 1;
    });
    var now = new Date();
    var end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    var series = [];
    for (var i = days - 1; i >= 0; i -= 1) {
      var d = new Date(end.getTime() - i * 86400000);
      var key = dayKey(d);
      series.push({ date: key, count: counts[key] || 0 });
    }
    return series;
  }

  // Signature element: an EKG-style pulse trace of daily change volume.
  // Built entirely with DOM APIs — no innerHTML, no record-derived markup.
  function renderPulse(records) {
    var host = document.getElementById("pulse");
    if (!host) return;
    records = records || productFilteredRecords();
    Array.prototype.forEach.call(host.querySelectorAll(".pulse-svg"), function (svg) {
      svg.parentNode.removeChild(svg);
    });
    host.classList.remove("pulse-live");

    var days = 14;
    var series = pulseBuckets(days, records);
    var W = 260, H = 48, padY = 7, padX = 3;
    var baseline = H - padY;
    var top = padY;
    var max = series.reduce(function (m, p) { return Math.max(m, p.count); }, 0);
    var span = W - padX * 2;
    var stepX = days > 1 ? span / (days - 1) : 0;

    function xAt(i) { return padX + i * stepX; }
    function yAt(count) {
      if (max <= 0) return baseline;
      return baseline - (count / max) * (baseline - top);
    }

    // Build an EKG-like trace: a flat lead into each sample, then a sharp
    // deflection to the sample value. Isolated spikes read like a heartbeat.
    var pts = [];
    series.forEach(function (p, i) {
      var x = xAt(i);
      var y = yAt(p.count);
      if (p.count > 0 && stepX > 0) {
        pts.push([x - stepX * 0.28, baseline]);
        pts.push([x, y]);
        pts.push([x + stepX * 0.28, baseline]);
      } else {
        pts.push([x, baseline]);
      }
    });

    var svg = svgEl("svg", {
      viewBox: "0 0 " + W + " " + H,
      preserveAspectRatio: "none",
      class: "pulse-svg",
      focusable: "false"
    });
    svg.setAttribute("aria-hidden", "true");

    // Faint zero baseline — the instrument's resting line.
    svg.appendChild(svgEl("line", {
      x1: padX, y1: baseline, x2: W - padX, y2: baseline, class: "pulse-base"
    }));

    var d = pts.map(function (p, i) {
      return (i === 0 ? "M" : "L") + p[0].toFixed(2) + " " + p[1].toFixed(2);
    }).join(" ");
    var trace = svgEl("path", { d: d, class: "pulse-trace" });
    svg.appendChild(trace);

    // Leading cursor dot at the most recent sample — a live-monitor touch.
    var last = series[series.length - 1];
    svg.appendChild(svgEl("circle", {
      cx: xAt(days - 1), cy: yAt(last.count), r: 2.6, class: "pulse-cursor"
    }));

    var caption = host.querySelector(".pulse-caption");
    host.insertBefore(svg, caption || null);

    var reduce = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!state.pulseRendered && !reduce && typeof trace.getTotalLength === "function") {
      var len = trace.getTotalLength();
      if (len && isFinite(len)) {
        trace.style.strokeDasharray = len;
        trace.style.strokeDashoffset = len;
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { host.classList.add("pulse-live"); });
        });
      }
    }
    state.pulseRendered = true;
  }

  function summaryWindowStart(windowDays) {
    var anchor = state.summary && state.summary.generated_at ?
      new Date(state.summary.generated_at) : new Date();
    if (isNaN(anchor)) anchor = new Date();
    return dayKey(new Date(anchor.getTime() - windowDays * 86400000));
  }

  function countsByKind(records, windowDays) {
    var start = summaryWindowStart(windowDays);
    var counts = {};
    KINDS.forEach(function (k) { counts[k.id] = 0; });
    records.forEach(function (rec) {
      if (!rec.date || rec.date < start) return;
      counts[rec.kind] = (counts[rec.kind] || 0) + 1;
    });
    return counts;
  }

  function renderTiles(records, windowDays, fallbackCounts) {
    var tiles = document.getElementById("stat-tiles");
    var counts = fallbackCounts || countsByKind(records, windowDays);
    var total = Object.keys(counts).reduce(function (sum, k) {
      return sum + (counts[k] || 0);
    }, 0);
    tiles.textContent = "";

    function tile(value, label) {
      var t = el("div", "tile");
      t.appendChild(el("div", "value", String(value)));
      t.appendChild(el("div", "label", label));
      return t;
    }

    tiles.appendChild(tile(total, "changes, last " + windowDays + " days"));
    KINDS.forEach(function (k) {
      if (counts[k.id] > 0) tiles.appendChild(tile(counts[k.id], k.badge));
    });
  }

  function betterNotable(next, current) {
    if (!current) return true;
    var nextWeight = KIND_WEIGHT[next.kind] || 0;
    var currentWeight = KIND_WEIGHT[current.kind] || 0;
    if (nextWeight !== currentWeight) return nextWeight > currentWeight;
    return (next.date || "") > (current.date || "");
  }

  function notableRows(top) {
    var byTitle = {};
    top.forEach(function (rec) {
      var title = (rec.title || "").trim();
      if (!title || /^merge pull request/i.test(title)) return;
      if (state.product !== "all" && rec.product !== state.product) return;
      var key = normalizeText(title);
      if (!key) return;
      if (!byTitle[key]) byTitle[key] = { rec: rec, count: 0 };
      byTitle[key].count += 1;
      if (betterNotable(rec, byTitle[key].rec)) byTitle[key].rec = rec;
    });
    return Object.keys(byTitle).map(function (key) {
      return byTitle[key];
    }).sort(function (a, b) {
      var weight = (KIND_WEIGHT[b.rec.kind] || 0) - (KIND_WEIGHT[a.rec.kind] || 0);
      if (weight) return weight;
      return (a.rec.date < b.rec.date ? 1 : a.rec.date > b.rec.date ? -1 : 0);
    });
  }

  function renderNotable(top) {
    var list = document.getElementById("notable");
    var block = document.getElementById("notable-block");
    var heading = block.querySelector("h2");
    var rows = notableRows(top || []);
    var previousDate = "";
    list.textContent = "";
    if (heading) {
      heading.textContent = state.product === "all"
        ? "Notable this week"
        : "Notable this week — " + productName(state.product);
    }
    rows.forEach(function (row) {
      var rec = row.rec;
      var li = el("li");
      li.appendChild(badgeFor(rec.kind));
      li.appendChild(link(recordUrl(rec), rec.title || "(untitled)", "title"));
      if (row.count > 1) li.appendChild(el("span", "count-pill", "×" + row.count));
      li.appendChild(el("span", "product-tag", productName(rec.product)));
      if (rec.date !== previousDate) {
        li.appendChild(el("span", "date", formatDateShort(rec.date)));
        previousDate = rec.date;
      }
      list.appendChild(li);
    });
    block.hidden = rows.length === 0;
  }

  function renderSummary(summary, useFallbackCounts) {
    var windowDays = (summary && summary.window_days) || 7;
    renderTiles(productFilteredRecords(), windowDays,
      useFallbackCounts && state.product === "all" ? summary.counts_by_kind : null);
    renderNotable((summary && summary.top_changes) || []);
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

  function productFilteredRecords() {
    return state.records.filter(function (r) {
      return state.product === "all" || r.product === state.product;
    });
  }

  function renderBatch(batch) {
    var card = el("article", "record batch-record record-" + batch.kind);
    var headline = batchHeadline(batch);
    var summary = cardSummary(batch, headline);

    var head = el("div", "head");
    head.appendChild(badgeFor(batch.kind));
    head.appendChild(link(firstUrl(batch.records), headline, "card-title"));
    head.appendChild(el("span", "product-tag", batchTitle(batch)));
    head.appendChild(el("span", "count-pill", countLabel(batch)));
    card.appendChild(head);

    if (summary) {
      card.appendChild(el("p", "card-summary", summary));
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
    meta.appendChild(el("span", null, batch.records.length === 1 ? "1 commit" : batch.records.length + " commits"));
    var reasons = {};
    batch.records.forEach(function (rec) {
      (rec.reasons || []).forEach(function (reason) { reasons[reason] = true; });
    });
    var reasonKeys = Object.keys(reasons).slice(0, 4);
    if (reasonKeys.length) meta.appendChild(el("span", "sep", "·"));
    reasonKeys.forEach(function (reason) {
      var tag = el("span", "tag", reasonLabel(reason));
      tag.title = reason;
      meta.appendChild(tag);
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
    if (state.summary) renderSummary(state.summary, false);
    renderTimeline();
    renderPulse(productFilteredRecords());
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
        state.summary = summary;

        if (summary && summary.generated_at) {
          var generatedAt = document.getElementById("generated-at");
          var generatedText = relativeTime(summary.generated_at);
          if (isStaleGeneratedAt(summary.generated_at)) {
            generatedAt.classList.add("stale");
            generatedText = "data may be stale — " + generatedText;
          } else {
            generatedAt.classList.remove("stale");
          }
          generatedAt.textContent = generatedText;
        }

        var products = (productsDoc && productsDoc.products) || [];
        if (!products.length) {
          statusEl.textContent = "Couldn't load data.";
          renderPulse();
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
            renderPulse();
            return;
          }
          // Newest first; stable sort keeps each feed's internal order on ties.
          state.records.sort(function (a, b) {
            return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
          });

          var hash = decodeURIComponent(location.hash.slice(1));
          if (state.productNames[hash]) state.product = hash;

          if (summary) renderSummary(summary, true);
          renderFilters(products);
          renderTimeline();
          renderPulse(productFilteredRecords());
        });
      })
      .catch(function () {
        statusEl.textContent = "Couldn't load data.";
      });
  }

  init();
})();
