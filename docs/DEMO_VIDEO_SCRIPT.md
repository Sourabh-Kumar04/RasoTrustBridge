# RasoTrustBridge — Demo Video Script (≤3 min)

Copy-paste the narration blocks into Clipchamp text-to-audio. Record the matching screen action for each block. Total runtime target: ~2:45.

---

## NARRATION (single block for Clipchamp)

Hiring a freelancer across borders is broken. You wire money and hope. They do the work and hope you pay. Disputes take weeks, and nobody trusts the process.

RasoTrustBridge fixes this with programmable trust. It is an escrow layer built natively on PayPal's rails. Two AI agents negotiate the deal terms first — price, timeline, milestones — within bounds you set. Then your payment is captured through PayPal's Orders API and held in escrow.

When the freelancer ships, verification is automatic. The system checks the GitHub pull request, validates the commits, and runs the test suite. Every check must pass before a single dollar moves.

Then release happens through PayPal's Payouts API, straight to the freelancer's account, with the exchange rate locked at signing so nobody gets surprised by currency swings.

And if anything goes wrong, disputes generate a complete evidence dossier — the contract, the commits, the test logs, the verification scores — everything an arbiter needs, in one place.

RasoTrustBridge. Negotiate. Escrow. Verify. Release. Trust, programmed.

---

## SHOT LIST

| # | Time | Screen action | Narration cue |
|---|------|---------------|---------------|
| 1 | 0:00–0:15 | Landing page, slow scroll through hero | "Hiring a freelancer across borders is broken..." |
| 2 | 0:15–0:45 | Negotiation view: start a negotiation, show the two agents converging on terms, deal locks | "Two AI agents negotiate the deal terms first..." |
| 3 | 0:45–1:15 | Contract dossier: fund escrow, show PayPal order capture, milestone stages | "Then your payment is captured through PayPal's Orders API and held in escrow." |
| 4 | 1:15–1:45 | Submit a milestone, show verification running (PR check, commits, tests), milestone flips to VERIFIED | "When the freelancer ships, verification is automatic..." |
| 5 | 1:45–2:10 | Release payout, show payout confirmation + ledger entries, FX locked rate | "Then release happens through PayPal's Payouts API..." |
| 6 | 2:10–2:35 | Dispute view: open a dispute, show the evidence dossier | "And if anything goes wrong, disputes generate a complete evidence dossier..." |
| 7 | 2:35–2:45 | Landing page hero, end card with repo URL | "RasoTrustBridge. Negotiate. Escrow. Verify. Release. Trust, programmed." |

## NOTES

- Run the app locally: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (mock PayPal mode works with zero credentials — no real money moves).
- Use mock mode for the recording so every step is deterministic.
- Keep each shot tight; cut dead waiting time in Clipchamp.
- Upload the final video as PUBLIC (unlisted does not meet hackathon rules).
