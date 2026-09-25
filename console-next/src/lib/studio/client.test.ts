import { afterEach, describe, expect, it, vi } from "vitest";

import { isApiError, toApiError } from "@/lib/api";

import {
  airflowDagUrl,
  deleteDataset,
  deployDag,
  exportFilename,
  getDagGraph,
  getLayerPreview,
  getRuntimeConfig,
  importCartridge,
  listDags,
  MAX_IMPORT_ZIP_BYTES,
  MAX_SPEC_BYTES,
  renameDag,
  replaceDagId,
  safeHttpUrl,
  saveDataset,
  streamStudioChat,
  studioErrorMessage,
  studioPaths,
  SUPERSET_INTERNAL_ONLY_COPY,
  supersetErrorMessage,
  uploadEntitySpec,
} from "./client";

type FetchCall = [string, RequestInit];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "req-studio" },
  });
}

function stubFetch(...responses: Response[]) {
  const queue = [...responses];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void input;
    void init;
    const next = queue.shift();
    if (!next) throw new Error("unexpected fetch");
    return next;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof stubFetch>, index = 0): FetchCall {
  return fetchMock.mock.calls[index] as FetchCall;
}

function sseResponse(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "x-request-id": "req-sse" } });
}

function fakeFile(size: number, name = "spec.yaml"): File {
  return { name, size, type: "application/octet-stream" } as unknown as File;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("studioPaths", () => {
  it("encodes every path segment and query parameter", () => {
    expect(studioPaths.dagGraph("sap hcm")).toBe("/api/studio/dag-graph?cartridge=sap+hcm");
    expect(studioPaths.dagSource("acme", "a/b")).toBe("/api/studio/dags/a%2Fb/source?cartridge=acme");
    expect(studioPaths.dag("acme", "acme_x")).toBe("/api/studio/dags/acme_x?cartridge=acme");
    expect(studioPaths.cartridgeStatus("../x")).toBe("/studio/cartridges/..%2Fx/status");
    expect(studioPaths.entity("acme", "Inv oice")).toBe("/studio/cartridges/acme/entities/Inv%20oice");
    expect(studioPaths.entityRename("acme", "Invoice")).toBe("/studio/cartridges/acme/entities/Invoice/rename");
    expect(studioPaths.layerPreview("gold", "acme", "gold_sales", 50)).toBe(
      "/api/studio/gold/preview?cartridge=acme&dataset=gold_sales&limit=50",
    );
    expect(studioPaths.datasetDelete("a&b")).toBe("/api/datasets?name=a%26b");
    expect(studioPaths.datasetRefresh("a b")).toBe("/datasets/a%20b/refresh");
    expect(studioPaths.entitiesUpload("acme")).toBe("/api/studio/entities/upload?cartridge=acme");
    expect(studioPaths.chatStream()).toBe("/studio/chat/stream");
  });
});

describe("error mapping", () => {
  it("explains Airflow timeouts and Superset internal-only responses", () => {
    expect(studioErrorMessage(toApiError("x", 504), "fallback")).toContain("HTTP 504");
    expect(studioErrorMessage(toApiError("x", 413, {}), "fallback")).toContain("tamaño máximo");
    expect(studioErrorMessage(new Error("detalle"), "fallback")).toBe("detalle");
    expect(studioErrorMessage("nope", "fallback")).toBe("fallback");
    expect(supersetErrorMessage(toApiError("x", 503))).toBe(SUPERSET_INTERNAL_ONLY_COPY);
  });

  it("maps a backend 504 on the DAG list to an ApiError with status", async () => {
    stubFetch(jsonResponse({ detail: "Airflow DAG list timed out" }, 504));
    const error = await listDags("acme").catch((err: unknown) => err);
    expect(isApiError(error) && error.status).toBe(504);
    expect(studioErrorMessage(error, "fallback")).toContain("Airflow no respondió");
  });

  it("passes a safe 403 detail from the deploy gate through to the user", async () => {
    stubFetch(jsonResponse({ detail: "Deploy a Airflow requiere ALLOW_RCE_TOOLS=true en el entorno local." }, 403));
    const error = await deployDag({ cartridge: "acme", entity: "Invoice", dag_id: "acme_x", code: "x" }).catch(
      (err: unknown) => err,
    );
    expect((error as Error).message).toContain("ALLOW_RCE_TOOLS");
  });
});

describe("deployDag", () => {
  it("sends template_id instead of code when a template is chosen", async () => {
    const fetchMock = stubFetch(jsonResponse({ status: "deployed", dag_id: "acme_invoice_full" }));
    const result = await deployDag({
      cartridge: "acme",
      entity: "Invoice",
      dag_id: "acme_invoice_full",
      template_id: "full_extract",
      code: "ignored",
    });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/studio/dag-deploy");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      cartridge: "acme",
      entity: "Invoice",
      dag_id: "acme_invoice_full",
      template_id: "full_extract",
    });
    expect(result.status).toBe("deployed");
  });

  it("normalises a missing status to failed", async () => {
    stubFetch(jsonResponse({}));
    const result = await deployDag({ cartridge: "acme", entity: "Invoice", dag_id: "acme_x", code: "x" });
    expect(result.status).toBe("failed");
  });

  it("renames by deploying under the new id and deleting the old one", async () => {
    const fetchMock = stubFetch(
      jsonResponse({ status: "deployed", dag_id: "acme_new" }),
      jsonResponse({ deleted: true, dag_id: "acme_old" }),
    );
    const result = await renameDag({
      cartridge: "acme",
      entity: "Invoice",
      oldId: "acme_old",
      newId: "acme_new",
      code: "with DAG(dag_id='acme_old') as dag:\n    pass",
    });
    expect(result.deleted).toBe(true);
    expect(JSON.parse(String(call(fetchMock, 0)[1].body)).code).toContain("dag_id='acme_new'");
    expect(call(fetchMock, 1)[0]).toBe("/api/studio/dags/acme_old?cartridge=acme");
    expect(call(fetchMock, 1)[1].method).toBe("DELETE");
  });

  it("does not delete the old DAG when the new deploy is not deployed", async () => {
    const fetchMock = stubFetch(jsonResponse({ status: "failed", error: "syntax error" }));
    const result = await renameDag({ cartridge: "acme", entity: "E", oldId: "acme_a", newId: "acme_b", code: "x" });
    expect(result.deleted).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rewrites every dag_id assignment", () => {
    expect(replaceDagId(`dag_id="a"\nDAG(dag_id = 'b')`, "c")).toBe(`dag_id='c'\nDAG(dag_id='c')`);
  });
});

