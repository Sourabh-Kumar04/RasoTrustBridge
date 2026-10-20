#!/usr/bin/env python3
"""
TrustBridge UI V4 PayPal-Inspired Theme & Declutter Screenshot Automation.
Captures Desktop (1440x900) and Mobile (375x812) screenshots:
1. Landing Page (docs/screenshots/v4/01_landing_1440px.png & 01_landing_375px.png)
2. Dashboard / Portfolio (docs/screenshots/v4/02_dashboard_1440px.png & 02_dashboard_375px.png)
3. Deal Formation & AI Negotiator (docs/screenshots/v4/03_negotiator_1440px.png & 03_negotiator_375px.png)
4. Contract Dossier & Apple Wallet Ledger (docs/screenshots/v4/04_contract_dossier_1440px.png & 04_contract_dossier_375px.png)
5. Contract Builder (docs/screenshots/v4/05_contract_builder_1440px.png & 05_contract_builder_375px.png)
"""

import os
import sys
import time
import json
import urllib.request
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, init_db
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import AuditLog
from app.models.user import User
from app.models.notification import Notification
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, VerificationType, ActorRole
from app.services.auth_service import hash_password, create_access_token


CONTRACT_ID = "ct_8492017a"


def seed_v4_data():
    init_db()
    db = SessionLocal()

    # 1. Clean out legacy demo records if any exist
    db.query(Milestone).filter(Milestone.contract_id.in_(["ct_mobile_demo_1", CONTRACT_ID])).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.contract_id.in_(["ct_mobile_demo_1", CONTRACT_ID])).delete(synchronize_session=False)
    db.query(Contract).filter(Contract.id.in_(["ct_mobile_demo_1", CONTRACT_ID])).delete(synchronize_session=False)
    db.commit()

    # 2. Seed realistic users
    client_user = db.query(User).filter(User.email == "david.sterling@sterlingfintech.com").first()
    if not client_user:
        client_user = User(
            id="usr_client_891",
            email="david.sterling@sterlingfintech.com",
            hashed_password=hash_password("Password123!"),
            role="client",
            display_name="David Sterling",
            trust_score=98.5,
            completed_deals=16,
            disputed_deals=0,
            disputes_lost=0,
        )
        db.add(client_user)

    dev_user = db.query(User).filter(User.email == "arjun.mehta@apexdev.io").first()
    if not dev_user:
        dev_user = User(
            id="usr_dev_412",
            email="arjun.mehta@apexdev.io",
            hashed_password=hash_password("Password123!"),
            role="freelancer",
            display_name="Arjun Mehta",
            trust_score=97.2,
            completed_deals=21,
            disputed_deals=1,
            disputes_lost=0,
        )
        db.add(dev_user)
    db.commit()

    # 3. Seed real contract
    contract = Contract(
        id=CONTRACT_ID,
        title="Cross-Border Settlement Rails",
        brief="PayPal Orders dual-custody escrow integration with automated FX hedging and cryptographic verification suites.",
        client_id=client_user.id,
        freelancer_id=dev_user.id,
        client_name="David Sterling",
        client_email="david.sterling@sterlingfintech.com",
        client_country="US",
        developer_name="Arjun Mehta",
        developer_email="arjun.mehta@apexdev.io",
        developer_country="IN",
        base_currency="USD",
        payout_currency="INR",
        total_amount=3200.0,
        fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
        locked_fx_rate=84.50,
        status=ContractStatus.ACTIVE,
        paypal_order_id="5O190127TN364715T",
        paypal_capture_id="2NW78192HK381902P",
    )
    db.add(contract)

    m1 = Milestone(
        id="ms_gate_01",
        contract_id=CONTRACT_ID,
        order_index=1,
        title="Dual-Custody Escrow & Auth Rails",
        description="FastAPI PostgreSQL schema, JWT bearer authentication with bcrypt hashing, and HMAC webhook verification.",
        amount=1000.0,
        payout_amount=84500.0,
        status=MilestoneStatus.VERIFIED,
        verification_type=VerificationType.CODE_DELIVERY,
        acceptance_criteria=[
            {"description": "Unit tests pass with 100% test coverage", "verification_method": "test_suite"},
            {"description": "JWT tokens validate HMAC-SHA256 signature", "verification_method": "automated_diff"},
        ],
        evidence_url="https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/1",
        evidence_notes="Full test suites passing. Bcrypt cost factor 12. SQLite WAL enabled.",
        verification_score=0.98,
        verified_by="AI_ENGINE_V1",
    )
    m2 = Milestone(
        id="ms_gate_02",
        contract_id=CONTRACT_ID,
        order_index=2,
        title="Webhooks & Automated Verification Gate",
        description="PayPal sandbox order authorization, continuous event listening, and automated diff inspection.",
        amount=1200.0,
        payout_amount=101400.0,
        status=MilestoneStatus.SUBMITTED,
        verification_type=VerificationType.CODE_DELIVERY,
        acceptance_criteria=[
            {"description": "Automated verification triggers on PR webhook", "verification_method": "automated_diff"},
            {"description": "Dual custody vault locks and logs HMAC headers", "verification_method": "webhook_event"},
        ],
        evidence_url="https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/2",
        evidence_notes="PR submitted with full PayPal sandbox test run. Ready for verification.",
    )
    m3 = Milestone(
        id="ms_gate_03",
        contract_id=CONTRACT_ID,
        order_index=3,
        title="Production Settlement Telemetry",
        description="End-to-end multi-currency test suites, rate locks, and immutable receipt generation.",
        amount=1000.0,
        payout_amount=84500.0,
        status=MilestoneStatus.FUNDED,
        verification_type=VerificationType.CODE_DELIVERY,
        acceptance_criteria=[
            {"description": "Zero FX slippage locked through settlement execution", "verification_method": "test_suite"},
        ],
    )
    db.add_all([m1, m2, m3])

    audit1 = AuditLog(
        contract_id=CONTRACT_ID,
        actor_role=ActorRole.CLIENT, actor_identifier="david.sterling@sterlingfintech.com",
        action="ESCROW_ORDER_AUTHORIZED",
        description="David Sterling authorized $3,200.00 USD dual-custody escrow vault via PayPal Sandbox (Order #5O190127TN364715T).",
    )
    audit2 = AuditLog(
        contract_id=CONTRACT_ID,
        actor_role=ActorRole.SYSTEM, actor_identifier="trustbridge:evaluator",
        action="FX_RATE_COLLAR_LOCKED",
        description="Spot rate 84.50 USD/INR locked under LOCKED_AT_SIGNING strategy. Payout guaranteed at ₹270,400.00 INR.",
    )
    audit3 = AuditLog(
        contract_id=CONTRACT_ID,
        actor_role=ActorRole.SYSTEM, actor_identifier="trustbridge:evaluator",
        action="MILESTONE_VERIFIED",
        description="Milestone 1 (Dual-Custody Escrow & Auth Rails) verified cryptographically (Verification Score: 0.98).",
    )
    db.add_all([audit1, audit2, audit3])

    notif = Notification(
        user_id=client_user.id,
        type="MILESTONE_SUBMITTED",
        title="Evidence Submitted",
        body="Arjun Mehta submitted deliverables for Milestone 2: Webhooks & Automated Verification Gate.",
        read=False,
    )
    db.add(notif)
    db.commit()
    db.close()
    print("✓ Seeded realistic v4 contract and audit ledger.")


