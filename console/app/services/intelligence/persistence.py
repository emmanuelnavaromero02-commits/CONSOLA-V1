from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth, permissions
from app.services.intelligence.utils import (
    TERMINAL_SIGNAL_STATUSES,
    SIGNAL_KIND,
    coerce_json_metadata,
    json_dumps,
    num,
    public_json,
    workspace_scope,
)
from app.services.intelligence.history import (
    link_outcome_to_snapshot,
    persist_decision_intelligence_snapshot,
)


def _actor_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return None


def _can_read_workspace_wide(user: dict) -> bool:
    role = permissions.user_role(user)
    scoped = permissions.workspace_role(user)
    return role in {"admin", "owner", "super_admin"} or scoped in {
        "workspace_admin",
        "tenant_admin",
    }


def _owner_user_id(user: dict) -> int | None:
    return _actor_id(user.get("id"))


@asynccontextmanager
async def scoped_db(pool: Any, tenant_id: str | None, workspace_id: str):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id or "",
                workspace_id,
            )
            yield conn


async def persist_artifacts(
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
    artifacts: list[dict[str, Any]],
    *,
    intelligence_run_id: int | None = None,
    run_ref: str | None = None,
) -> None:
    owner_user_id = _owner_user_id(user)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        for artifact in artifacts:
            signal = artifact["signal"]
            baseline = artifact["baseline"]
            evidence_pack = artifact["evidence_pack"]
            hypotheses = artifact["hypotheses"]
            options = artifact["options"]
            baseline_id = await persist_baseline(
                conn, tenant_id, workspace_id, signal, baseline
            )
            decision_intelligence = artifact.get("decision_intelligence")
            if isinstance(decision_intelligence, dict):
                signal["decision_intelligence"] = decision_intelligence
            if intelligence_run_id is not None:
                signal["intelligence_run_id"] = intelligence_run_id
            if run_ref:
                signal["run_ref"] = run_ref
            signal["baseline_id"] = baseline_id
            pack_id = await persist_evidence(
                conn, tenant_id, workspace_id, signal, evidence_pack, owner_user_id
            )
            evidence_pack["id"] = pack_id
            signal["evidence_pack_id"] = pack_id
            for hypothesis in hypotheses:
                hypothesis["evidence_pack_id"] = pack_id
            await persist_signal(conn, tenant_id, workspace_id, signal, owner_user_id)
            await persist_hypotheses(
                conn, tenant_id, workspace_id, signal, hypotheses, owner_user_id
            )
            await persist_options(
                conn, tenant_id, workspace_id, signal, options, owner_user_id
            )
            await publish_control_room_item(
                conn,
                tenant_id,
                workspace_id,
                user,
                {
                    **artifact,
                    "signal": signal,
                    "evidence_pack": evidence_pack,
                    "hypotheses": hypotheses,
                    "options": options,
                },
            )
            await persist_decision_intelligence_snapshot(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user=user,
                artifact={
                    **artifact,
                    "signal": signal,
                    "evidence_pack": evidence_pack,
                    "hypotheses": hypotheses,
                    "options": options,
                },
                intelligence_run_id=intelligence_run_id,
                run_ref=run_ref,
            )


async def persist_baseline(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    baseline: dict[str, Any],
) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO metric_baselines (
            tenant_id, workspace_id, cartridge_id, dataset, metric, entity_kind,
            entity_id, entity_label, period_key, method, actual_value,
            expected_value, sample_count, window_days, confidence, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16::jsonb)
        ON CONFLICT (workspace_id, cartridge_id, dataset, metric, entity_id, period_key, method)
        DO UPDATE SET
            actual_value = EXCLUDED.actual_value,
            expected_value = EXCLUDED.expected_value,
            sample_count = EXCLUDED.sample_count,
            window_days = EXCLUDED.window_days,
            confidence = EXCLUDED.confidence,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        signal["cartridge_id"],
        signal["dataset"],
        signal["metric"],
        signal["entity_kind"],
        signal["entity_id"],
        signal["entity_label"],
        signal["period_key"],
        baseline["method"],
        signal["actual_value"],
        signal["expected_value"],
        baseline["sample_count"],
        baseline["window"],
        baseline["confidence"],
        json_dumps(baseline),
    )
    return int(row["id"])


