# Harmer's Graduated Staking & Dispute Protocol

## Overview

RasoTrustBridge implements **Harmer's Graduated Staking Model**, replacing naive flat collateral with an economically calibrated skin-in-the-game protocol. This protocol directly eliminates the Sybil/scam rerun vulnerability where bad actors create ephemeral accounts to exploit escrow systems at near-zero friction.

---

## 1. Graduated Stake Rates

Freelancer collateral requirement is dynamically evaluated from the freelancer's verified reputation tier at contract acceptance time:

| Reputation Tier | Criteria | Stake Rate | Rationale |
|---|---|---|---|
| **New Accounts** | `< 7 days` account age **OR** `0` completed deals | **10%** of contract value | Imposes economic barrier on throwaway/Sybil accounts |
| **Emerging** | `1 - 4` completed deals, `0` lost disputes | **7%** of contract value | Proven baseline performance, reduced collateral requirement |
| **Established** | `5 - 19` completed deals, dispute-loss rate `< 10%` | **4%** of contract value | High trust track record |
| **Proven** | `20+` completed deals, dispute-loss rate `< 5%` | **2%** of contract value | Minimum friction for platform veterans |

*Note: If an account has completed deals but an unacceptably high dispute-loss rate, it falls back to the 10% baseline.*

The applied stake rate is immutably stamped onto the `Stake.stake_percentage` record and tracked via event-sourced ledger entry `STAKE_POSTED`.

---

## 2. Symmetric Client Deposit

To prevent buyer bad-faith actions and nuisance disputes, the protocol enforces symmetric skin-in-the-game:

- **Matching Deposit**: Upon contract acceptance, the client posts a matching good-faith deposit equal to **50% of the freelancer's stake amount** (e.g., on a $1,000 contract with a 10% freelancer stake = $100, the client posts $50).
- **Normal Completion**: The client deposit is returned in full (`CLIENT_DEPOSIT_RETURNED`) when the contract completes normally.
- **Client Wins Dispute**: When the client prevails in arbitration, the client deposit is returned in full (`CLIENT_DEPOSIT_RETURNED`).
- **Frivolous Dispute Penalty**: If the client opens a dispute and **LOSES** (neutral arbiter rules for the freelancer), the client's deposit is **FORFEITED** to the freelancer (`CLIENT_DEPOSIT_FORFEITED`) as compensation for frivolous obstruction.

Model: `ClientDeposit` (`party='client'`, `status='HELD'|'RETURNED'|'FORFEITED'`).

---

## 3. Partial Slash Only (Never 100%)

Full collateral liquidation encourages scorched-earth behavior and excessive friction:

- **Partial Slash Default**: When a freelancer loses a dispute, the slash is partial: **50% of the staked amount by default** (configurable per contract in the range 25% – 75%).
- **Unslashed Remainder Returned**: The remaining unslashed collateral (50% default) is **always returned to the freelancer** (`STAKE_RETURNED`).
- **Strict Invariant**: Full slash is strictly forbidden. The system raises `ValueError` if `slash_percentage >= 100%`.
- **Restitution to Wronged Party**: Slashed funds go directly to the wronged party (**the client** as restitution), never to the platform treasury.

---

## 4. Neutral Arbiter Protocol

Dispute outcomes are determined by an impartial arbiter, removing unilateral client cancel/slash powers:

- `DisputeRecord.decided_by` records the authority:
  - `'arbiter'`: Authoritative contested dispute determination (default).
  - `'client_accept'`: Client voluntarily accepts deliverables during dispute (not a dispute loss; both deposits returned).
  - `'auto_timeout'`: Programmatic buyer timeout resolution.
- **Arbitration Endpoint**: `POST /api/v1/disputes/{id}/resolve`
  - Body: `{"outcome": "freelancer" | "client", "arbiter_note": "..."}`
  - Role: Restricted to `admin` / neutral arbiters (non-admin returns `HTTP 403 Forbidden`).
  - *Production Architecture Note*: In enterprise production, this administrative role maps to decentralized peer juries (e.g., Kleros / Aragon Court model) or licensed professional dispute resolution services.

---

## Event-Sourced Ledger Integration

All staking, deposits, slashes, and forfeitures write immutable records first to the append-only `LedgerEntry` table:

1. `STAKE_POSTED`: Freelancer stake captured.
2. `CLIENT_DEPOSIT_POSTED`: Matching client good-faith deposit held.
3. `STAKE_RETURNED`: Collateral returned upon successful delivery or dispute victory.
4. `CLIENT_DEPOSIT_RETURNED`: Client deposit returned upon normal completion or valid dispute.
5. `STAKE_SLASHED`: Partial collateral slashed and awarded to client.
6. `CLIENT_DEPOSIT_FORFEITED`: Client deposit forfeited to developer on frivolous dispute.
