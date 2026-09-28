import { describe, expect, it } from "vitest";

import { foldText, parseNaturalLanguage, type NlColumn, type NlFilter, type NlParseResult } from "./nl-parse";

const COLUMNS: NlColumn[] = [
  { name: "nombre", kind: "text" },
  { name: "apellido", kind: "text" },
  { name: "salario", kind: "number" },
  { name: "fecha_ingreso", kind: "temporal" },
  { name: "fecha_nacimiento", kind: "temporal" },
  { name: "departamento", kind: "text" },
  { name: "ciudad", kind: "text" },
  { name: "activo", kind: "boolean" },
  { name: "correo", kind: "text" },
  { name: "edad", kind: "number" },
  { name: "load_date", kind: "temporal" },
  { name: "meta", kind: "other" },
  { name: "tipo", kind: "text" },
];
const TODAY = new Date(2026, 8, 26);

function parse(text: string, columns: NlColumn[] = COLUMNS, latestAvailable = true): NlParseResult {
  return parseNaturalLanguage(text, columns, { today: TODAY, latestAvailable });
}

function f(column: string, op: NlFilter["op"], value = "", valueTo = ""): NlFilter {
  return { column, op, value, valueTo };
}

interface Case {
  text: string;
  filters?: NlFilter[];
  sort?: NlParseResult["sort"];
  limit?: number | null;
  latestOnly?: boolean;
  unrecognized?: Array<{ text: string; hint: RegExp }>;
  notes?: RegExp[];
}

