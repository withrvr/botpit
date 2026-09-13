import random
import string

from sqlmodel import Session

from app.models import Room


def create_room(db: Session, *, game: str = "tictactoe", seat_count: int = 2) -> Room:
    room = Room(
        code="".join(random.choices(string.ascii_uppercase, k=8)),
        game=game,
        seat_count=seat_count,
        seed=random.getrandbits(62),
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return room
