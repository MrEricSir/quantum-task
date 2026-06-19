import json

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, llm_client, LLM_MODEL

router = APIRouter()

_JOB_SYSTEM = """\
You are a personal administrative assistant. Take action based on the user's instruction \
and provided context. Respond directly with the output — do not describe what you are \
about to do, just do it.

Rules:
- Match the tone and format implied by the instruction and context.
- Keep output concise and professional unless the task implies otherwise.
- If the context does not contain enough information to act, say so in one sentence.
"""


@router.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(models.Job).order_by(models.Job.updated_at.desc()).all()
    return [schemas.Job.model_validate(j) for j in jobs]


@router.post("/api/jobs")
def create_job(req: schemas.JobCreate, db: Session = Depends(get_db)):
    job = models.Job(
        title=req.title,
        prompt=req.prompt,
        input_sources=json.dumps([s.model_dump() for s in req.input_sources]),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return schemas.Job.model_validate(job)


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return schemas.Job.model_validate(job)


@router.put("/api/jobs/{job_id}")
def update_job(job_id: int, req: schemas.JobUpdate, db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if req.title is not None:
        job.title = req.title or None
    if req.prompt is not None:
        job.prompt = req.prompt
    if req.input_sources is not None:
        job.input_sources = json.dumps([s.model_dump() for s in req.input_sources])
    if req.last_output is not None:
        job.last_output = req.last_output
    if req.output_card_id is not None:
        job.output_card_id = req.output_card_id
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return schemas.Job.model_validate(job)


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete(job)
    db.commit()
    return {"ok": True}


@router.post("/api/jobs/{job_id}/run")
def run_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    sources = json.loads(job.input_sources or "[]")
    context_parts: list[str] = []
    for src in sources:
        if src.get("type") == "card" and src.get("card_id"):
            card = db.query(models.Card).filter(models.Card.id == src["card_id"]).first()
            if card:
                context_parts.append(f"### Card: {card.title}")
                if card.description:
                    context_parts.append(card.description)
        elif src.get("type") == "tag" and src.get("tag_id"):
            tag_cards = (
                db.query(models.Card)
                .join(models.card_tags, models.Card.id == models.card_tags.c.card_id)
                .filter(
                    models.card_tags.c.tag_id == src["tag_id"],
                    models.Card.archived == False,
                    models.Card.completed == False,
                )
                .all()
            )
            if tag_cards:
                tag_label = src.get("tag_name") or f"tag #{src['tag_id']}"
                context_parts.append(f'### Cards tagged "{tag_label}"')
                for card in tag_cards:
                    context_parts.append(f"**{card.title}**")
                    if card.description:
                        context_parts.append(card.description)
        elif src.get("type") == "text" and src.get("content"):
            context_parts.append("### Additional Context")
            context_parts.append(src["content"])

    user_msg = f"## Instruction\n{job.prompt}"
    if context_parts:
        user_msg += "\n\n## Context\n" + "\n\n".join(context_parts)

    accumulated: list[str] = []

    def generate():
        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _JOB_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                stream=True,
                temperature=0.3,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    accumulated.append(delta)
                    yield f"data: {json.dumps({'text': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"
        # Persist output after streaming completes
        if accumulated:
            from datetime import datetime, timezone
            with db.no_autoflush:
                j = db.query(models.Job).filter(models.Job.id == job_id).first()
                if j:
                    j.last_output = "".join(accumulated)
                    j.updated_at = datetime.now(timezone.utc)
                    db.commit()

    return StreamingResponse(generate(), media_type="text/event-stream")
