const TOOL_LABELS: Record<string, string> = {
  list_sources: "Listar tablas de origen",
  get_source_partitions: "Consultar cargas disponibles",
  preview_source: "Vista previa de una tabla de origen",
  describe_source: "Describir una tabla de origen",
  generate_transform: "Proponer una transformación",
  preview_transform: "Vista previa de una transformación",
  save_dataset: "Guardar un conjunto de datos",
  materialize: "Actualizar un conjunto de datos",
  list_datasets: "Listar conjuntos de datos",
  list_datasets_with_schemas: "Listar conjuntos de datos con su estructura",
  get_dataset_definition: "Ver la definición de un conjunto de datos",
  get_schema: "Consultar la estructura de datos",
  query_dataset: "Consultar un conjunto de datos",
  describe_silver: "Describir datos modelados",
  discover_relationships: "Descubrir relaciones entre datos",
  validate_relationship: "Validar una relación entre datos",
  register_relationship: "Registrar una relación entre datos",
  get_data_catalog: "Consultar el catálogo de datos",
  upsert_catalog_entries: "Actualizar el catálogo de datos",
  get_lineage: "Consultar el origen de los datos",
  publish_app: "Publicar una aplicación",
  list_apps: "Listar aplicaciones",
  get_app_details: "Ver el detalle de una aplicación",
  get_app_html: "Ver el contenido de una aplicación",
  delete_app: "Eliminar una aplicación",
  delete_dataset: "Eliminar un conjunto de datos",

  search_rag: "Buscar en la base de conocimiento",
  list_rag_sources: "Listar documentos de conocimiento",
  ingest_document: "Agregar un documento de conocimiento",
  request_admin_help: "Escalar al administrador",

  agent_list: "Listar agentes",
  agent_get: "Ver un agente",
  agent_create: "Crear un agente",
  agent_update: "Modificar un agente",
  agent_delete: "Eliminar un agente",

  airflow_list_dags: "Listar automatizaciones",
  airflow_get_run_status: "Consultar el estado de una ejecución",
  airflow_get_task_logs: "Ver la bitácora de una ejecución",
  airflow_list_task_instances: "Listar pasos de una ejecución",
  airflow_list_dag_runs: "Listar ejecuciones de una automatización",
  airflow_trigger_dag: "Iniciar una automatización",
  airflow_create_dag: "Crear una automatización",
  airflow_delete_dag: "Eliminar una automatización",
  airflow_set_variable: "Cambiar una variable de automatización",
  dag_get_source: "Ver el código de una automatización",
  dag_save_source: "Guardar el código de una automatización",
  pipeline_run_save: "Registrar una ejecución",

  cartridge_get_semantic: "Consultar el vocabulario de negocio",
  cartridge_sync_semantic_to_rag: "Sincronizar el vocabulario con el conocimiento",
  cartridge_search_term: "Buscar un término de negocio",
  cartridge_get_manifest: "Ver la configuración de la fuente de datos",
  cartridge_get_hints: "Ver las guías de la fuente de datos",
  list_cartridges: "Listar fuentes de datos",
  cartridge_list_entities: "Listar tablas de la fuente de datos",
  cartridge_get_schema: "Consultar la estructura de la fuente de datos",
  cartridge_preview: "Vista previa de la fuente de datos",
  cartridge_extract: "Extraer datos de la fuente",
  cartridge_extract_all: "Extraer todas las tablas de la fuente",
  cartridge_get_run_logs: "Ver la bitácora de una extracción",
  cartridge_get_job_status: "Consultar el estado de una extracción",
  cartridge_list_jobs: "Listar extracciones",
  cartridge_list_kbs: "Listar consultas guardadas",
  cartridge_query_kb: "Leer una consulta guardada",
  cartridge_run_kb: "Ejecutar una consulta guardada",

  control_room__summary_read: "Resumen del Control Room",
  control_room__dashboard_read: "Tablero del Control Room",
  control_room__ops_summary_read: "Resumen operativo",
  control_room__alerts_read: "Consultar alertas",
  control_room__agents_ops_read: "Estado de los agentes",
  control_room__sap_successfactors_gold_kpis_read: "Indicadores de talento (SuccessFactors)",
  control_room__talent_kpis_read: "Indicadores de talento",
  control_room__talent_overview_read: "Panorama de talento",
  control_room__talent_9box_read: "Matriz de talento 9-box",
  control_room__talent_metadata_readiness_read: "Preparación de datos de talento",
  control_room__finance_kpis_read: "Indicadores financieros",
  control_room__operations_kpis_read: "Indicadores de operación",
  control_room__risk_kpis_read: "Indicadores de riesgo",
  control_room__sap_b1_kpis_read: "Indicadores de SAP Business One",
  control_room__agent_memory_read: "Leer hallazgos compartidos",
  control_room__agent_memory_write: "Registrar un hallazgo compartido",
  control_room__decision_intelligence_runs_read: "Consultar análisis de decisiones",
  control_room__raise_alert: "Levantar una alerta",
  control_room__raise_analysis_alert: "Levantar una alerta de análisis",
  calibration__bayesian_state: "Consultar la confianza de las estimaciones",
  simulation__monte_carlo_run: "Simular escenarios",
  decision__orchestrate: "Preparar una recomendación de decisión",
  wisdom_bits__run: "Ejecutar un análisis guardado",
  market_context_read: "Consultar contexto de mercado",

  postgres_list_schemas: "Listar esquemas de base de datos",
  postgres_list_tables: "Listar tablas de base de datos",
  postgres_get_table_schema: "Consultar la estructura de una tabla",
  postgres_get_sample: "Ver una muestra de una tabla",
  postgres_execute_query: "Ejecutar una consulta técnica",
  postgres_execute_ddl: "Modificar la base de datos",

  minio_list_objects: "Listar archivos del almacén",
  minio_get_parquet_schema: "Consultar la estructura de un archivo",
  minio_get_sample_rows: "Ver una muestra de un archivo",
  minio_list_cartridge_specs: "Listar especificaciones de fuentes",
  minio_read_spec: "Leer una especificación",
  minio_upload_spec: "Subir una especificación",

  superset_list_databases: "Listar bases de datos de tableros",
  superset_list_datasets: "Listar datos de tableros",
  superset_list_charts: "Listar gráficas",
  superset_list_dashboards: "Listar tableros",
  superset_create_chart: "Crear una gráfica",
  superset_create_dashboard: "Crear un tablero",
  superset_create_database: "Conectar una base de datos de tableros",
  superset_create_dataset: "Crear datos de tablero",
  superset_export_dashboard: "Exportar un tablero",
  superset_import_dashboard: "Importar un tablero",

  vault_list_connections: "Listar conexiones",
  vault_get_connection: "Ver una conexión",
  vault_set_connection: "Guardar una conexión",
  vault_delete_connection: "Eliminar una conexión",
  vault_list_secrets: "Listar credenciales guardadas",
  vault_set_secret: "Guardar una credencial",

  watermark_get: "Consultar el avance de carga",
  watermark_set: "Cambiar el avance de carga",
};

const SERVER_LABELS: Record<string, string> = {
  refinement: "Datos y catálogo",
  "mcp-infra": "Plataforma y conocimiento",
  infra: "Plataforma y conocimiento",
};

export function splitToolId(id: string): { server: string; name: string } {
  const index = id.indexOf("__");
  if (index <= 0) return { server: "", name: id };
  return { server: id.slice(0, index), name: id.slice(index + 2) };
}

export function hasToolLabel(id: string): boolean {
  return splitToolId(id).name in TOOL_LABELS;
}

export function toolLabel(id: string): string {
  const { name } = splitToolId(id);
  return TOOL_LABELS[name] ?? name.replace(/__/g, " · ").replace(/_/g, " ");
}

export function toolServerLabel(server: string): string {
  return SERVER_LABELS[server] ?? server;
}

export function toolTooltip(id: string, description?: string | null): string {
  const detail = (description ?? "").trim();
  return detail ? `${id}\n${detail}` : id;
}
