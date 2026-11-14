"""Background execution engine.

Runs inside the local backend process on an asyncio loop. It does not depend on
the dashboard or on WebSocket clients: closing the browser leaves it running.

State honesty
- `processing`      local work is happening (prompt prep, staging, checkpoints)
- `waiting_ai`      an authorized automated provider call is in flight
- `waiting_user`    a human must act (user-assisted / manual accounts)
- `waiting_provider` provider unavailable or backing off before a retry
Nothing is ever labelled as running at a provider unless a real call is in flight.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ..checkpoints.service import create_checkpoint
from ..config import get_settings
from ..conversations import handoff
from ..conversations import service as conv
from ..database import session_scope
from ..models import (
    AIAccount, ExecutionMode, MessageKind, Project, RunState, ServiceState, Task, TaskStatus, utcnow,
)
from ..models.enums import PlanStatus
from ..providers import ADAPTERS, ProviderUnavailable
from ..providers.registry import load_secret, refresh_account, resolve_mode
from ..services.activity import log_event
from ..services.events import bus
from ..services.notifications import notify
from ..services.settings_service import get_setting, set_setting
from ..tasks import execution, queue
from ..utils.logging_setup import get_logger

log = get_logger("engine")


def build_prompt(db, project: Project, task: Task) -> str:
    return handoff.context_for_task(db, project, task) + "\n\n---\n\n" + task.input_prompt


class Engine:
    def __init__(self) -> None:
        self._loop_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.state: str = ServiceState.STOPPED.value
        self.current_task_id: int | None = None
        self.last_heartbeat: datetime | None = None

    # ---- lifecycle ----------------------------------------------------------
    @property
    def loop_alive(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self, recover: bool = True) -> None:
        if self.loop_alive:
            self._set_state(ServiceState.RUNNING.value)
            return
        self._stop = asyncio.Event()
        with session_scope() as db:
            if recover:
                queue.recover_interrupted(db)
            set_setting(db, "engine.state", ServiceState.RUNNING.value)
        self._set_state(ServiceState.RUNNING.value)
        self._loop_task = asyncio.create_task(self._run(), name="project-pilot-engine")
        with session_scope() as db:
            log_event(db, "engine.started", "Background engine started")

    async def stop(self, timeout: float = 30.0, notify_user: bool = True) -> None:
        self._stop.set()
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(self._loop_task, timeout=timeout)
            except asyncio.TimeoutError:
                self._loop_task.cancel()
            except Exception:  # pragma: no cover
                pass
        self._loop_task = None
        self._set_state(ServiceState.STOPPED.value)
        with session_scope() as db:
            set_setting(db, "engine.state", ServiceState.STOPPED.value)
            log_event(db, "engine.stopped", "Background engine stopped")
            if notify_user:
                notify(db, "service_stopped", "Project Pilot engine stopped", "Background processing is off.")

    def pause(self) -> None:
        self._set_state(ServiceState.PAUSED.value)
        with session_scope() as db:
            set_setting(db, "engine.state", ServiceState.PAUSED.value)
            for p in db.scalars(select(Project).where(Project.plan_status == PlanStatus.EXECUTING.value)):
                create_checkpoint(db, p, reason="paused", label="Execution paused")
            log_event(db, "engine.paused", "Background engine paused")

    def resume(self) -> None:
        self._set_state(ServiceState.RUNNING.value)
        with session_scope() as db:
            set_setting(db, "engine.state", ServiceState.RUNNING.value)
            log_event(db, "engine.resumed", "Background engine resumed")

    def _set_state(self, state: str) -> None:
        self.state = state
        bus.publish("engine", state=state)

    # ---- main loop ------------------------------------------------------------
    async def _run(self) -> None:
        poll = get_settings().worker_poll_seconds
        while not self._stop.is_set():
            self.last_heartbeat = datetime.now(timezone.utc)
            try:
                worked = False
                if self.state == ServiceState.RUNNING.value:
                    worked = await self.run_once()
                if not worked:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=poll)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception as exc:  # never let the loop die
                log.exception("engine loop error: %s", exc)
                await asyncio.sleep(poll)

    async def run_once(self) -> bool:
        """Claim and process at most one task. Returns True if a task was processed."""
        with session_scope() as db:
            task = queue.claim_next(db)
            task_id = task.id if task else None
        if task_id is None:
            return False
        self.current_task_id = task_id
        try:
            await self._process(task_id)
        finally:
            self.current_task_id = None
        return True

    # ---- task processing --------------------------------------------------------
    async def _process(self, task_id: int) -> None:
        # Phase 1: prepare (local processing)
        with session_scope() as db:
            task = db.get(Task, task_id)
            project = db.get(Project, task.project_id)
            task.run_state = RunState.PROCESSING.value
            account = db.get(AIAccount, task.assigned_account_id) if task.assigned_account_id else None
            create_checkpoint(db, project, reason="before_task", label=f"Before: {task.title}", task=task)
            c = conv.get_or_create(db, task)
            prompt_text = build_prompt(db, project, task)
            already = any(m.kind == MessageKind.USER_PROMPT.value and m.meta.get("attempt") == task.retry_count + 1
                          for m in c.messages)
            if not already:
                conv.add_message(db, c, MessageKind.USER_PROMPT, prompt_text, account, "prepared",
                                 {"attempt": task.retry_count + 1})
            queue.publish_task(task)

            problem = self._account_problem(db, task, account)
            if problem:
                self._wait_for_provider(db, task, project, account, problem)
                return
            assert account is not None
            refresh_account(account)
            mode = resolve_mode(account)
            execution.start_execution(db, task, account, mode)
            adapter = ADAPTERS.get(account.provider.key) or ADAPTERS["other"]
            cfg = dict(account.config or {})
            secret = load_secret(account) if mode == ExecutionMode.AUTOMATED.value else None
            provider_name, account_name = account.provider.name, account.display_name

            if mode != ExecutionMode.AUTOMATED.value:
                # Honest state: a human has to do this step. Nothing is "running" anywhere.
                task.status = TaskStatus.AWAITING_USER.value
                task.run_state = RunState.WAITING_USER.value
                task.lock_token, task.locked_at = None, None
                execution.finish_execution(db, task, "awaiting_user")
                conv.add_message(
                    db, c, MessageKind.SYSTEM_EVENT,
                    f"Prompt prepared for {provider_name} / {account_name}. This account is "
                    f"{'user-assisted' if mode == 'user_assisted' else 'manual-only'}: copy the prompt, run it "
                    f"yourself on the provider's website, then paste the response (and any files) back here.",
                    account, "awaiting_user")
                notify(db, "user_action_required", "Your input is needed",
                       f"Run '{task.title}' on {provider_name} and paste the result.", project.id)
                handoff.write_handoff(db, project)
                log_event(db, "task.awaiting_user", f"Waiting for user: {task.title}", project_id=project.id,
                          task_id=task.id)
                queue.publish_task(task)
                return
            # Automated: a real, authorized call is about to be in flight.
            task.run_state = RunState.WAITING_AI.value
            queue.publish_task(task)
            log_event(db, "task.calling_provider", f"Calling {provider_name}: {task.title}",
                      project_id=project.id, task_id=task.id)

        # Phase 2: provider call (blocking, off the event loop)
        error: Exception | None = None
        result = None
        try:
            result = await asyncio.to_thread(adapter.execute, prompt_text, cfg, secret)
        except Exception as exc:  # classified below
            error = exc

        # Phase 3: record outcome
        with session_scope() as db:
            task = db.get(Task, task_id)
            if task is None or task.status == TaskStatus.CANCELLED.value:
                return
            project = db.get(Project, task.project_id)
            account = db.get(AIAccount, task.assigned_account_id) if task.assigned_account_id else None
            if error is None and result is not None:
                execution.complete_with_response(db, task, result.text, None, account, "automated")
            else:
                self._handle_failure(db, task, project, account, error)

    @staticmethod
    def _account_problem(db, task: Task, account: AIAccount | None) -> str:
        if account is None:
            return "No provider account is assigned to this task. Assign one to continue."
        if not account.enabled:
            return f"Account '{account.display_name}' is disabled."
        allowed = {a.id for a in queue.project_accounts(db, task.project_id)}
        if account.id not in allowed:
            return f"Account '{account.display_name}' is not enabled for this project."
        if account.unavailable_reason:
            return f"Account '{account.display_name}' is unavailable: {account.unavailable_reason}"
        return ""

    def _wait_for_provider(self, db, task: Task, project: Project, account: AIAccount | None, reason: str) -> None:
        task.status = TaskStatus.AWAITING_USER.value
        task.run_state = RunState.WAITING_PROVIDER.value
        task.error = reason
        task.lock_token, task.locked_at = None, None
        create_checkpoint(db, project, reason="provider_unavailable", label=f"Blocked: {task.title}", task=task)
        c = conv.get_or_create(db, task)
        conv.add_message(db, c, MessageKind.ERROR, reason, account, "waiting_provider")
        notify(db, "provider_attention", "Provider needs attention", reason, project.id)
        log_event(db, "task.waiting_provider", reason, level="warning", project_id=project.id, task_id=task.id)
        queue.publish_task(task)

    def _handle_failure(self, db, task: Task, project: Project, account: AIAccount | None,
                        error: Exception | None) -> None:
        c = conv.get_or_create(db, task)
        s = get_settings()
        max_retries = task.max_retries if task.max_retries is not None else s.default_max_retries
        base = float(get_setting(db, "backoff_base_seconds", s.default_backoff_base_seconds))
        task.lock_token, task.locked_at = None, None
        if isinstance(error, ProviderUnavailable):
            reason = error.reason
            if error.retryable and task.retry_count < max_retries:
                task.retry_count += 1
                delay = queue.backoff_delay(base, task.retry_count)
                task.next_attempt_at = utcnow() + delay
                task.status = TaskStatus.READY.value
                task.run_state = RunState.WAITING_PROVIDER.value
                task.error = reason
                execution.finish_execution(db, task, "retry_scheduled", reason)
                conv.add_message(db, c, MessageKind.ERROR,
                                 f"{reason} Retry {task.retry_count}/{max_retries} in {int(delay.total_seconds())}s.",
                                 account, "waiting_provider")
                notify(db, "recoverable_error", "Temporary provider problem", reason, project.id)
                log_event(db, "task.retry_scheduled", f"{task.title}: {reason}", level="warning",
                          project_id=project.id, task_id=task.id)
            else:
                execution.finish_execution(db, task, "provider_unavailable", reason)
                self._wait_for_provider(db, task, project, account, reason)
        else:
            reason = f"{type(error).__name__}: {error}" if error else "unknown error"
            if task.retry_count < max_retries:
                task.retry_count += 1
                delay = queue.backoff_delay(base, task.retry_count)
                task.next_attempt_at = utcnow() + delay
                task.status = TaskStatus.READY.value
                task.run_state = RunState.IDLE.value
                task.error = reason
                execution.finish_execution(db, task, "retry_scheduled", reason)
                conv.add_message(db, c, MessageKind.ERROR, f"{reason}. Retry {task.retry_count}/{max_retries} "
                                 f"in {int(delay.total_seconds())}s.", account, "retrying")
                notify(db, "recoverable_error", "Task error (will retry)", reason, project.id)
            else:
                task.status = TaskStatus.FAILED.value
                task.run_state = RunState.IDLE.value
                task.error = reason
                execution.finish_execution(db, task, "failed", reason)
                conv.add_message(db, c, MessageKind.ERROR, f"Failed after {max_retries} retries: {reason}",
                                 account, "failed")
                create_checkpoint(db, project, reason="task_failed", label=f"Failed: {task.title}", task=task)
                notify(db, "recoverable_error", "Task failed", f"{task.title}: {reason}", project.id)
                log_event(db, "task.failed", f"{task.title}: {reason}", level="error", project_id=project.id,
                          task_id=task.id)
        queue.publish_task(task)

    # ---- status ---------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        with session_scope() as db:
            counts = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
            running = [t.id for t in db.scalars(select(Task).where(Task.status == TaskStatus.RUNNING.value))]
            return {
                "state": self.state, "loop_alive": self.loop_alive,
                "heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
                "current_task_id": self.current_task_id, "running_task_ids": running,
                "task_counts": counts, "ws_clients": bus.subscriber_count,
            }


engine = Engine()
