import { afterEach, describe, expect, it, vi } from "vitest";

import { isApiError } from "@/lib/api";

import { streamMessage } from "./client";

// Se mockea únicamente el transporte (apiFetch); la política de saneamiento
// real de @/lib/api queda activa para verificar que es una sola y compartida.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: vi.fn() };
});

const { apiFetch } = await import("@/lib/api");
const apiFetchMock = vi.mocked(apiFetch);

function sseResponse(frames: string[], requestId = "req-sse-1"): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "x-request-id": requestId },
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("streamMessage: errores nunca llegan crudos al usuario", () => {
  it("un cuerpo HTML de proxy en un 502 no aparece en el mensaje", async () => {
    apiFetchMock.mockResolvedValue(
      new Response("<html><body>Bad Gateway at /var/proxy.py</body></html>", {
        status: 502,
        headers: { "x-request-id": "req-html-1" },
      }),
    );

    const error = await streamMessage("c1", "hola").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    const message = (error as Error).message;
    expect(message).not.toContain("<html");
    expect(message).not.toContain("proxy.py");
    expect(message).toContain("Ref: req-html-1");
    expect(isApiError(error) && error.status).toBe(502);
  });

  it("un detail 4xx user-safe sí pasa (misma política que api.ts)", async () => {
    apiFetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "La conversación no admite más mensajes." }), {
        status: 400,
        headers: { "content-type": "application/json", "x-request-id": "req-400-1" },
      }),
    );

    const error = await streamMessage("c1", "hola").catch((e: unknown) => e);

    expect((error as Error).message).toBe("La conversación no admite más mensajes.");
  });

  it("un detail 4xx con traceback cae al copy genérico + referencia", async () => {
    apiFetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: 'Traceback (most recent call last): File "/app/chat.py", line 3' }),
        { status: 400, headers: { "content-type": "application/json", "x-request-id": "req-tb-1" } },
      ),
    );

    const error = await streamMessage("c1", "hola").catch((e: unknown) => e);

    expect((error as Error).message).not.toContain("Traceback");
    expect((error as Error).message).not.toContain("/app/");
    expect((error as Error).message).toContain("Ref: req-tb-1");
  });

  it("el detail crudo de un evento SSE error nunca se muestra (sin contrato user-safe)", async () => {
    apiFetchMock.mockResolvedValue(
      sseResponse([
        'event: error\ndata: {"detail": "psycopg2.OperationalError: SELECT * FROM secrets", "code": "db_error"}\n\n',
      ]),
    );

    const error = await streamMessage("c1", "hola").catch((e: unknown) => e);

    const message = (error as Error).message;
    expect(message).not.toContain("psycopg2");
    expect(message).not.toContain("SELECT");
    expect(message).toContain("Ref: req-sse-1");
  });

  it("incluso un detail SSE de apariencia inocua cae al copy genérico", async () => {
    apiFetchMock.mockResolvedValue(
      sseResponse(['event: error\ndata: {"detail": "Algo salió mal."}\n\n']),
    );

    const error = await streamMessage("c1", "hola").catch((e: unknown) => e);

    expect((error as Error).message).not.toBe("Algo salió mal.");
    expect((error as Error).message).toContain("Ref: req-sse-1");
  });

  it("conserva código y estado del error SSE sin exponerlos en el mensaje", async () => {
    apiFetchMock.mockResolvedValue(
      sseResponse(['event: error\ndata: {"detail": "boom interno", "code": "rate_limited"}\n\n']),
    );

    const error = await streamMessage("c1", "hola").catch((e: unknown) => e);

    expect(isApiError(error)).toBe(true);
    if (isApiError(error)) {
      const data = (error.data ?? {}) as Record<string, unknown>;
      expect(data.code).toBe("rate_limited");
      expect(error.message).not.toContain("boom interno");
      expect(error.message).not.toContain("rate_limited");
    }
  });
});