async def persist_signal(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    owner_user_id: int | None,
) -> None:
    await pool.execute(
        """
        INSERT INTO intelligence_signals (
            signal_id, tenant_id, workspace_id, cartridge_id, dataset, domain,
            entity_kind, entity_id, entity_label, metric, period_key,
            actual_value, expected_value, deviation_value, deviation_pct,
            severity, signal_type, status, baseline_id, confidence, summary,
            prediction_horizon_days, predicted_value, prediction_method, signal_subtype,
            owner_user_id, metadata
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11,
            $12, $13, $14, $15,
            $16, $17, 'open', $18, $19, $20,
            $21, $22, $23, $24,
            $25, $26::jsonb
        )
        ON CONFLICT (workspace_id, signal_id) DO UPDATE
        SET actual_value = EXCLUDED.actual_value,
            expected_value = EXCLUDED.expected_value,
            deviation_value = EXCLUDED.deviation_value,
            deviation_pct = EXCLUDED.deviation_pct,
            severity = EXCLUDED.severity,
            signal_type = EXCLUDED.signal_type,
            baseline_id = EXCLUDED.baseline_id,
            confidence = EXCLUDED.confidence,
            summary = EXCLUDED.summary,
            prediction_horizon_days = EXCLUDED.prediction_horizon_days,
            predicted_value = EXCLUDED.predicted_value,
            prediction_method = EXCLUDED.prediction_method,
            signal_subtype = EXCLUDED.signal_subtype,
            owner_user_id = COALESCE(intelligence_signals.owner_user_id, EXCLUDED.owner_user_id),
            metadata = EXCLUDED.metadata,
            updated_at = NOW(),
            status = CASE
                WHEN intelligence_signals.status = ANY($27::text[])
                THEN intelligence_signals.status
                ELSE 'open'
            END
        """,
        signal["signal_id"],
        tenant_id,
        workspace_id,
        signal["cartridge_id"],
        signal["dataset"],
        signal["domain"],
        signal["entity_kind"],
        signal["entity_id"],
        signal["entity_label"],
        signal["metric"],
        signal["period_key"],
        signal["actual_value"],
        signal["expected_value"],
        signal["deviation_value"],
        signal["deviation_pct"],
        signal["severity"],
        signal["signal_type"],
        signal.get("baseline_id"),
        signal["confidence"],
        signal["summary"],
        signal.get("prediction_horizon_days"),
        signal.get("predicted_value"),
        signal.get("prediction_method"),
        signal.get("signal_subtype") or "observed",
        owner_user_id,
        json_dumps(
            {
                "metric_name": signal.get("metric_name"),
                "expected_behavior": signal.get("expected_behavior"),
                "signal_subtype": signal.get("signal_subtype") or "observed",
                "source_system": signal.get("source_system")
                or signal.get("cartridge_id"),
                "source_dataset": signal.get("source_dataset") or signal.get("dataset"),
                "dataset": signal.get("dataset"),
                "gold_table": signal.get("gold_table")
                or f"gold_{signal.get('dataset')}",
                "freshness_at": signal.get("freshness_at") or signal.get("period_key"),
                "freshness_field": signal.get("freshness_field"),
                "evidence_pack_id": signal.get("evidence_pack_id"),
                "decision_intelligence": signal.get("decision_intelligence")
                if isinstance(signal.get("decision_intelligence"), dict)
                else None,
                "intelligence_run_id": signal.get("intelligence_run_id"),
                "run_ref": signal.get("run_ref"),
            }
        ),
        sorted(TERMINAL_SIGNAL_STATUSES),
    )


