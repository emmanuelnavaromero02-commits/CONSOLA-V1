"use client";

import { toast } from "sonner";

import type { DeployDagInput, DeployDagResult } from "@/lib/studio/types";

import { ConfirmDialog } from "./ui";

export interface DeployRequest extends DeployDagInput {
  renameFrom?: string;
  templateName?: string;
}

export function notifyDeployResult(result: DeployDagResult, dagId: string): void {
  const id = result.dag_id || dagId;
  switch (result.status) {
    case "deployed":
      toast.success(`DAG ${id} desplegado en Airflow.`);
      return;
    case "managed":
      toast.info(result.message || `DAG ${id} empaquetado: se gestiona desde el cartucho.`);
      return;
    case "needs_input":
      toast.warning(result.message || "Faltan datos para desplegar el DAG.");
      return;
    case "failed":
      toast.error(`No se pudo desplegar ${id}: ${result.error || "Airflow rechazó el DAG."}`);
      return;
    default:
      toast.error(`Respuesta inesperada del deploy (${result.status || "sin estado"}).`);
  }
}

function lineCount(code: string | undefined): number {
  return code ? code.split("\n").length : 0;
}

export function DeployDialog({
  request,
  pending,
  onConfirm,
  onCancel,
}: {
  request: DeployRequest | null;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const renaming = Boolean(request?.renameFrom);
  return (
    <ConfirmDialog
      open={Boolean(request)}
      title={renaming ? "Confirmar renombrado del DAG" : "Confirmar deploy a Airflow"}
      description={
        renaming
          ? "Se desplegará una copia con el nuevo dag_id y, si Airflow la acepta, se eliminará el DAG anterior."
          : "El código se escribirá en Airflow para este cartucho. Revisa los datos antes de continuar."
      }
      confirmLabel={renaming ? "Renombrar" : "Desplegar"}
      pendingLabel={renaming ? "Renombrando…" : "Desplegando…"}
      pending={pending}
      onConfirm={onConfirm}
      onCancel={onCancel}
      testId="deploy-dialog"
    >
      {request ? (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Cartucho</dt>
          <dd className="break-all font-mono">{request.cartridge}</dd>
          {renaming ? (
            <>
              <dt className="text-muted-foreground">DAG actual</dt>
              <dd className="break-all font-mono">{request.renameFrom}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">{renaming ? "Nuevo dag_id" : "dag_id"}</dt>
          <dd className="break-all font-mono">{request.dag_id}</dd>
          <dt className="text-muted-foreground">Entidad</dt>
          <dd className="break-all font-mono">{request.entity}</dd>
          <dt className="text-muted-foreground">Origen</dt>
          <dd>
            {request.template_id
              ? `Plantilla ${request.templateName || request.template_id}`
              : `Código del editor (${lineCount(request.code)} líneas)`}
          </dd>
        </dl>
      ) : null}
    </ConfirmDialog>
  );
}
