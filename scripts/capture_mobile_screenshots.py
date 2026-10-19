#!/usr/bin/env python3
"""
Mobile Screenshot Automation Script for TrustBridge (375px viewport audit).
Captures 375px mobile screenshots of:
1. Landing Page (docs/screenshots/mobile/landing_375px.png)
2. Dashboard (docs/screenshots/mobile/dashboard_375px.png)
3. Contract Detail (docs/screenshots/mobile/contract_detail_375px.png)
4. AI Negotiator (docs/screenshots/mobile/negotiator_375px.png)
"""

import os
import sys
import time
import subprocess
import signal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, init_db
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import AuditLog
from app.models.user import User
from app.models.notification import Notification
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, VerificationType, ActorRole
from app.services.auth_service import hash_password


def seed_demo_data():
    init_db()
    db = SessionLocal()

    # 1. Seed demo user
    client_user = db.query(User).filter(User.email == "sarah.client@trustbridge.network").first()
    if not client_user:
        client_user = User(
            id="usr_demo_client",
            email="sarah.client@trustbridge.network",
            hashed_password=hash_password("Password123!"),
            role="client",
            display_name="Sarah Vance",
        )
        db.add(client_user)

    dev_user = db.query(User).filter(User.email == "rahul.dev@trustbridge.network").first()
    if not dev_user:
        dev_user = User(
            id="usr_demo_dev",
            email="rahul.dev@trustbridge.network",
            hashed_password=hash_password("Password123!"),
            role="freelancer",
            display_name="Rahul Sharma",
        )
        db.add(dev_user)
    db.commit()

    # 2. Seed demo contract
    contract_id = "ct_mobile_demo_1"
    existing_c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not existing_c:
        contract = Contract(
            id=contract_id,
            title="Enterprise Payment Integration",
            brief="Full PayPal sandbox dual-custody escrow with automated FX risk mitigation and milestone verification.",
            client_id=client_user.id,
            freelancer_id=dev_user.id,
            client_name="Sarah Vance",
            client_email="sarah.client@trustbridge.network",
            client_country="US",
            developer_name="Rahul Sharma",
            developer_email="rahul.dev@trustbridge.network",
            developer_country="IN",
            base_currency="USD",
            payout_currency="INR",
            total_amount=3500.0,
            fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
            locked_fx_rate=84.50,
            status=ContractStatus.ACTIVE,
            paypal_order_id="ORDER-SANDBOX-DEMO-991",
            paypal_capture_id="CAP-SANDBOX-DEMO-991",
        )
        db.add(contract)

        m1 = Milestone(
            id="ms_demo_01",
            contract_id=contract_id,
            order_index=1,
            title="Phase 1: DB Schema & Auth",
            description="PostgreSQL schema with JWT authentication and bcrypt password hashing.",
            amount=1000.0,
            payout_amount=84500.0,
            status=MilestoneStatus.VERIFIED,
            verification_type=VerificationType.CODE_DELIVERY,
            acceptance_criteria=[
                {"description": "Unit tests pass with 100% coverage", "verification_method": "test_suite"},
                {"description": "JWT tokens with expiration validation", "verification_method": "automated_diff"},
            ],
            evidence_url="https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/1",
            evidence_notes="All 35 pytest suites pass. Bcrypt rounds set to 12.",
            verification_score=0.98,
            verified_by="AI_ENGINE_V1",
        )
        m2 = Milestone(
            id="ms_demo_02",
            contract_id=contract_id,
            order_index=2,
            title="Phase 2: Escrow Engine & Webhooks",
            description="PayPal sandbox order capture and GitHub PR webhook delivery verification.",
            amount=1500.0,
            payout_amount=126750.0,
            status=MilestoneStatus.SUBMITTED,
            verification_type=VerificationType.GITHUB_PR,
            acceptance_criteria=[
                {"description": "GitHub PR merged into main branch", "verification_method": "github_pr"},
            ],
            evidence_url="https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/2",
            evidence_notes="PR submitted with HMAC-SHA256 signature verification.",
            verification_score=0.88,
        )
        m3 = Milestone(
            id="ms_demo_03",
            contract_id=contract_id,
            order_index=3,
            title="Phase 3: Production Deployment",
            description="Docker slim image build and health check configuration.",
            amount=1000.0,
            payout_amount=84500.0,
            status=MilestoneStatus.FUNDED,
            verification_type=VerificationType.MANUAL_REVIEW,
            acceptance_criteria=[
                {"description": "Docker container starts cleanly with postgres", "verification_method": "manual_review"},
            ],
        )
        db.add_all([m1, m2, m3])

        audit1 = AuditLog(
            contract_id=contract_id,
            actor_role=ActorRole.CLIENT,
            actor_identifier="sarah.client@trustbridge.network",
            action="CONTRACT_CREATED",
            description="Contract created and locked at 84.50 USD/INR.",
        )
        audit2 = AuditLog(
            contract_id=contract_id,
            actor_role=ActorRole.PAYPAL,
            actor_identifier="ORDER-SANDBOX-DEMO-991",
            action="ESCROW_FUNDED",
            description="Escrow vault funded via PayPal Order ORDER-SANDBOX-DEMO-991. Total held: $3,500.00 USD.",
        )
        db.add_all([audit1, audit2])

        # Demo notifications
        notif1 = Notification(
            user_id=client_user.id,
            type="MILESTONE_SUBMITTED",
            title="Milestone Submitted",
            body="Rahul Sharma submitted evidence for Phase 2: Escrow Engine & Webhooks.",
            read=False,
        )
        notif2 = Notification(
            user_id=dev_user.id,
            type="MILESTONE_VERIFIED",
            title="Milestone Verified",
            body="Phase 1: DB Schema & Auth passed verification criteria (Score: 0.98).",
            read=True,
        )
        db.add_all([notif1, notif2])

        db.commit()
    db.close()
    print("✓ Demo contract & users seeded successfully.")


