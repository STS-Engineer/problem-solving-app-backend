import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any
import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class WebhookService:
    def __init__(self):
        self.webhook_url = "https://your-app-a-url.com/api/webhooks/complaint-events"  # Configure via env
        self.webhook_secret = "your-webhook-secret"  # Configure via env
        self.max_retries = 3
        self.timeout = 5.0
    
    async def send_webhook_async(
        self, 
        event_type: str, 
        complaint_data: Dict[str, Any],
        complaint_id: int,
    ) -> bool:
        """
        Send webhook asynchronously with retry logic
        Returns True if successful, False otherwise
        """
        payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": complaint_data
        }
        
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.webhook_url,
                        json=payload,
                        headers={
                            "X-Webhook-Secret": self.webhook_secret,
                            "Content-Type": "application/json"
                        },
                        timeout=self.timeout
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"Webhook sent successfully: {event_type} for complaint {complaint_id}")
                        return True
                    else:
                        logger.warning(
                            f"Webhook failed with status {response.status_code}: "
                            f"{event_type} for complaint {complaint_id} (attempt {attempt + 1}/{self.max_retries})"
                        )
                        
            except Exception as e:
                logger.error(
                    f"Webhook error: {event_type} for complaint {complaint_id} "
                    f"(attempt {attempt + 1}/{self.max_retries}): {str(e)}"
                )
            
            # Exponential backoff: 1s, 2s, 4s
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        
        return False
    
    def send_webhook_background(
        self,
        event_type: str,
        complaint_data: Dict[str, Any],
        complaint_id: int,
        db: Session
    ):
        """
        Trigger webhook send in background (fire and forget)
        Updates webhook tracking fields in database
        """
        async def _send_and_update():
            from app.models.complaint import Complaint
            
            success = await self.send_webhook_async(event_type, complaint_data, complaint_id)
            
            # Update webhook tracking
            complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
            if complaint:
                complaint.webhook_sent = success
                complaint.webhook_attempts += 1
                complaint.last_webhook_attempt = datetime.now(timezone.utc)
                db.commit()
        
        # Run in background (don't block the response)
        asyncio.create_task(_send_and_update())


webhook_service = WebhookService()