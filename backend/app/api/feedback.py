from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from ..db import get_session
from ..models import Message, Feedback
from ..schemas import FeedbackRequest
from ..errors import fail

router = APIRouter(prefix='/api/feedback', tags=['feedback'])


@router.post('')
def feedback(payload: FeedbackRequest, session: Session = Depends(get_session)):
    message = session.get(Message, payload.message_id)
    if not message or message.role != 'assistant':
        fail(404, 'MESSAGE_NOT_FOUND', '回答不存在。')
    row = session.exec(select(Feedback).where(Feedback.message_id == message.id)).first()
    if row is None:
        row = Feedback(message_id=message.id, rating=payload.rating)
    row.rating, row.comment = payload.rating, payload.comment
    message.feedback = payload.rating
    session.add(row)
    session.add(message)
    session.commit()
    return {'success': True}
