# -*- coding: utf-8 -*-
"""Mixin: batch run operations."""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from src.storage.models import BatchRun, BatchSchedule


class BatchMixin:
    """Mixin providing batch run CRUD operations."""

    def get_batch_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.session_scope() as session:
            rows = session.query(BatchRun).order_by(desc(BatchRun.started_at)).limit(limit).all()
            return [self._batch_run_to_dict(r) for r in rows]

    def get_batch_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.session_scope() as session:
            row = session.query(BatchRun).filter_by(run_id=run_id).first()
            if row is None:
                return None
            return self._batch_run_to_dict(row)

    def get_batch_run_report_path(self, run_id: str) -> Optional[str]:
        with self.session_scope() as session:
            row = session.query(BatchRun).filter_by(run_id=run_id).first()
            if row is None:
                return None
            return row.report_path

    def get_incomplete_batch_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self.session_scope() as session:
            rows = (
                session.query(BatchRun)
                .filter(BatchRun.completed_at.is_(None))
                .filter(BatchRun.status == "running")
                .order_by(desc(BatchRun.started_at))
                .limit(limit)
                .all()
            )
            return [self._batch_run_to_dict(r) for r in rows]

    def update_batch_run_status(self, run_id: str, status: str) -> bool:
        with self.session_scope() as session:
            row = session.query(BatchRun).filter_by(run_id=run_id).first()
            if row is None:
                return False
            row.status = status
            return True

    def update_batch_run_report_path(self, run_id: str, report_path: str) -> bool:
        with self.session_scope() as session:
            row = session.query(BatchRun).filter_by(run_id=run_id).first()
            if row is None:
                return False
            row.report_path = report_path
            return True

    def delete_batch_run(self, run_id: str) -> bool:
        with self.session_scope() as session:
            row = session.query(BatchRun).filter_by(run_id=run_id).first()
            if row is None:
                return False
            session.delete(row)
            return True

    # ============ batch_schedules ============

    def get_batch_schedule(self) -> Optional[Dict[str, Any]]:
        with self.session_scope() as session:
            row = session.query(BatchSchedule).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "enabled": row.enabled,
                "times": json.loads(row.times_json or "[]"),
                "template_id": row.template_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

    def save_batch_schedule(self, enabled: bool, times: List[str], template_id: str) -> Dict[str, Any]:
        with self.session_scope() as session:
            row = session.query(BatchSchedule).first()
            if row is None:
                row = BatchSchedule()
                session.add(row)
            row.enabled = enabled
            row.times_json = json.dumps(times, ensure_ascii=False)
            row.template_id = template_id
            row.updated_at = datetime.now()
            session.commit()
            return {
                "id": row.id,
                "enabled": row.enabled,
                "times": times,
                "template_id": row.template_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

    @staticmethod
    def _batch_run_to_dict(row: BatchRun) -> Dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "triggered_by": row.triggered_by,
            "template_id": row.template_id,
            "template_name": row.template_name,
            "stock_count": row.stock_count,
            "success_count": row.success_count,
            "fail_count": row.fail_count,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "report_path": row.report_path,
            "results_json": row.results_json,
            "stock_codes_json": row.stock_codes_json,
            "status": row.status,
        }