async def persist_evidence(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    pack: dict[str, Any],
    owner_user_id: int | None,
) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO evidence_packs (tenant_id, workspace_id, signal_id, summary, confidence, owner_user_id, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        signal["signal_id"],
        pack["summary"],
        pack["confidence"],
        owner_user_id,
        json_dumps(
            {
                "metric": signal["metric"],
                "dataset": signal["dataset"],
                "signal_subtype": signal.get("signal_subtype") or "observed",
                "source_system": signal.get("source_system")
                or signal.get("cartridge_id"),
                "source_dataset": signal.get("source_dataset") or signal.get("dataset"),
                "gold_table": signal.get("gold_table")
                or f"gold_{signal.get('dataset')}",
                "freshness_at": signal.get("freshness_at") or signal.get("period_key"),
                "freshness_field": signal.get("freshness_field"),
                "decision_intelligence_method": (
                    signal.get("decision_intelligence") or {}
                ).get("method")
                if isinstance(signal.get("decision_intelligence"), dict)
                else None,
                "intelligence_run_id": signal.get("intelligence_run_id"),
                "run_ref": signal.get("run_ref"),
            }
        ),
    )
    pack_id = int(row["id"])
    for item in pack.get("items", []):
        await pool.execute(
            """
            INSERT INTO evidence_items (
                tenant_id, workspace_id, evidence_pack_id, source_type,
                source_ref, query_text, data, supports_hypothesis, strength,
                owner_user_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11::jsonb)
            """,
            tenant_id,
            workspace_id,
            pack_id,
            item.get("source_type") or "dataset",
            item.get("source_ref") or signal["dataset"],
            item.get("query_text"),
            json_dumps(item.get("data") or {}),
            item.get("supports_hypothesis"),
            item.get("strength") or pack["confidence"],
            owner_user_id,
            json_dumps(item.get("metadata") or {}),
        )
    return pack_id


async def persist_hypotheses(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    owner_user_id: int | None,
) -> None:
    for hypothesis in hypotheses:
        await pool.execute(
            """
            INSERT INTO hypotheses (
                tenant_id, workspace_id, signal_id, hypothesis_key, title,
                rationale, confidence, evidence_pack_id, owner_user_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (workspace_id, signal_id, hypothesis_key) DO UPDATE
            SET title = EXCLUDED.title,
                rationale = EXCLUDED.rationale,
                confidence = EXCLUDED.confidence,
                evidence_pack_id = EXCLUDED.evidence_pack_id,
                owner_user_id = COALESCE(hypotheses.owner_user_id, EXCLUDED.owner_user_id),
                metadata = EXCLUDED.metadata
            """,
            tenant_id,
            workspace_id,
            signal["signal_id"],
            hypothesis["hypothesis_key"],
            hypothesis["title"],
            hypothesis["rationale"],
            hypothesis["confidence"],
            hypothesis.get("evidence_pack_id"),
            owner_user_id,
            json_dumps(hypothesis.get("metadata") or {}),
        )


