from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.member_directory import MemberDirectory

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/test-members")
def test_members(query: str, db: Session = Depends(get_db)):

    directory = MemberDirectory(db)

    members = directory.search(query)

    return [
        {"name": m.name, "email": m.email, "department": m.department, "role": m.role}
        for m in members
    ]