describe("normalisation", () => {
  it("keeps only well-formed graph nodes and edges", async () => {
    stubFetch(
      jsonResponse({
        format: "svg",
        svg: "<svg><script>alert(1)</script></svg>",
        nodes: [{ id: "cartridge:acme", kind: "cartridge", label: "Acme" }, { kind: "entity" }],
        edges: [{ source: "cartridge:acme", target: "entity:X" }, { source: 1 }],
      }),
    );
    const graph = await getDagGraph("acme");
    expect(graph.nodes).toHaveLength(1);
    expect(graph.edges).toHaveLength(1);
    expect(graph).not.toHaveProperty("svg");
  });

  it("reports unavailable layer previews honestly", async () => {
    stubFetch(
      jsonResponse({ layer: "gold", columns: [], rows: [], total: 0, available: false, reason: "No gold datasets" }),
    );
    const preview = await getLayerPreview("gold", "acme", "sales");
    expect(preview.available).toBe(false);
    expect(preview.reason).toBe("No gold datasets");
  });

  it("drops non-http runtime URLs", async () => {
    stubFetch(jsonResponse({ airflow_url: "javascript:alert(1)", superset_url: "https://bi.example.test/" }));
    const config = await getRuntimeConfig();
    expect(config.airflow_url).toBeNull();
    expect(config.superset_url).toBe("https://bi.example.test");
    expect(safeHttpUrl("//evil.example")).toBeNull();
  });

  it("builds Airflow deep links only from a valid base URL", () => {
    expect(airflowDagUrl("http://airflow.test:8082/", "acme x")).toBe("http://airflow.test:8082/dags/acme%20x/grid");
    expect(airflowDagUrl("", "acme")).toBeNull();
    expect(airflowDagUrl("ftp://x", "acme")).toBeNull();
  });

  it("sanitises export file names", () => {
    expect(exportFilename('attachment; filename="acme.zip"', "acme")).toBe("acme.zip");
    expect(exportFilename('attachment; filename="../../etc/passwd"', "acme")).toBe(".._.._etc_passwd.zip");
    expect(exportFilename(null, "acme")).toBe("acme.zip");
  });
});

