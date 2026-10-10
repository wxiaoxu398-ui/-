import json
from fastapi import APIRouter, Depends
from sqlalchemy import delete
from sqlmodel import Session, select
from ..db import get_session
from ..models import Conversation, Message, Feedback
from ..errors import fail

router = APIRouter(prefix='/api/conversations', tags=['conversations'])


@router.get('')
def conversations(session: Session = Depends(get_session)):
    items = session.exec(select(Conversation).order_by(Conversation.updated_at.desc())).all()
    return {'items': [item.model_dump() for item in items], 'total': len(items)}


@router.get('/{conversation_id}/messages')
def messages(conversation_id: int, session: Session = Depends(get_session)):
    if not session.get(Conversation, conversation_id):
        fail(404, 'CONVERSATION_NOT_FOUND', '会话不存在。')
    rows = session.exec(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()
    return {'conversation_id': conversation_id, 'items': [dict(row.model_dump(exclude={'sources_json', 'metrics_json'}), sources=json.loads(row.sources_json), metrics=json.loads(row.metrics_json)) for row in rows]}


@router.delete('/{conversation_id}')
def delete_conversation(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        fail(404, 'CONVERSATION_NOT_FOUND', '会话不存在。')
    rows = session.exec(select(Message).where(Message.conversation_id == conversation_id)).all()
    if any(m.status == 'generating' for m in rows):
        fail(409, 'CHAT_BUSY', '请先停止生成，再删除会话。')
    ids = [m.id for m in rows]
    if ids:
        session.exec(delete(Feedback).where(Feedback.message_id.in_(ids)))
    session.exec(delete(Message).where(Message.conversation_id == conversation_id))
    session.delete(conversation)
    session.commit()
    return {'success': True}