const CORPUS: Case[] = [
  { text: "salario mayor que 20000", filters: [f("salario", "gt", "20000")] },
  { text: "SALARIO MAYOR QUE 10", filters: [f("salario", "gt", "10")] },
  { text: "salario > 1500.50", filters: [f("salario", "gt", "1500.50")] },
  { text: "salario es mayor o igual a 1000", filters: [f("salario", "gte", "1000")] },
  { text: "sueldo de al menos 3 mil", filters: [f("salario", "gte", "3000")] },
  { text: "salario menor a 1,500,000.50", filters: [f("salario", "lt", "1500000.50")] },
  { text: "salario como máximo 1.500.000", filters: [f("salario", "lte", "1500000")] },
  { text: "salario menos de 3,5", filters: [f("salario", "lt", "3.5")] },
  { text: "salario entre 1000 y 5000", filters: [f("salario", "between", "1000", "5000")] },
  {
    text: "edad entre 20 y 30 y salario menor a 5000",
    filters: [f("edad", "between", "20", "30"), f("salario", "lt", "5000")],
  },
  { text: "salario que sea mayor a 1000", filters: [f("salario", "gt", "1000")] },
  { text: "edad entre 18 e 30", filters: [f("edad", "between", "18", "30")] },
  { text: "tipo es total", filters: [f("tipo", "eq", "total")] },
  { text: "edad igual a 30 o 40", filters: [f("edad", "in", "30, 40")] },
  { text: "nombre contiene Ana", filters: [f("nombre", "contains", "Ana")] },
  { text: "nombre empieza con Jo", filters: [f("nombre", "starts_with", "Jo")] },
  { text: "nombre que empiece con J", filters: [f("nombre", "starts_with", "J")] },
  { text: 'apellido no contiene "López"', filters: [f("apellido", "not_contains", "López")] },
  { text: "departamento es Ventas o Finanzas", filters: [f("departamento", "in", "Ventas, Finanzas")] },
  { text: "departamento no es Ventas", filters: [f("departamento", "neq", "Ventas")] },
  { text: "ciudad distinta de Monterrey", filters: [f("ciudad", "neq", "Monterrey")] },
  { text: 'ciudad es "Ciudad de México"', filters: [f("ciudad", "eq", "Ciudad de México")] },
  {
    text: "ciudad es Ciudad de México",
    unrecognized: [{ text: "ciudad es Ciudad de México", hint: /incluye «Ciudad».*entre comillas/ }],
  },
  { text: "departamento es Recursos Humanos", filters: [f("departamento", "eq", "Recursos Humanos")] },
  { text: "nombre contiene de la Rosa", filters: [f("nombre", "contains", "de la Rosa")] },
  {
    text: "ciudad es Monterrey salario mayor que 5",
    unrecognized: [{ text: "ciudad es Monterrey salario mayor que 5", hint: /incluye «salario»/ }],
  },
  {
    text: "ciudad es Monterrey hasta 2020",
    unrecognized: [{ text: "ciudad es Monterrey hasta 2020", hint: /incluye «hasta»/ }],
  },
  {
    text: "ciudad es Monterrey primeros 10",
    unrecognized: [{ text: "ciudad es Monterrey primeros 10", hint: /incluye «primeros»/ }],
  },
  {
    text: "departamento es Norte excepto Centro",
    unrecognized: [{ text: "departamento es Norte excepto Centro", hint: /incluye «excepto»/ }],
  },
  {
    text: "departamento es Ventas o Norte ordenado",
    unrecognized: [{ text: "departamento es Ventas o Norte ordenado", hint: /incluye «ordenado»/ }],
  },
  { text: "ciudad es Monterrey alfabéticamente", unrecognized: [{ text: "ciudad es Monterrey alfabéticamente", hint: /incluye «alfabéticamente»/ }] },
  { text: "ciudad es Monterrey desc", unrecognized: [{ text: "ciudad es Monterrey desc", hint: /incluye «desc»/ }] },
  { text: "ciudad es Monterrey de la A a la Z", unrecognized: [{ text: "ciudad es Monterrey de la A a la Z", hint: /incluye «de la A a la Z»/ }] },
  { text: "ciudad es Monterrey más recientes primero", unrecognized: [{ text: "ciudad es Monterrey más recientes primero", hint: /incluye «más recientes primero»/ }] },
  { text: "ciudad es Monterrey hoy", unrecognized: [{ text: "ciudad es Monterrey hoy", hint: /incluye «hoy»/ }] },
  { text: "ciudad es Monterrey este mes", unrecognized: [{ text: "ciudad es Monterrey este mes", hint: /incluye «este mes»/ }] },
  { text: "ciudad es Monterrey en 2020", unrecognized: [{ text: "ciudad es Monterrey en 2020", hint: /incluye «en 2020»/ }] },
  { text: "ciudad es Monterrey en marzo de 2024", unrecognized: [{ text: "ciudad es Monterrey en marzo de 2024", hint: /incluye «en marzo»/ }] },
  { text: "ciudad es Monterrey del año pasado", unrecognized: [{ text: "ciudad es Monterrey del año pasado", hint: /incluye «año pasado»/ }] },
  { text: "ciudad es Monterrey últimos 30 días", unrecognized: [{ text: "ciudad es Monterrey últimos 30 días", hint: /incluye «últimos 30 días»/ }] },
  { text: "ciudad es Monterrey hace 3 años", unrecognized: [{ text: "ciudad es Monterrey hace 3 años", hint: /incluye «hace 3 años»/ }] },
  { text: "Mostrar Monterrey", unrecognized: [{ text: "Mostrar Monterrey", hint: /Prueba con/ }] },
  { text: "10 Monterrey", unrecognized: [{ text: "10 Monterrey", hint: /Prueba con/ }] },
  { text: "Mostrar empleados", notes: [/Se omitió «empleados»/] },
  { text: "correo contiene @gmail.com", filters: [f("correo", "contains", "@gmail.com")] },
  { text: "sin correo", filters: [f("correo", "is_empty")] },
  { text: "con correo", filters: [f("correo", "is_not_empty")] },
  { text: "correo vacío", filters: [f("correo", "is_empty")] },
  { text: "correo no está vacío", filters: [f("correo", "is_not_empty")] },
  { text: "activo es sí", filters: [f("activo", "eq", "true")] },
  { text: "activo = false", filters: [f("activo", "eq", "false")] },
  {
    text: "fecha de ingreso en 2024",
    filters: [f("fecha_ingreso", "gte", "2024-01-01"), f("fecha_ingreso", "lt", "2025-01-01")],
  },
  { text: "fecha de ingreso después de 2023", filters: [f("fecha_ingreso", "gte", "2024-01-01")] },
  { text: "fecha de ingreso antes del 15/03/2021", filters: [f("fecha_ingreso", "lt", "2021-03-15")] },
  { text: "fecha de ingreso desde 2020-01-01", filters: [f("fecha_ingreso", "gte", "2020-01-01")] },
  { text: "fecha de ingreso hasta 2020", filters: [f("fecha_ingreso", "lt", "2021-01-01")] },
  {
    text: "fecha de nacimiento entre 01/01/1990 y 31/12/1999",
    filters: [f("fecha_nacimiento", "gte", "1990-01-01"), f("fecha_nacimiento", "lt", "2000-01-01")],
  },
  {
    text: "fecha de ingreso en este mes",
    filters: [f("fecha_ingreso", "gte", "2026-09-01"), f("fecha_ingreso", "lt", "2026-10-01")],
  },
  {
    text: "fecha de ingreso del último mes",
    filters: [f("fecha_ingreso", "gte", "2026-08-01"), f("fecha_ingreso", "lt", "2026-09-01")],
    notes: [/mes calendario anterior \(agosto de 2026\)/],
  },
  {
    text: "fecha de ingreso en los últimos 30 días",
    filters: [f("fecha_ingreso", "gte", "2026-08-27"), f("fecha_ingreso", "lt", "2026-09-27")],
  },
  {
    text: "fecha de ingreso en marzo de 2025",
    filters: [f("fecha_ingreso", "gte", "2025-03-01"), f("fecha_ingreso", "lt", "2025-04-01")],
  },
  { text: "fecha de ingreso antes de hace 3 años", filters: [f("fecha_ingreso", "lt", "2023-09-26")] },
  { text: "ordenado por salario de mayor a menor", sort: [{ column: "salario", direction: "desc" }] },
  { text: "ordenar por nombre", sort: [{ column: "nombre", direction: "asc" }] },
  { text: "salario de mayor a menor", sort: [{ column: "salario", direction: "desc" }] },
  {
    text: "ordenado por salario y luego por nombre",
    sort: [
      { column: "salario", direction: "asc" },
      { column: "nombre", direction: "asc" },
    ],
  },
  { text: "los 10 mayores salarios", limit: 10, sort: [{ column: "salario", direction: "desc" }] },
  { text: "los cinco menores por edad", limit: 5, sort: [{ column: "edad", direction: "asc" }] },
  { text: "top 5 por edad", limit: 5, sort: [{ column: "edad", direction: "desc" }] },
  { text: "primeros 25", limit: 25 },
  { text: "solo 100 filas", limit: 100 },
  {
    text: "los 10 más recientes por fecha de ingreso",
    limit: 10,
    sort: [{ column: "fecha_ingreso", direction: "desc" }],
  },
  { text: "solo la carga más reciente", latestOnly: true },
  {
    text: "Mostrar los 10 empleados con mayor salario",
    limit: 10,
    sort: [{ column: "salario", direction: "desc" }],
    notes: [/Se omitió «empleados»/],
  },
  {
    text: "Mostrar empleados cuyo salario sea mayor a 1000 y ordenados por fecha de ingreso descendente",
    filters: [f("salario", "gt", "1000")],
    sort: [{ column: "fecha_ingreso", direction: "desc" }],
    notes: [/Se omitió «empleados»/],
  },
  {
    text: "Mostrar empleados con salario mayor al promedio y con más de 3 años de antigüedad",
    filters: [],
    unrecognized: [
      { text: "salario mayor al promedio", hint: /promedio requiere calcularlo/ },
      { text: "más de 3 años de antigüedad", hint: /No encontré una columna para «antigüedad».*fecha_ingreso antes de hace 3 años/ },
    ],
    notes: [/Se omitió «empleados»/],
  },
  {
    text: "salario mayor que la mediana",
    unrecognized: [{ text: "salario mayor que la mediana", hint: /«mediana» requiere calcularlo/ }],
  },
  {
    text: "salario mayor a 20,000",
    unrecognized: [{ text: "salario mayor a 20,000", hint: /ambiguo.*20000/ }],
  },
  { text: "edad mayor a 20%", unrecognized: [{ text: "edad mayor a 20%", hint: /No sé si «20%»/ }] },
  {
    text: "fecha mayor a 2020",
    unrecognized: [{ text: "fecha mayor a 2020", hint: /coincide con varias columnas: fecha_ingreso, fecha_nacimiento, load_date/ }],
  },
  { text: "nombre mayor que 5", unrecognized: [{ text: "nombre mayor que 5", hint: /no aplica a nombre \(texto\)/ }] },
  { text: "salario contiene 5", unrecognized: [{ text: "salario contiene 5", hint: /no aplica a salario \(número\)/ }] },
  { text: "meta es x", unrecognized: [{ text: "meta es x", hint: /no aplica a meta \(estructura\)/ }] },
  { text: "color es rojo", unrecognized: [{ text: "color es rojo", hint: /No encontré una columna para «color»/ }] },
  {
    text: "salario mayor que 1000 pesos mensuales",
    unrecognized: [{ text: "salario mayor que 1000 pesos mensuales", hint: /Sobra «mensuales»/ }],
  },
  {
    text: "los 10 más recientes",
    unrecognized: [{ text: "los 10 más recientes", hint: /Columnas de fecha: fecha_ingreso, fecha_nacimiento, load_date/ }],
  },
  { text: "salario entre 5000 y 1000", unrecognized: [{ text: "salario entre 5000 y 1000", hint: /menor o igual/ }] },
  { text: "fecha de ingreso en enero", unrecognized: [{ text: "fecha de ingreso en enero", hint: /Indica el año/ }] },
  { text: "empleados activos", unrecognized: [{ text: "empleados activos", hint: /Prueba con/ }] },
  { text: "activo", unrecognized: [{ text: "activo", hint: /activo es sí/ }] },
  { text: "nombre", unrecognized: [{ text: "nombre", hint: /selector «Columnas»/ }] },
  { text: "empleados que ganen más de 1000", unrecognized: [{ text: "ganen más de 1000", hint: /No encontré una columna para «ganen»/ }], notes: [/empleados/] },
  { text: "ciudad en Lima", unrecognized: [{ text: "ciudad en Lima", hint: /No entendí la condición sobre ciudad/ }] },
  {
    text: "departamento no es Ventas o Finanzas",
    unrecognized: [{ text: "departamento no es Ventas o Finanzas", hint: /Excluir varios valores/ }],
  },
  { text: "con", unrecognized: [{ text: "con", hint: /Falta la columna/ }] },
  {
    text: "ordenado por salario, ordenado por salario",
    sort: [{ column: "salario", direction: "asc" }],
    unrecognized: [{ text: "ordenado por salario", hint: /ya está en el orden/ }],
  },
];