def trigger_v4_negotiation():
    """Trigger a real negotiation via the API to populate the negotiator view."""
    url = "http://127.0.0.1:8000/api/v1/negotiate"
    payload = {
        "buyer_brief": "Need payment gateway integration in FastAPI with PayPal Orders dual-custody. Budget $3,000 USD, timeline 21 days.",
        "seller_brief": "Senior backend engineer with fintech specialization. Rate $4,000 USD, timeline 28 days with full test suites.",
        "client_name": "David Sterling",
        "client_email": "david.sterling@sterlingfintech.com",
        "client_country": "US",
        "developer_name": "Arjun Mehta",
        "developer_email": "arjun.mehta@apexdev.io",
        "developer_country": "IN",
        "base_currency": "USD",
        "payout_currency": "INR",
        "max_rounds": 3,
        "auto_create_contract": False,
        "force_rule_based": True,
        "buyer_max_budget": 3000.0,
        "seller_min_price": 3200.0,
        "max_over_budget_pct": 15.0,
        "walk_away_gap_pct": 35.0,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"✓ Seeded real negotiation via API: Status={data.get('status')} Rounds={data.get('total_rounds')}")
    except Exception as e:
        print(f"Warning: Failed to seed negotiation via API: {e}")


def capture_all_screenshots():
    out_dir = os.path.abspath("docs/screenshots/v4")
    os.makedirs(out_dir, exist_ok=True)

    token = create_access_token({
        "sub": "usr_client_891",
        "email": "david.sterling@sterlingfintech.com",
        "role": "client",
        "display_name": "David Sterling",
    })

    chromium_bin = "/home/sourabh-kumar04/.local/bin/chromium"

    pages = [
        ("01_landing", f"http://localhost:8000/app?auth_token={token}#/"),
        ("02_dashboard", f"http://localhost:8000/app?auth_token={token}#/dashboard"),
        ("03_negotiator", f"http://localhost:8000/app?auth_token={token}#/negotiate"),
        ("04_contract_dossier", f"http://localhost:8000/app?auth_token={token}#/contracts/{CONTRACT_ID}"),
        ("05_contract_builder", f"http://localhost:8000/app?auth_token={token}#/builder"),
    ]

    viewports = [
        ("1440px", "1440,900"),
        ("375px", "375,812"),
    ]

    for prefix, url in pages:
        for vp_label, vp_size in viewports:
            filename = f"{prefix}_{vp_label}.png"
            out_path = os.path.join(out_dir, filename)
            cmd = [
                chromium_bin,
                "--headless",
                "--disable-gpu",
                f"--window-size={vp_size}",
                "--hide-scrollbars",
                "--virtual-time-budget=4000",
                f"--screenshot={out_path}",
                url,
            ]
            print(f"Capturing [{vp_label}] {filename} from {url}...")
            subprocess.run(cmd, check=True)
            time.sleep(1)
            if os.path.exists(out_path):
                print(f"  ✓ Saved: {filename} ({os.path.getsize(out_path):,} bytes)")
            else:
                print(f"  ❌ Failed: {filename}")


def main():
    seed_v4_data()

    print("Starting TrustBridge server on http://127.0.0.1:8000...")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    print("Waiting for server health...")
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1) as resp:
                if resp.status == 200:
                    print("✓ Server healthy on port 8000.")
                    break
        except Exception:
            time.sleep(0.5)

    try:
        trigger_v4_negotiation()
        capture_all_screenshots()
    finally:
        print("Stopping TrustBridge test server...")
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
