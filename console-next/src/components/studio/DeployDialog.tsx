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
      toast.success(`Automatización ${id} publicada en Airflow.`);
      return;
    case "managed":
      toast.info(result.message || `La fuente de datos instala y gestiona la automatización ${id}.`);
      return;
    case "needs_input":
      toast.warning(result.message || "Faltan datos para publicar la automatización.");
      return;
    case "failed":
      toast.error(`No se pudo publicar ${id}: ${result.error || "Airflow rechazó la automatización."}`);
      return;
    default:
      toast.error(`Respuesta inesperada al publicar (${result.status || "sin estado"}).`);
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
      title={renaming ? "Confirmar renombrado de la automatización" : "Confirmar publicación en Airflow"}
      description={
        renaming
          ? "Se publicará una copia con el nuevo dag_id y, si Airflow la acepta, se eliminará la automatización anterior."
          : "El código se escribirá en Airflow para esta fuente de datos. Revisa los datos antes de continuar."
      }
      confirmLabel={renaming ? "Renombrar" : "Publicar"}
      pendingLabel={renaming ? "Renombrando…" : "Publicando…"}
      pending={pending}
      onConfirm={onConfirm}
      onCancel={onCancel}
      testId="deploy-dialog"
    >
      {request ? (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Fuente de datos</dt>
          <dd className="break-all font-mono">{request.cartridge}</dd>
          {renaming ? (
            <>
              <dt className="text-muted-foreground">Automatización actual</dt>
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
