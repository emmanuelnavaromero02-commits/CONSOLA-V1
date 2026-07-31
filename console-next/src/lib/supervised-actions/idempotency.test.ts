import { describe, expect, it } from "vitest";

import { createIntentKeyRegistry } from "./idempotency";

describe("createIntentKeyRegistry", () => {
  it("conserva exactamente la misma clave entre reintentos de la misma intención", () => {
    const registry = createIntentKeyRegistry();

    const first = registry.keyFor("validate:act-1");
    const retry = registry.keyFor("validate:act-1");
    const thirdTry = registry.keyFor("validate:act-1");

    expect(retry).toBe(first);
    expect(thirdTry).toBe(first);
  });

  it("rota la clave solo tras completar la intención con éxito", () => {
    const registry = createIntentKeyRegistry();

    const first = registry.keyFor("validate:act-1");
    registry.complete("validate:act-1");
    const next = registry.keyFor("validate:act-1");

    expect(next).not.toBe(first);
  });

  it("descarta la clave ante una cancelación explícita de la intención", () => {
    const registry = createIntentKeyRegistry();

    const first = registry.keyFor("cancel:act-9");
    registry.discard("cancel:act-9");

    expect(registry.keyFor("cancel:act-9")).not.toBe(first);
  });

  it("intenciones lógicas distintas nunca comparten clave", () => {
    const registry = createIntentKeyRegistry();

    const validate = registry.keyFor("validate:act-1");
    const reject = registry.keyFor("reject:act-1");
    const otherAction = registry.keyFor("validate:act-2");

    expect(new Set([validate, reject, otherAction]).size).toBe(3);
  });

  it("genera claves con formato UUID por defecto", () => {
    const registry = createIntentKeyRegistry();

    expect(registry.keyFor("validate:act-1")).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });

  it("registros independientes (p. ej. remount) no reutilizan claves previas", () => {
    const first = createIntentKeyRegistry();
    const key = first.keyFor("validate:act-1");
    first.complete("validate:act-1");

    const remounted = createIntentKeyRegistry();

    expect(remounted.keyFor("validate:act-1")).not.toBe(key);
  });
});
