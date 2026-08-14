from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime

from app.db.base import Base


class GraphSubscription(Base):
    """
    Tracks a Microsoft Graph change-notification subscription (webhook) that
    watches the shared claims mailbox for new messages.

    One row per active subscription. Graph subscriptions on the messages
    resource expire after at most ~4230 minutes (~2.9 days), so a scheduled
    job renews `expires_at` before it lapses (see
    app.services.graph_subscription_service.renew_expiring_subscriptions).
    """

    __tablename__ = "graph_subscription"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Graph-issued subscription id (GUID) — used for PATCH/DELETE calls.
    subscription_id = Column(String(100), nullable=False, unique=True, index=True)

    # e.g. "/users/{mailbox}/mailFolders('Inbox')/messages"
    resource = Column(String(500), nullable=False)
    change_type = Column(String(100), nullable=False, default="created")
    notification_url = Column(String(500), nullable=False)

    # Random secret we generated and Graph echoes back on every notification.
    client_state = Column(String(255), nullable=False)

    expires_at = Column(DateTime, nullable=False, index=True)

    status = Column(
        String(30),
        nullable=False,
        default="active",
        comment="active | expired | deleted",
    )
    last_renewed_at = Column(DateTime, nullable=True)
    last_error = Column(String(1000), nullable=True)

    # Breadcrumb updated by the webhook itself on every notification received
    # — lets you confirm "did a notification actually arrive?" via the DB /
    # the admin status endpoint, without needing to tail application logs.
    last_notification_at = Column(DateTime, nullable=True)
    last_notification_message_id = Column(String(500), nullable=True)
    notification_count = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<GraphSubscription(id={self.id}, subscription_id={self.subscription_id!r}, "
            f"status={self.status!r}, expires_at={self.expires_at})>"
        )
