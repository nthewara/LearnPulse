// @ts-check
const { defineConfig } = require("@playwright/test");

const useSystemChrome = process.env.LEARNPULSE_USE_SYSTEM_CHROME === "1";

module.exports = defineConfig({
  testDir: "tests/e2e",
  use: {
    baseURL: "http://127.0.0.1:4173",
    ...(useSystemChrome ? { channel: "chrome" } : {})
  },
  webServer: {
    command: "python3 -m http.server 4173 --bind 127.0.0.1 --directory docs",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI
  }
});
