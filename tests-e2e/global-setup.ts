import { chromium, type FullConfig } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { loginViaApi } from "./fixtures/auth";

const AUTH_DIR = ".auth";
const STORAGE_STATE = path.join(AUTH_DIR, "session.json");

async function globalSetup(_config: FullConfig): Promise<void> {
  await mkdir(AUTH_DIR, { recursive: true });

  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    await loginViaApi(context.request);
    const state = await context.storageState();
    state.cookies = state.cookies.map((cookie) => {
      const domain = cookie.domain.replace(/^\./, "");
      if (domain === "localhost" || domain === "127.0.0.1" || domain === "::1") {
        return { ...cookie, secure: false };
      }
      return cookie;
    });
    await writeFile(STORAGE_STATE, JSON.stringify(state, null, 2));
    console.log(`[e2e:setup] storageState persisted → ${STORAGE_STATE}`);
  } finally {
    await browser.close();
  }
}

export default globalSetup;
