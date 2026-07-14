"""
app/services/plant_contacts_service.py

CRUD for per-plant notification contacts (CQE / QM / PM / GM).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.enums import PlantEnum
from app.models.plant_contacts import PlantContact
from app.schemas.plant_contact import PlantContactUpdate

logger = logging.getLogger(__name__)


class PlantContactService:

    @staticmethod
    def list(db: Session) -> List[PlantContact]:
        return db.query(PlantContact).order_by(PlantContact.plant).all()

    @staticmethod
    def get(db: Session, plant: PlantEnum) -> Optional[PlantContact]:
        return (
            db.query(PlantContact).filter(PlantContact.plant == plant).one_or_none()
        )

    @staticmethod
    def update(
        db: Session, plant: PlantEnum, payload: PlantContactUpdate
    ) -> PlantContact:
        """
        Update the given plant's contacts. Creates the row if it does not exist
        (defensive — the migration seeds all plants, but a new enum value may
        not have a row yet).
        """
        contact = PlantContactService.get(db, plant)
        if contact is None:
            contact = PlantContact(plant=plant, cqe_emails=[])
            db.add(contact)

        data = payload.model_dump(exclude_unset=True)
        if "cqe_emails" in data and data["cqe_emails"] is not None:
            # normalise: strip, drop blanks, de-dup (case-insensitive)
            seen: set[str] = set()
            cleaned: list[str] = []
            for e in data["cqe_emails"]:
                e = (e or "").strip()
                if e and e.lower() not in seen:
                    seen.add(e.lower())
                    cleaned.append(e)
            contact.cqe_emails = cleaned

        for field in (
            "quality_manager_email",
            "plant_manager_email",
            "general_manager_email",
        ):
            if field in data:
                val = data[field]
                setattr(contact, field, (val or "").strip() or None)

        contact.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(contact)
        logger.info("plant_contacts: updated %s", plant.value)
        return contact
