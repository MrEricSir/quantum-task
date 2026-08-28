"""
Shared "list the available tags" prompt-text builder. Used by every LLM
prompt that needs to offer existing tags for the model to match against
(Quick Add parsing, bulk parsing, the iOS Shortcuts endpoint, action-item
extraction) so the exact wording stays in one place instead of drifting
across independently-typed copies.
"""
from sqlalchemy.orm import Session

import models


def tags_prompt_section(db: Session) -> str:
    tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]
    return f"Available tags: {', '.join(tag_names)}" if tag_names else "No tags available."