def capture_screenshots():
    out_dir = os.path.abspath("docs/screenshots/mobile")
    os.makedirs(out_dir, exist_ok=True)

    from app.services.auth_service import create_access_token
    token = create_access_token({
        "sub": "usr_demo_client",
        "email": "sarah.client@trustbridge.network",
        "role": "client",
        "display_name": "Sarah Vance",
    })

    chromium_bin = "/home/sourabh-kumar04/.local/bin/chromium"

    targets = [
        ("landing_375px.png", f"http://localhost:8000/app?auth_token={token}"),
        ("dashboard_375px.png", f"http://localhost:8000/app?auth_token={token}#/dashboard"),
        ("contract_detail_375px.png", f"http://localhost:8000/app?auth_token={token}#/contracts/ct_mobile_demo_1"),
        ("negotiator_375px.png", f"http://localhost:8000/app?auth_token={token}#/negotiate"),
    ]

    for filename, url in targets:
        out_path = os.path.join(out_dir, filename)
        cmd = [
            chromium_bin,
            "--headless",
            "--disable-gpu",
            "--window-size=375,812",
            "--hide-scrollbars",
            "--virtual-time-budget=3000",
            f"--screenshot={out_path}",
            url,
        ]
        print(f"Capturing 375px mobile screenshot: {filename} from {url}...")
        subprocess.run(cmd, check=True)
        time.sleep(1)
        if os.path.exists(out_path):
            print(f"  ✓ Saved: {out_path} ({os.path.getsize(out_path):,} bytes)")
        else:
            print(f"  ❌ Failed to create: {out_path}")


def main():
    seed_demo_data()

    # Start FastAPI server
    print("Starting TrustBridge server on http://127.0.0.1:8000...")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for server readiness
    print("Waiting for server to become healthy...")
    import urllib.request
    for _ in range(15):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.5)

    try:
        capture_screenshots()
    finally:
        print("Stopping TrustBridge test server...")
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
