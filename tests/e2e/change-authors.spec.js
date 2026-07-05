const { test, expect } = require("@playwright/test");

const summary = {
  generated_at: "2026-07-05T00:00:00Z",
  window_days: 7,
  counts_by_kind: {
    "new-feature": 0,
    "ga": 0,
    "preview": 0,
    "deprecation": 0,
    "breaking-change": 0,
    "doc-update": 8
  },
  counts_by_product: { aks: 8 },
  top_changes: []
};

const products = {
  products: [{ id: "aks", name: "Azure Kubernetes Service" }]
};

function record(overrides) {
  return Object.assign({
    id: overrides.id,
    product: "aks",
    date: "2026-07-05",
    kind: "doc-update",
    title: "Documentation change",
    summary: "Updated documentation page.",
    change_summary: "Updated documentation page.",
    page_change_category: "existing-page",
    batch_key: overrides.id,
    reasons: ["doc-update"],
    files: ["articles/aks/" + overrides.id + ".md"],
    doc_urls: ["https://learn.microsoft.com/azure/aks/" + overrides.id],
    commit_url: "https://github.com/MicrosoftDocs/azure-aks-docs/commit/" + overrides.id,
    sha: overrides.id
  }, overrides);
}

const feed = {
  product: "aks",
  generated_at: "2026-07-05T00:00:00Z",
  records: [
    record({
      id: "single-author",
      title: "Single author change",
      author: "octocat",
      author_name: "Ignored Name"
    }),
    record({
      id: "overflow-a",
      date: "2026-07-04",
      summary: "Overflow authors batch.",
      change_summary: "Overflow authors batch.",
      batch_key: "overflow-authors",
      author: "first-user"
    }),
    record({
      id: "overflow-b",
      date: "2026-07-04",
      summary: "Overflow authors batch.",
      change_summary: "Overflow authors batch.",
      batch_key: "overflow-authors",
      author_name: "Display Name"
    }),
    record({
      id: "overflow-c",
      date: "2026-07-04",
      summary: "Overflow authors batch.",
      change_summary: "Overflow authors batch.",
      batch_key: "overflow-authors",
      author: "third-user"
    }),
    record({
      id: "legacy-no-author",
      date: "2026-07-03",
      title: "Legacy authorless change"
    }),
    record({
      id: "bot-a",
      date: "2026-07-02",
      summary: "Bot-only batch.",
      change_summary: "Bot-only batch.",
      batch_key: "bot-only",
      author: "dependabot[bot]"
    }),
    record({
      id: "bot-b",
      date: "2026-07-02",
      summary: "Bot-only batch.",
      change_summary: "Bot-only batch.",
      batch_key: "bot-only",
      author: "github-actions"
    }),
    record({
      id: "invalid-a",
      date: "2026-07-01",
      summary: "Invalid authors batch.",
      change_summary: "Invalid authors batch.",
      batch_key: "invalid-authors",
      author: "javascript:alert(1)",
      author_name: "Safe Fallback"
    }),
    record({
      id: "invalid-b",
      date: "2026-07-01",
      summary: "Invalid authors batch.",
      change_summary: "Invalid authors batch.",
      batch_key: "invalid-authors",
      author: "a/../evil"
    })
  ]
};

test.beforeEach(async ({ page }) => {
  await page.route("**/data/summary.json", route => {
    route.fulfill({ contentType: "application/json", body: JSON.stringify(summary) });
  });
  await page.route("**/data/products.json", route => {
    route.fulfill({ contentType: "application/json", body: JSON.stringify(products) });
  });
  await page.route("**/data/aks.json", route => {
    route.fulfill({ contentType: "application/json", body: JSON.stringify(feed) });
  });
});

test("renders valid author logins as GitHub links after commit metadata", async ({ page }) => {
  await page.goto("/");

  const card = page.locator(".record", { hasText: "Single author change" });
  const meta = card.locator(".meta");
  const authorLink = meta.locator(".authors a.author", { hasText: "@octocat" });

  await expect(meta).toHaveText(/1 commit.*doc update.*@octocat/);
  await expect(authorLink).toHaveAttribute("href", "https://github.com/octocat");
});

test("shows two distinct authors with overflow in encounter order", async ({ page }) => {
  await page.goto("/");

  const authors = page.locator(".record", { hasText: "Overflow authors batch." })
    .locator(".authors");

  await expect(authors).toHaveText("@first-user, Display Name +1");
  await expect(authors.locator("a.author")).toHaveCount(1);
});

test("omits author segment for legacy and bot-only batches", async ({ page }) => {
  await page.goto("/");

  const legacy = page.locator(".record", { hasText: "Legacy authorless change" });
  const bots = page.locator(".record", { hasText: "Bot-only batch." });

  await expect(legacy.locator(".authors")).toHaveCount(0);
  await expect(legacy.locator(".meta")).toHaveText("1 commit·doc update");
  await expect(bots.locator(".authors")).toHaveCount(0);
  await expect(bots.locator(".meta")).not.toContainText("dependabot");
  await expect(bots.locator(".meta")).not.toContainText("github-actions");
});

test("renders invalid author values as plain text without GitHub links", async ({ page }) => {
  await page.goto("/");

  const authors = page.locator(".record", { hasText: "Invalid authors batch." })
    .locator(".authors");

  await expect(authors).toHaveText("javascript:alert(1), a/../evil");
  await expect(authors.locator("a")).toHaveCount(0);
  await expect(page.locator("a[href^='https://github.com/javascript']")).toHaveCount(0);
  await expect(page.locator("a[href*='a/../evil']")).toHaveCount(0);
});

test("does not request avatars or other external resources", async ({ page }) => {
  const remoteRequests = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:4173") remoteRequests.push(url.href);
  });

  await page.goto("/");
  await expect(page.locator(".record")).toHaveCount(5);
  expect(remoteRequests).toEqual([]);
});
