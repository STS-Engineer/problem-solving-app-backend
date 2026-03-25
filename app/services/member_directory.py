from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.avomember import AvoMember


class MemberDirectory:

    def __init__(self, db: Session):
        self.db = db

    def search(self, query: str, limit: int = 5) -> List[AvoMember]:
        q = f"%{query.strip()}%"

        return (
            self.db.query(AvoMember)
            .filter(
                or_(
                    AvoMember.name.ilike(q),
                    AvoMember.email.ilike(q),
                    AvoMember.role.ilike(q),  # ← ajouté
                    AvoMember.department.ilike(q),  # ← ajouté
                    AvoMember.city.ilike(q),  # ← ajouté
                    AvoMember.office.ilike(q),  # ← ajouté
                    AvoMember.region.ilike(q),  # ← ajouté
                )
            )
            .limit(limit)
            .all()
        )

    def get(self, member_id: int) -> Optional[AvoMember]:
        return self.db.get(AvoMember, member_id)

    def create(self, data: dict) -> AvoMember:

        member = AvoMember(**data)

        self.db.add(member)
        self.db.flush()

        return member

    def update(self, member: AvoMember, patch: dict) -> AvoMember:

        for key, value in patch.items():
            setattr(member, key, value)

        self.db.flush()

        return member