describe("parseNaturalLanguage corpus", () => {
  it.each(CORPUS)("$text", (item) => {
    const result = parse(item.text);
    expect(result.filters).toEqual(item.filters ?? []);
    expect(result.sort).toEqual(item.sort ?? []);
    expect(result.limit).toBe(item.limit ?? null);
    expect(result.latestOnly).toBe(item.latestOnly ?? false);
    expect(result.unrecognized.map((entry) => entry.text)).toEqual((item.unrecognized ?? []).map((entry) => entry.text));
    (item.unrecognized ?? []).forEach((expected, position) => {
      expect(result.unrecognized[position].hint).toMatch(expected.hint);
    });
    for (const note of item.notes ?? []) expect(result.notes.join(" | ")).toMatch(note);
  });
});

describe("parseNaturalLanguage guarantees", () => {
  it("returns an empty result for blank text", () => {
    expect(parse("   ")).toEqual({ filters: [], sort: [], limit: null, latestOnly: false, unrecognized: [], notes: [] });
  });

  it("reports row limits instead of applying them when the caller cannot honor them", () => {
    const result = parseNaturalLanguage("los 10 empleados con mayor salario y primeros 5", COLUMNS, { today: TODAY, allowLimit: false });
    expect(result.limit).toBeNull();
    expect(result.sort).toEqual([{ column: "salario", direction: "desc" }]);
    expect(result.unrecognized.map((entry) => entry.text)).toEqual(["los 10 empleados", "primeros 5"]);
    expect(result.unrecognized[0].hint).toMatch(/no aplica al guardar un conjunto de datos/);
    const top = parseNaturalLanguage("los 10 mayores salarios", COLUMNS, { today: TODAY, allowLimit: false });
    expect(top.sort).toEqual([]);
    expect(top.unrecognized[0].text).toBe("los 10 mayores salarios");
  });

  it("only treats date words as conflicts when the source has date columns", () => {
    const textOnly: NlColumn[] = [{ name: "ciudad", kind: "text" }];
    expect(parse("ciudad es Monterrey hoy", textOnly).filters).toEqual([f("ciudad", "eq", "Monterrey hoy")]);
    expect(parse("ciudad es Monterrey desc", textOnly).unrecognized[0].hint).toMatch(/incluye «desc»/);
  });

  it("reports latest-load requests when the source cannot honor them", () => {
    const result = parse("solo la carga más reciente", COLUMNS, false);
    expect(result.latestOnly).toBe(false);
    expect(result.unrecognized[0].hint).toMatch(/fuentes bronze/);
  });

  it("keeps injection-looking text as plain values and never emits SQL", () => {
    const result = parse('nombre es "x\' OR 1=1 --"');
    expect(result.filters).toEqual([f("nombre", "eq", "x' OR 1=1 --")]);
    expect(JSON.stringify(result)).not.toMatch(/select|where/i);
  });

  it("never drops a clause silently", () => {
    const text = "salario mayor a 10, color azul; edad menor a 5";
    const result = parse(text);
    expect(result.filters).toEqual([f("salario", "gt", "10"), f("edad", "lt", "5")]);
    expect(result.unrecognized.map((entry) => entry.text)).toEqual(["color azul"]);
  });

  it("maps English and camelCase column names through the dictionary", () => {
    const columns: NlColumn[] = [
      { name: "hire_date", kind: "temporal" },
      { name: "salary", kind: "number" },
      { name: "lastModifiedDateTime", kind: "temporal" },
      { name: "custom", kind: "text", labels: ["Unidad de negocio"] },
    ];
    expect(parse("sueldo mayor a 10", columns).filters).toEqual([f("salary", "gt", "10")]);
    expect(parse("fecha de ingreso en 2024", columns).filters).toEqual([
      f("hire_date", "gte", "2024-01-01"),
      f("hire_date", "lt", "2025-01-01"),
    ]);
    expect(parse("fecha de modificación después de 2025", columns).filters).toEqual([
      f("lastModifiedDateTime", "gte", "2026-01-01"),
    ]);
    expect(parse("unidad de negocio es Norte", columns).filters).toEqual([f("custom", "eq", "Norte")]);
  });

  it("matches a quoted column name exactly", () => {
    expect(parse('"load_date" desde 2026-09-01').filters).toEqual([f("load_date", "gte", "2026-09-01")]);
  });

  it("folds accents and case", () => {
    expect(foldText("  Árbol ÑANDÚ ")).toBe("arbol nandu");
  });
});