async def persist_options(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    options: list[dict[str, Any]],
    owner_user_id: int | None,
) -> None:
    for option in options:
        await pool.execute(
            """
            INSERT INTO decision_options (
                tenant_id, workspace_id, signal_id, option_id, label, action_kind,
                impact_expected, confidence, cost, risk, time_cost, score, selected, owner_user_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, FALSE, $13, $14::jsonb)
            ON CONFLICT (workspace_id, signal_id, option_id) DO UPDATE
            SET label = EXCLUDED.label,
                action_kind = EXCLUDED.action_kind,
                impact_expected = EXCLUDED.impact_expected,
                confidence = EXCLUDED.confidence,
                cost = EXCLUDED.cost,
                risk = EXCLUDED.risk,
                time_cost = EXCLUDED.time_cost,
                score = EXCLUDED.score,
                owner_user_id = COALESCE(decision_options.owner_user_id, EXCLUDED.owner_user_id),
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            tenant_id,
            workspace_id,
            signal["signal_id"],
            option["option_id"],
            option["label"],
            option["action_kind"],
            option["impact_expected"],
            option["confidence"],
            option["cost"],
            option["risk"],
            option["time_cost"],
            option["score"],
            owner_user_id,
            json_dumps({"score_explanation": option.get("score_explanation")}),
        )


async def publish_control_room_item(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
    artifact: dict[str, Any],
) -> None:
    owner_user_id = _owner_user_id(user)
    signal = artifact["signal"]
    top_hypothesis = (artifact.get("hypotheses") or [{}])[0]
    top_option = (artifact.get("options") or [{}])[0]
    evidence_pack = (
        artifact.get("evidence_pack")
        if isinstance(artifact.get("evidence_pack"), dict)
        else {}
    )
    evidence_items = (
        evidence_pack.get("items")
        if isinstance(evidence_pack.get("items"), list)
        else []
    )
    evidence_pack_id = evidence_pack.get("id") or signal.get("evidence_pack_id")
    source_system = signal.get("source_system") or signal.get("cartridge_id")
    source_dataset = signal.get("source_dataset") or signal.get("dataset")
    gold_table = signal.get("gold_table") or f"gold_{source_dataset}"
    freshness_at = signal.get("freshness_at") or signal.get("period_key")
    freshness_field = signal.get("freshness_field")
    decision_intelligence = signal.get("decision_intelligence")
    if not isinstance(decision_intelligence, dict):
        decision_intelligence = (
            artifact.get("decision_intelligence")
            if isinstance(artifact.get("decision_intelligence"), dict)
            else {}
        )
    expected_impact = (
        decision_intelligence.get("expected_impact")
        if isinstance(decision_intelligence.get("expected_impact"), dict)
        else {}
    )
    time_series = (
        decision_intelligence.get("time_series")
        if isinstance(decision_intelligence.get("time_series"), dict)
        else {}
    )
    time_series_residual = (
        time_series.get("residual")
        if isinstance(time_series.get("residual"), dict)
        else {}
    )
    time_series_seasonality = (
        time_series.get("seasonality")
        if isinstance(time_series.get("seasonality"), dict)
        else {}
    )
    impact_estimate = num(expected_impact.get("value"))
    if impact_estimate is None:
        impact_estimate = abs(float(signal["deviation_value"]))
    impact_currency = str(expected_impact.get("currency") or "USD")
    metadata = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "source_system": source_system,
        "source_dataset": source_dataset,
        "dataset": signal.get("dataset"),
        "gold_table": gold_table,
        "freshness_at": freshness_at,
        "freshness_field": freshness_field,
        "data_status": "gold_ready",
        "evidence_pack_id": evidence_pack_id,
        "intelligence_run_id": signal.get("intelligence_run_id"),
        "run_ref": signal.get("run_ref"),
        "evidence_pack": {
            "id": evidence_pack_id,
            "summary": evidence_pack.get("summary"),
            "confidence": evidence_pack.get("confidence"),
            "items": public_json(evidence_items),
        },
        "decision_intelligence": public_json(decision_intelligence),
        "module": "Intelligence Engine",
        "description": signal["summary"],
        "recommendation": top_option.get("label")
        or "Revisar evidencia y decidir siguiente paso.",
        "root_cause": top_hypothesis.get("title") or "Desviacion contra baseline.",
        "impact": f"Desviacion {signal['deviation_value']:.2f} en {signal['metric_name']}.",
        "details": {
            "actual_value": signal["actual_value"],
            "expected_value": signal["expected_value"],
            "predicted_value": signal.get("predicted_value"),
            "prediction_horizon_days": signal.get("prediction_horizon_days"),
            "deviation_pct": signal["deviation_pct"],
            "evidence_pack_id": evidence_pack_id,
            "source_system": source_system,
            "source_dataset": source_dataset,
            "gold_table": gold_table,
            "freshness_at": freshness_at,
            "freshness_field": freshness_field,
            "source": "intelligence_engine",
            "decision_intelligence_method": decision_intelligence.get("method"),
            "time_series_method": time_series.get("method"),
            "residual_z": time_series_residual.get("robust_z"),
            "seasonality_status": time_series_seasonality.get("status"),
            "recommended_decision": decision_intelligence.get("recommended_decision"),
            "intelligence_run_id": signal.get("intelligence_run_id"),
            "run_ref": signal.get("run_ref"),
        },
        "sql": (evidence_items or [{}])[0].get("query_text"),
        "intelligence": public_json(artifact),
    }
    priority_score = int(
        max(
            0,
            min(
                100,
                round(
                    float(signal["confidence"]) * 45
                    + abs(float(signal["deviation_pct"])) * 55
                ),
            ),
        )
    )
    await pool.execute(
        """
        INSERT INTO control_room_items (
            tenant_id, workspace_id, owner_user_id, item_id, cartridge_id, domain, source_dataset,
            item_kind, title, severity, status, entity_kind, entity_id,
            entity_label, anomaly_type, metadata, impact_estimate,
            impact_currency, confidence, priority_score, selected_option_id,
            execution_status, first_seen_at, last_seen_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7,
            $8, $9, $10, 'open', $11, $12,
            $13, $14, $15::jsonb, $16,
            $17, $18, $19, NULL,
            'not_started', NOW(), NOW()
        )
        ON CONFLICT (workspace_id, item_id) DO UPDATE
        SET cartridge_id = EXCLUDED.cartridge_id,
            domain = EXCLUDED.domain,
            source_dataset = EXCLUDED.source_dataset,
            item_kind = EXCLUDED.item_kind,
            title = EXCLUDED.title,
            severity = EXCLUDED.severity,
            entity_kind = EXCLUDED.entity_kind,
            entity_id = EXCLUDED.entity_id,
            entity_label = EXCLUDED.entity_label,
            anomaly_type = EXCLUDED.anomaly_type,
            metadata = control_room_items.metadata || EXCLUDED.metadata,
            impact_estimate = EXCLUDED.impact_estimate,
            impact_currency = EXCLUDED.impact_currency,
            confidence = EXCLUDED.confidence,
            priority_score = EXCLUDED.priority_score,
            owner_user_id = COALESCE(control_room_items.owner_user_id, EXCLUDED.owner_user_id),
            last_seen_at = NOW(),
            status = CASE
                WHEN control_room_items.status = ANY($20::text[])
                THEN control_room_items.status
                ELSE 'open'
            END
        """,
        tenant_id,
        workspace_id,
        owner_user_id,
        signal["signal_id"],
        signal["cartridge_id"],
        signal["domain"],
        signal["dataset"],
        SIGNAL_KIND,
        signal["summary"],
        signal["severity"],
        signal["entity_kind"],
        signal["entity_id"],
        signal["entity_label"],
        signal["metric"],
        json_dumps(metadata),
        impact_estimate,
        impact_currency,
        signal["confidence"],
        priority_score,
        sorted(TERMINAL_SIGNAL_STATUSES),
    )
    await pool.execute(
        """
        INSERT INTO control_room_item_events (
            tenant_id, workspace_id, item_id, event_type, actor_id, actor_email, metadata
        )
        VALUES ($1, $2, $3, 'intelligence_signal_created', $4, $5, $6::jsonb)
        """,
        tenant_id,
        workspace_id,
        signal["signal_id"],
        _actor_id(user.get("id")),
        user.get("email"),
        json_dumps(
            {
                "metric": signal["metric"],
                "severity": signal["severity"],
                "source_system": source_system,
                "source_dataset": source_dataset,
                "gold_table": gold_table,
                "freshness_at": freshness_at,
                "evidence_pack_id": evidence_pack_id,
                "intelligence_run_id": signal.get("intelligence_run_id"),
                "run_ref": signal.get("run_ref"),
            }
        ),
    )


async def list_signals(user: dict, *, limit: int = 100) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    can_read_all = _can_read_workspace_wide(user)
    owner_id = _owner_user_id(user)
    params: list[Any] = [workspace_id]
    tenant_clause = ""
    if tenant_id:
        params.append(tenant_id)
        tenant_clause = f" AND tenant_id::text = ${len(params)}"
    owner_clause = ""
    if not can_read_all:
        if owner_id is None:
            return {"signals": []}
        params.append(owner_id)
        owner_clause = f" AND owner_user_id = ${len(params)}"
    params.append(max(1, min(int(limit or 100), 500)))
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT signal_id, cartridge_id, dataset, domain, entity_kind, entity_id,
                   entity_label, metric, period_key, actual_value, expected_value,
                   deviation_value, deviation_pct, severity, signal_type, status,
                   confidence, summary, prediction_horizon_days, predicted_value,
                   prediction_method, signal_subtype, metadata, created_at, updated_at
              FROM intelligence_signals
             WHERE workspace_id = $1
             {tenant_clause}
             {owner_clause}
             ORDER BY updated_at DESC, severity DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"signals": [row_to_signal(row) for row in rows]}


async def get_signal(user: dict, signal_id: str) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    can_read_all = _can_read_workspace_wide(user)
    owner_id = _owner_user_id(user)
    signal_params: list[Any] = [workspace_id, signal_id]
    signal_tenant_clause = ""
    if tenant_id:
        signal_params.append(tenant_id)
        signal_tenant_clause = f" AND tenant_id::text = ${len(signal_params)}"
    owner_clause = ""
    if not can_read_all:
        if owner_id is None:
            raise HTTPException(404, "intelligence signal not found")
        signal_params.append(owner_id)
        owner_clause = f" AND owner_user_id = ${len(signal_params)}"
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            f"""
            SELECT signal_id, cartridge_id, dataset, domain, entity_kind, entity_id,
                   entity_label, metric, period_key, actual_value, expected_value,
                   deviation_value, deviation_pct, severity, signal_type, status,
                   confidence, summary, prediction_horizon_days, predicted_value,
                   prediction_method, signal_subtype, metadata, created_at, updated_at
             FROM intelligence_signals
             WHERE workspace_id = $1
               AND signal_id = $2
               {signal_tenant_clause}
               {owner_clause}
            """,
            *signal_params,
        )
        if not row:
            raise HTTPException(404, "intelligence signal not found")
        evidence_pack = await _latest_evidence_pack(conn, workspace_id, signal_id, user)
        child_params: list[Any] = [workspace_id, signal_id]
        child_owner_clause = ""
        if not can_read_all:
            child_params.append(owner_id)
            child_owner_clause = f" AND owner_user_id = ${len(child_params)}"
        hypotheses = await conn.fetch(
            f"""
            SELECT hypothesis_key, title, rationale, confidence, evidence_pack_id, metadata, created_at
              FROM hypotheses
             WHERE workspace_id = $1
               AND signal_id = $2
               {child_owner_clause}
             ORDER BY confidence DESC
            """,
            *child_params,
        )
        options = await conn.fetch(
            f"""
            SELECT option_id, label, action_kind, impact_expected, confidence, cost,
                   risk, time_cost, score, selected, metadata, created_at, updated_at
              FROM decision_options
             WHERE workspace_id = $1
               AND signal_id = $2
               {child_owner_clause}
             ORDER BY score DESC
            """,
            *child_params,
        )
        outcomes = await conn.fetch(
            f"""
            SELECT id, option_id, action_taken, predicted_value, actual_value,
                   prediction_error, outcome_summary, learned_rule, metadata, created_at
              FROM prediction_outcomes
             WHERE workspace_id = $1
               AND signal_id = $2
               {child_owner_clause}
             ORDER BY created_at DESC
             LIMIT 5
            """,
            *child_params,
        )
    return {
        "signal": row_to_signal(row),
        "evidence_pack": evidence_pack,
        "hypotheses": [public_json(dict(item)) for item in hypotheses],
        "options": [public_json(dict(item)) for item in options],
        "outcomes": [public_json(dict(item)) for item in outcomes],
    }


async def select_option(user: dict, signal_id: str, option_id: str) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    can_read_all = _can_read_workspace_wide(user)
    owner_id = _owner_user_id(user)
    params: list[Any] = [workspace_id, signal_id, option_id]
    owner_clause = ""
    if not can_read_all:
        if owner_id is None:
            raise HTTPException(404, "decision option not found")
        params.append(owner_id)
        owner_clause = f" AND owner_user_id = ${len(params)}"
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        option = await conn.fetchrow(
            f"""
            SELECT option_id, label, score
              FROM decision_options
             WHERE workspace_id = $1
               AND signal_id = $2
               AND option_id = $3
               {owner_clause}
            """,
            *params,
        )
        if not option:
            raise HTTPException(404, "decision option not found")
        update_params: list[Any] = [workspace_id, signal_id]
        update_owner_clause = ""
        if not can_read_all:
            update_params.append(owner_id)
            update_owner_clause = f" AND owner_user_id = ${len(update_params)}"
        await conn.execute(
            f"UPDATE decision_options SET selected = FALSE WHERE workspace_id = $1 AND signal_id = $2{update_owner_clause}",
            *update_params,
        )
        await conn.execute(
            f"""
            UPDATE decision_options
               SET selected = TRUE, updated_at = NOW()
             WHERE workspace_id = $1
               AND signal_id = $2
               AND option_id = $3
               {owner_clause}
            """,
            *params,
        )
        item_params: list[Any] = [
            workspace_id,
            signal_id,
            option_id,
            json_dumps({"selected_option_id": option_id}),
        ]
        item_owner_clause = ""
        if not can_read_all:
            item_params.append(owner_id)
            item_owner_clause = f" AND owner_user_id = ${len(item_params)}"
        await conn.execute(
            f"""
            UPDATE control_room_items
               SET selected_option_id = $3,
                   status = CASE WHEN status = 'open' THEN 'in_review' ELSE status END,
                   metadata = metadata || $4::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
               {item_owner_clause}
            """,
            *item_params,
        )
    await audit_service.record_event(
        user.get("id"),
        user.get("email"),
        "intelligence.option.select",
        "intelligence_signal",
        signal_id,
        metadata={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "option_id": option_id,
        },
    )
    return {"selected": public_json(dict(option))}


async def record_outcome(
    user: dict, signal_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    can_read_all = _can_read_workspace_wide(user)
    owner_id = _owner_user_id(user)
    signal_params: list[Any] = [workspace_id, signal_id]
    owner_clause = ""
    if not can_read_all:
        if owner_id is None:
            raise HTTPException(404, "intelligence signal not found")
        signal_params.append(owner_id)
        owner_clause = f" AND owner_user_id = ${len(signal_params)}"
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        signal = await conn.fetchrow(
            f"""
            SELECT actual_value, expected_value, predicted_value, metric, cartridge_id
              FROM intelligence_signals
             WHERE workspace_id = $1
               AND signal_id = $2
               {owner_clause}
            """,
            *signal_params,
        )
        if not signal:
            raise HTTPException(404, "intelligence signal not found")
        signal_data = dict(signal)
        option_id = str(body.get("option_id") or "").strip() or None
        action_taken = str(body.get("action_taken") or body.get("action") or "").strip()
        if not action_taken:
            raise HTTPException(400, "action_taken is required")
        actual_value = num(body.get("actual_value"))
        predicted_value = num(body.get("predicted_value"))
        if predicted_value is None:
            predicted_value = num(signal_data.get("predicted_value")) or num(
                signal_data.get("actual_value")
            )
        prediction_error = None
        if actual_value is not None and predicted_value is not None:
            prediction_error = round(actual_value - predicted_value, 4)
        learned_rule = str(body.get("learned_rule") or "").strip() or None
        if not learned_rule and prediction_error is not None:
            learned_rule = f"Resultado medido con error {prediction_error:.2f} para {signal_data['metric']}."
        outcome_summary = str(
            body.get("outcome_summary")
            or body.get("summary")
            or learned_rule
            or "Outcome registrado."
        ).strip()
        row = await conn.fetchrow(
            """
            INSERT INTO prediction_outcomes (
                tenant_id, workspace_id, signal_id, option_id, action_taken,
                predicted_value, actual_value, prediction_error, outcome_summary,
                learned_rule, owner_user_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
            RETURNING *
            """,
            tenant_id,
            workspace_id,
            signal_id,
            option_id,
            action_taken,
            predicted_value,
            actual_value,
            prediction_error,
            outcome_summary,
            learned_rule,
            owner_id,
            json_dumps({"reported_by": user.get("email")}),
        )
        await link_outcome_to_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            signal_id=signal_id,
            outcome_row=row,
            body=body,
        )
        if learned_rule:
            await conn.execute(
                """
                INSERT INTO control_room_lessons (
                    tenant_id, workspace_id, item_id, cartridge_id, anomaly_type, rule, confidence, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, 0.70, $7::jsonb)
                """,
                tenant_id,
                workspace_id,
                signal_id,
                signal_data["cartridge_id"],
                signal_data["metric"],
                learned_rule,
                json_dumps({"source": "prediction_outcome"}),
            )
        item_params: list[Any] = [
            workspace_id,
            signal_id,
            json_dumps(
                {
                    "intelligence_outcome": public_json(dict(row)),
                    "lessons": [learned_rule] if learned_rule else [],
                }
            ),
        ]
        item_owner_clause = ""
        if not can_read_all:
            item_params.append(owner_id)
            item_owner_clause = f" AND owner_user_id = ${len(item_params)}"
        await conn.execute(
            f"""
            UPDATE control_room_items
               SET metadata = metadata || $3::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
               {item_owner_clause}
            """,
            *item_params,
        )
    await audit_service.record_event(
        user.get("id"),
        user.get("email"),
        "intelligence.outcome.record",
        "intelligence_signal",
        signal_id,
        metadata={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "option_id": option_id,
        },
    )
    return {"outcome": public_json(dict(row))}


async def _latest_evidence_pack(
    pool: Any, workspace_id: str, signal_id: str, user: dict
) -> dict[str, Any] | None:
    can_read_all = _can_read_workspace_wide(user)
    owner_id = _owner_user_id(user)
    params: list[Any] = [workspace_id, signal_id]
    owner_clause = ""
    if not can_read_all:
        if owner_id is None:
            return None
        params.append(owner_id)
        owner_clause = f" AND owner_user_id = ${len(params)}"
    packs = await pool.fetch(
        f"""
        SELECT id, summary, confidence, metadata, created_at
          FROM evidence_packs
         WHERE workspace_id = $1
           AND signal_id = $2
           {owner_clause}
         ORDER BY created_at DESC
         LIMIT 1
        """,
        *params,
    )
    if not packs:
        return None
    pack = dict(packs[0])
    item_params: list[Any] = [workspace_id, pack["id"]]
    item_owner_clause = ""
    if not can_read_all:
        item_params.append(owner_id)
        item_owner_clause = f" AND owner_user_id = ${len(item_params)}"
    items = await pool.fetch(
        f"""
        SELECT id, source_type, source_ref, query_text, data, supports_hypothesis,
               strength, metadata, created_at
          FROM evidence_items
         WHERE workspace_id = $1
           AND evidence_pack_id = $2
           {item_owner_clause}
         ORDER BY strength DESC, id
        """,
        *item_params,
    )
    return {**public_json(pack), "items": [public_json(dict(item)) for item in items]}


def row_to_signal(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["metadata"] = coerce_json_metadata(data.get("metadata"))
    decision_intelligence = data["metadata"].get("decision_intelligence")
    if not isinstance(decision_intelligence, dict):
        intelligence = data["metadata"].get("intelligence")
        if isinstance(intelligence, dict):
            decision_intelligence = intelligence.get("decision_intelligence")
    if isinstance(decision_intelligence, dict):
        data["decision_intelligence"] = decision_intelligence
    return public_json(data)
