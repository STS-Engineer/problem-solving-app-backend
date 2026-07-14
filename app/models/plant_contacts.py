from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, JSON
from sqlalchemy import Enum as SQLEnum

from app.db.base import Base
from app.models.enums import PlantEnum


class PlantContact(Base):
    """
    Notification recipients per manufacturing plant.

    Used to route "new complaint intake" notifications to the right people:
    the plant's CQE(s), Quality Manager, Plant Manager (and optionally GM).

    One row per PlantEnum value. When the plant of an incoming complaint
    cannot be determined, the intake falls back to a configured triage email
    instead of this table (see INTAKE_FALLBACK_EMAIL).
    """

    __tablename__ = "plant_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plant = Column(
        SQLEnum(PlantEnum, name="plant_enum"),
        nullable=False,
        unique=True,
        index=True,
    )

    # List of CQE (== CQT) emails — a plant can have several.
    cqe_emails = Column(JSON, nullable=False, default=list, comment="List[str] of CQE/CQT emails")

    quality_manager_email = Column(String(255), nullable=True)
    plant_manager_email = Column(String(255), nullable=True)
    general_manager_email = Column(String(255), nullable=True)

    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @staticmethod
    def _dedup(emails: list[str]) -> list[str]:
        """De-dup (case-insensitive), preserve order, drop blanks."""
        seen: set[str] = set()
        out: list[str] = []
        for e in emails:
            e = (e or "").strip()
            if e and e.lower() not in seen:
                seen.add(e.lower())
                out.append(e)
        return out

    def manager_recipients(self) -> list[str]:
        """
        QM + PM (+ GM) for this plant — the managers who triage and assign a CQT.
        Used for the initial "new intake" notification. CQE(s) are NOT included
        here; the CQT is notified separately once the QM assigns them.
        """
        return self._dedup(
            [
                self.quality_manager_email,
                self.plant_manager_email,
                self.general_manager_email,
            ]
        )

    def all_recipients(self) -> list[str]:
        """Flatten every configured contact (CQE + QM + PM + GM) into a list."""
        return self._dedup(
            list(self.cqe_emails or [])
            + [
                self.quality_manager_email,
                self.plant_manager_email,
                self.general_manager_email,
            ]
        )

    def __repr__(self) -> str:
        return f"<PlantContact(plant={self.plant}, cqe={self.cqe_emails})>"
