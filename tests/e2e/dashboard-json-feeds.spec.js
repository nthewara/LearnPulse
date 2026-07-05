const fs = require("fs");
const path = require("path");
const { test, expect } = require("@playwright/test");

const repoRoot = path.resolve(__dirname, "../..");

function readJSON(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(repoRoot, relativePath), "utf8"));
}

function pageLabel(filePath) {
  return path.basename(filePath, ".md")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, ch => ch.toUpperCase());
}

function firstFeedWithMarkdownRecord() {
  const products = readJSON("docs/data/products.json").products;
  for (const product of products) {
    const feed = readJSON(`docs/data/${product.id}.json`);
    const record = feed.records.find(item => (item.files || []).some(file => file.endsWith(".md")));
    if (record) return { products, product, record };
  }
  throw new Error("Expected at least one generated JSON feed record with a markdown file");
}

test("loads committed JSON feeds and renders dashboard cards locally", async ({ page }) => {
  const { products, record } = firstFeedWithMarkdownRecord();
  const expectedPage = pageLabel(record.files.find(file => file.endsWith(".md")));
  const remoteRequests = [];

  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:4173") remoteRequests.push(url.href);
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Learn Pulse" })).toBeVisible();
  await expect(page.locator("#summary")).toBeVisible();
  await expect(page.locator("#product-filters button")).toHaveCount(products.length + 1);
  await expect(page.locator(".record").first()).toBeVisible();
  await expect(page.getByRole("link", { name: expectedPage }).first()).toBeVisible();
  await expect(page.locator(".meta").first()).toContainText(/commit/);
  expect(remoteRequests).toEqual([]);
});
