"""
app/api/routes/plant_contacts.py

Admin CRUD for per-plant notification contacts (CQE / QM / PM / GM).
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.enums import PlantEnum
from app.schemas.plant_contact import PlantContactRead, PlantContactUpdate
from app.services.plant_contacts_service import PlantContactService

router = APIRouter()


@router.get("", response_model=List[PlantContactRead], summary="List all plant contacts")
def list_plant_contacts(db: Session = Depends(get_db)) -> List[PlantContactRead]:
    return PlantContactService.list(db)


@router.get(
    "/{plant}", response_model=PlantContactRead, summary="Get one plant's contacts"
)
def get_plant_contact(plant: PlantEnum, db: Session = Depends(get_db)) -> PlantContactRead:
    contact = PlantContactService.get(db, plant)
    if contact is None:
        raise HTTPException(status_code=404, detail=f"No contacts row for {plant.value}")
    return contact


@router.put(
    "/{plant}", response_model=PlantContactRead, summary="Update a plant's contacts"
)
def update_plant_contact(
    plant: PlantEnum,
    payload: PlantContactUpdate,
    db: Session = Depends(get_db),
) -> PlantContactRead:
    return PlantContactService.update(db, plant, payload)
