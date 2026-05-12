// Studio Legacy — shared mutable state.
// Container for top-level variables that used to live as `let`/`const` at the
// top of legacy.js. Centralized here so legacy.js and sql-runner.js can both
// mutate and observe the same values after the ES Module conversion of Fase 4B.

export const state = {
  // S3/MinIO bucket — viene de /api/config; default cubre dev local.
  S3_BUCKET: 'lakehouse',

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
    1: 'Paso 1 — Resumen: visión general del cartucho seleccionado. Puedo ayudarte a crear uno nuevo o importar desde un ZIP. Describe el sistema origen y te guío.',
    2: 'Paso 2 — DAGs: edita y despliega los DAGs de Airflow de este cartucho. Puedo generar un DAG completo si me describes la API o la lógica de extracción.',
    3: 'Paso 3 — Entidades: qué objetos extraer del sistema origen, en qué modo (full/incremental) y qué DAG de Airflow los procesa. Puedo generar DAGs nuevos si me describes la API.',
    4: 'Paso 4 — Refinamiento: crea datasets Silver (snapshot limpio) y Gold (agregaciones). Soy experto en DuckDB y SQL para lakehouse.',
    5: 'Paso 5 — Analytics: publica datasets Gold en herramientas de reporting. Superset, métricas de negocio, KPIs.',
    6: 'Paso 6 — IA Semántica: define el vocabulario de negocio para que el asistente entienda lenguaje natural.',
    7: 'Paso 7 — RAG: ingresa documentos a la base de conocimiento (texto o PDF). Puedo buscar semánticamente en ellos y citarlos en mis respuestas.',
  },

  _runsByEntity: {},
  _extractingEntities: {},

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

  // Used by sql-runner.js
  _sqlRunnerSources: [],
};