describe("datasets", () => {
  it("treats a 200 response without deleted=true as a failure", async () => {
    stubFetch(jsonResponse({ detail: "published datasets require a staged retirement operation" }));
    await expect(deleteDataset("gold_sales")).rejects.toThrow("staged retirement");
  });

  it("surfaces refinement errors returned with HTTP 200 on save", async () => {
    stubFetch(jsonResponse({ error: "sql is required" }));
    await expect(
      saveDataset({ name: "x", layer: "silver", sql: "", description: "", cartridge: "acme", sources: [] }),
    ).rejects.toThrow("sql is required");
  });
});

describe("uploads", () => {
  it("rejects oversized specs before any request", async () => {
    const fetchMock = stubFetch();
    await expect(uploadEntitySpec("acme", fakeFile(MAX_SPEC_BYTES + 1))).rejects.toThrow("2 MB");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects oversized ZIP imports before any request", async () => {
    const fetchMock = stubFetch();
    await expect(importCartridge(fakeFile(MAX_IMPORT_ZIP_BYTES + 1, "acme.zip"))).rejects.toThrow("25 MB");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the spec as multipart with the cartridge", async () => {
    const fetchMock = stubFetch(jsonResponse({ accepted: true, accepted_count: 2, entities: ["A", "B"], errors: [] }));
    const file = new File(["openapi: 3.0.0"], "spec.yaml");
    const result = await uploadEntitySpec("acme", file);
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/studio/entities/upload?cartridge=acme");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("cartridge")).toBe("acme");
    expect(result.accepted_count).toBe(2);
  });
});

describe("streamStudioChat", () => {
  it("posts to the Studio stream endpoint and relays tool and text events", async () => {
    const fetchMock = stubFetch(
      sseResponse([
        "event: open\ndata: {}\n\n",
        'event: tool_use\ndata: {"type":"tool_use","tool":"cartridge_get_manifest","args":{"id":"acme"}}\n\n',
        'event: tool_result\ndata: {"type":"tool_result","tool":"cartridge_get_manifest","summary":"ok"}\n\n',
        'event: text_delta\ndata: {"type":"text_delta","text":"Ho"}\n\n',
        'event: text_delta\ndata: {"type":"text_delta","text":"la"}\n\n',
        'event: done\ndata: {"type":"done","reply":"Hola","messages":[{"role":"user","content":"hi"}],"viewer_urls":[{"url":"/viewer/lineage","label":"Linaje"},{"url":"https://evil.test/x"}]}\n\n',
      ]),
    );
    const deltas: string[] = [];
    const tools: string[] = [];
    const result = await streamStudioChat(
      { message: "hi", history: [], step: 2, cartridge_id: "acme" },
      {
        onTextDelta: (text) => deltas.push(text),
        onToolUse: (tool) => tools.push(tool),
      },
    );
    const [url, init] = call(fetchMock);
    expect(url).toBe("/studio/chat/stream");
    expect(JSON.parse(String(init.body))).toEqual({ message: "hi", history: [], step: 2, cartridge_id: "acme" });
    expect(deltas.join("")).toBe("Hola");
    expect(tools).toEqual(["cartridge_get_manifest"]);
    expect(result.reply).toBe("Hola");
    expect(result.history).toHaveLength(1);
    expect(result.viewerUrls).toEqual([{ url: "/viewer/lineage", label: "Linaje" }]);
  });

  it("turns an error event into a user-safe message with the reference", async () => {
    stubFetch(
      sseResponse([
        'event: error\ndata: {"type":"error","message":"Internal server error. error_id=abcdef123456"}\n\n',
      ]),
    );
    const error = await streamStudioChat({ message: "hi", history: [], step: 1, cartridge_id: null }).catch(
      (err: unknown) => err,
    );
    expect((error as Error).message).toBe("El asistente de Studio no pudo responder. Ref: abcdef123456");
  });

  it("fails when the stream ends without a done event", async () => {
    stubFetch(sseResponse(["event: open\ndata: {}\n\n"]));
    await expect(
      streamStudioChat({ message: "hi", history: [], step: 1, cartridge_id: null }),
    ).rejects.toThrow("sin respuesta");
  });
});
