import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger("trustbridge.notifications")


class NotificationService:
    """
    Manages in-app notifications and email notifications for TrustBridge.
    Includes automated event dispatchers for milestone submissions, verifications,
    fund releases, dispute filings, and negotiation agreements.
    """

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
    ) -> bool:
        """
        Email delivery interface with console/log driver.
        Does not require external email credentials in development/testing.

        PRODUCTION INTEGRATION GUIDE:
        -----------------------------
        To connect SendGrid:
            1. Install: pip install sendgrid
            2. Set: SENDGRID_API_KEY=SG.xxx in .env
            3. Implementation:
                from sendgrid import SendGridAPIClient
                from sendgrid.helpers.mail import Mail
                message = Mail(
                    from_email='no-reply@trustbridge.network',
                    to_emails=to_email,
                    subject=subject,
                    plain_text_content=body_text,
                    html_content=body_html,
                )
                sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
                response = sg.send(message)
                return response.status_code == 202

        To connect AWS SES:
            1. Install: pip install boto3
            2. Set AWS credentials / IAM role with ses:SendEmail
            3. Implementation:
                import boto3
                client = boto3.client('ses', region_name=os.getenv('AWS_REGION', 'us-east-1'))
                client.send_email(
                    Source='no-reply@trustbridge.network',
                    Destination={'ToAddresses': [to_email]},
                    Message={
                        'Subject': {'Data': subject},
                        'Body': {'Text': {'Data': body_text}},
                    },
                )
                return True
        """
        logger.info(
            f"[EMAIL NOTIFICATION DRIVER: CONSOLE] To: {to_email} | Subject: '{subject}' | Body: {body_text}"
        )
        return True

    def create_notification(
        self,
        db: Session,
        user_id: str,
        notif_type: str,
        title: str,
        body: str,
    ) -> Notification:
        """Creates an in-app notification in the database."""
        notif = Notification(
            user_id=user_id,
            type=notif_type,
            title=title,
            body=body,
            read=False,
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)
        return notif

    def mark_as_read(
        self,
        db: Session,
        notification_id: str,
        user_id: str,
    ) -> Optional[Notification]:
        """Marks a notification as read if it belongs to the given user."""
        notif = (
            db.query(Notification)
            .filter(Notification.id == notification_id, Notification.user_id == user_id)
            .first()
        )
        if notif:
            notif.read = True
            db.commit()
            db.refresh(notif)
        return notif

    def mark_all_as_read(self, db: Session, user_id: str) -> int:
        """Marks all unread notifications for a user as read."""
        count = (
            db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.read == False)  # noqa: E712
            .update({"read": True})
        )
        db.commit()
        return count

    def get_user_notifications(
        self,
        db: Session,
        user_id: str,
        unread_only: bool = False,
        limit: int = 50,
    ) -> List[Notification]:
        """Fetches notifications for a user."""
        query = db.query(Notification).filter(Notification.user_id == user_id)
        if unread_only:
            query = query.filter(Notification.read == False)  # noqa: E712
        return query.order_by(Notification.created_at.desc()).limit(limit).all()

    def get_unread_count(self, db: Session, user_id: str) -> int:
        """Returns the number of unread notifications for a user."""
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.read == False)  # noqa: E712
            .count()
        )

    # --------------------------------------------------------------------------
    # Event-Driven Notification Triggers
    # --------------------------------------------------------------------------

    def notify_milestone_submitted(self, db: Session, contract, milestone):
        """Triggered when freelancer submits milestone deliverable."""
        # Find client user by client_id or client_email
        client_user = None
        if contract.client_id:
            client_user = db.query(User).filter(User.id == contract.client_id).first()
        if not client_user and contract.client_email:
            client_user = db.query(User).filter(User.email == contract.client_email).first()

        title = "Milestone Submitted"
        body = f"Milestone '{milestone.title}' on contract '{contract.title}' has been submitted for verification."

        if client_user:
            self.create_notification(db, client_user.id, "MILESTONE_SUBMITTED", title, body)

        if contract.client_email:
            self.send_email(contract.client_email, f"[TrustBridge] {title}", body)

    def notify_milestone_verified(self, db: Session, contract, milestone):
        """Triggered when milestone passes verification criteria."""
        dev_user = None
        if contract.freelancer_id:
            dev_user = db.query(User).filter(User.id == contract.freelancer_id).first()
        if not dev_user and contract.developer_email:
            dev_user = db.query(User).filter(User.email == contract.developer_email).first()

        score_str = f" (Score: {milestone.verification_score:.2f})" if milestone.verification_score else ""
        title = "Milestone Verified"
        body = f"Milestone '{milestone.title}' on contract '{contract.title}' passed verification{score_str} and is ready for release."

        if dev_user:
            self.create_notification(db, dev_user.id, "MILESTONE_VERIFIED", title, body)

        if contract.developer_email:
            self.send_email(contract.developer_email, f"[TrustBridge] {title}", body)

    def notify_funds_released(self, db: Session, contract, milestone):
        """Triggered when escrow funds are released to freelancer."""
        dev_user = None
        if contract.freelancer_id:
            dev_user = db.query(User).filter(User.id == contract.freelancer_id).first()
        if not dev_user and contract.developer_email:
            dev_user = db.query(User).filter(User.email == contract.developer_email).first()

        payout_info = f"${milestone.amount:.2f} {contract.base_currency}"
        if milestone.payout_amount:
            payout_info += f" ({milestone.payout_amount:.2f} {contract.payout_currency})"

        title = "Funds Released"
        body = f"Funds of {payout_info} have been released for milestone '{milestone.title}' on contract '{contract.title}'."

        if dev_user:
            self.create_notification(db, dev_user.id, "FUNDS_RELEASED", title, body)

        if contract.developer_email:
            self.send_email(contract.developer_email, f"[TrustBridge] {title}", body)

    def notify_dispute_opened(self, db: Session, contract, milestone, dispute):
        """Triggered when a dispute is opened on a milestone."""
        title = "Dispute Opened"
        body = f"Dispute opened on milestone '{milestone.title}' by {dispute.initiator_role}: {dispute.reason}."

        # Notify both parties if they exist
        for email, user_id in [(contract.client_email, contract.client_id), (contract.developer_email, contract.freelancer_id)]:
            target_user = None
            if user_id:
                target_user = db.query(User).filter(User.id == user_id).first()
            if not target_user and email:
                target_user = db.query(User).filter(User.email == email).first()

            if target_user:
                self.create_notification(db, target_user.id, "DISPUTE_OPENED", title, body)
            if email:
                self.send_email(email, f"[TrustBridge] {title}", body)

    def notify_negotiation_completed(self, db: Session, contract):
        """Triggered when agent-to-agent negotiation completes and agreement is formed."""
        title = "Negotiation Agreement Reached"
        body = f"Terms agreed for contract '{contract.title}'. Escrow total: ${contract.total_amount:.2f} {contract.base_currency}."

        for email, user_id in [(contract.client_email, contract.client_id), (contract.developer_email, contract.freelancer_id)]:
            target_user = None
            if user_id:
                target_user = db.query(User).filter(User.id == user_id).first()
            if not target_user and email:
                target_user = db.query(User).filter(User.email == email).first()

            if target_user:
                self.create_notification(db, target_user.id, "NEGOTIATION_COMPLETED", title, body)
            if email:
                self.send_email(email, f"[TrustBridge] {title}", body)


notification_service = NotificationService()
