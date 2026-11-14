from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AIAccount, Checkpoint, FileChange, Project, ProjectPrompt, Task


def project_or_404(project_id: int, db: Session = Depends(get_db)) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def task_or_404(task_id: int, db: Session = Depends(get_db)) -> Task:
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t


def account_or_404(account_id: int, db: Session = Depends(get_db)) -> AIAccount:
    a = db.get(AIAccount, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    return a


def prompt_or_404(prompt_id: int, db: Session = Depends(get_db)) -> ProjectPrompt:
    p = db.get(ProjectPrompt, prompt_id)
    if p is None:
        raise HTTPException(404, "prompt not found")
    return p


def checkpoint_or_404(checkpoint_id: int, db: Session = Depends(get_db)) -> Checkpoint:
    c = db.get(Checkpoint, checkpoint_id)
    if c is None:
        raise HTTPException(404, "checkpoint not found")
    return c


def change_or_404(change_id: int, db: Session = Depends(get_db)) -> FileChange:
    c = db.get(FileChange, change_id)
    if c is None:
        raise HTTPException(404, "file change not found")
    return c
