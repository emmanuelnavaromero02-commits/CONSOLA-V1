export const state = {
  S3_BUCKET: 'lakehouse',
  AIRFLOW_PUBLIC_URL: '',
  SUPERSET_PUBLIC_URL: '',

  currentStep: 0,
  aiHistory: [],
  aiBusy: false,
  _allDatasets: [],
  _activeLayer: 'bronze',
  _bronzeSources: [],   // [{source, cartridge, entity, partitions}]
  _bronzeQuery: '',     // last query text
  _currentCartridge: null,   // full manifest object
  _cartridges: [],     // list from /studio/cartridges

  STEP_LABELS: {
    1: 'RESUMEN',
    2: 'DAGS',
    3: 'ENTIDADES',
    4: 'REFINAR',
    5: 'ANALYTICS',
    6: 'IA SEMÁNTICA',
    7: 'RAG',
  },

  STEP_HINTS: {
    1: 'Paso 1 — Resumen: puedo crear un blueprint completo en dry-run desde frase, sample, OpenAPI u OData; también puedo auditar el cartucho activo antes de escribir nada.',
    2: 'Paso 2 — DAGs: puedo introspectar la fuente, generar un DAG validado con schema real, revisar el DAG abierto y preparar despliegue con aprobación.',
    3: 'Paso 3 — Entidades: puedo descubrir entidades con tipos, primary keys y watermarks; también puedo detectar entidades incompletas, extraction smoke y gaps de configuración.',
    4: 'Paso 4 — Refinar: puedo generar SQL Silver/Gold por capa, previsualizarlo, revisar lineage y señalar datasets sin materializar o SQL frágil.',
    5: 'Paso 5 — Analytics: puedo convertir Gold en KPIs, Superset datasets o apps internas; primero leo lo existente y luego propongo cambios publicables.',
    6: 'Paso 6 — IA Semántica: puedo enriquecer glosario, relaciones y términos de negocio; después sincronizo a RAG para que el lenguaje natural use la versión nueva.',
    7: 'Paso 7 — RAG: puedo auditar fuentes, ingerir documentos, buscar evidencia y detectar huecos de conocimiento antes de responder sobre el cartucho.',
  },

  STEP_QUICK_ACTIONS: {
    1: [
      { key: 'autopilot_sample', label: 'Sample → cartucho' },
      { key: 'autopilot_openapi', label: 'OpenAPI/OData' },
      { key: 'self_check', label: 'Auditar cartucho' },
    ],
    2: [
      { key: 'dag_introspect_generate', label: 'Schema → DAG' },
      { key: 'dag_review_current', label: 'Revisar DAG' },
      { key: 'dag_smoke_plan', label: 'Smoke Airflow' },
    ],
    3: [
      { key: 'entities_discover', label: 'Descubrir entidades' },
      { key: 'entities_watermarks', label: 'PK + watermark' },
      { key: 'entities_extract_smoke', label: 'Smoke extracción' },
    ],
    4: [
      { key: 'refine_silver', label: 'Crear Silver' },
      { key: 'refine_gold', label: 'Crear Gold' },
      { key: 'refine_lineage', label: 'Validar lineage' },
    ],
    5: [
      { key: 'analytics_kpis', label: 'KPIs desde Gold' },
      { key: 'analytics_app', label: 'Publicar app' },
      { key: 'analytics_superset', label: 'Superset' },
    ],
    6: [
      { key: 'semantic_enrich', label: 'Enriquecer glosario' },
      { key: 'semantic_relationships', label: 'Relaciones' },
      { key: 'semantic_sync_rag', label: 'Sync a RAG' },
    ],
    7: [
      { key: 'rag_audit', label: 'Auditar fuentes' },
      { key: 'rag_ingest', label: 'Ingerir doc' },
      { key: 'rag_search', label: 'Buscar evidencia' },
    ],
  },

  _runsByEntity: {},
  _extractingEntities: {},
  _newEntityRequested: false,

  _selectedDS: null,    // dataset object currently in editor
  _dsEditorDirty: false,

  _catData: null,       // {datasets:{}, relationships:[]}
  _catTab: 'cols',      // 'cols' | 'rels'
  _catFilter: { layer: '', cartridge: '', search: '' },
  _catEditing: null,    // {dataset, column_name} being edited inline

  _CHAT_TTL_MS: 8 * 60 * 60 * 1000,  // 8 hours

  _selectedDag: null,
  _dagsCache: [],
  _deployedCode: '',
  _tplOpen: false,

  _dagGraphVisible: true,

  _dagResizing: false,
  _dagResizeX0: 0,
  _dagResizeW0: 0,

  _vResizing: false,
  _vResizeY0: 0,
  _vResizeH0: 0,
  _vResizeEl: null,
  _vResizeHandleEl: null,

  _aiResizing: false,
  _aiResizeX0: 0,
  _aiResizeW0: 0,

  _sqlRunnerSources: [],
};
