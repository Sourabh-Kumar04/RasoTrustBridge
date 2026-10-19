import time
from datetime import datetime, timezone
from typing import Dict, Optional
import httpx
from app.config import get_settings
from app.models.enums import FXStrategy
from app.schemas.fx import FXQuoteResponse, FXComparisonResponse


class FXService:
    # Standard conservative fallback rates when external network is unavailable
    FALLBACK_RATES: Dict[str, float] = {
        "USD": 1.0,
        "INR": 84.50,
        "EUR": 0.92,
        "GBP": 0.78,
        "CAD": 1.38,
        "AUD": 1.52,
        "SGD": 1.32,
        "JPY": 152.0,
    }

    def __init__(self):
        self.settings = get_settings()
        self._cache: Dict[str, Dict[str, float]] = {}
        self._cache_timestamp: float = 0.0

    async def get_exchange_rates(self) -> Dict[str, float]:
        """Fetch live FX rates from public API with caching and resilient fallback."""
        current_time = time.time()
        if self._cache and (current_time - self._cache_timestamp < self.settings.FX_CACHE_TTL_SECONDS):
            return self._cache.get("USD", self.FALLBACK_RATES)

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(self.settings.FX_RATES_API_URL)
                if response.status_code == 200:
                    data = response.json()
                    rates = data.get("rates", {})
                    if rates and "INR" in rates:
                        self._cache["USD"] = rates
                        self._cache_timestamp = current_time
                        return rates
        except Exception:
            # Resilient fallback to cached or hardcoded fallback rates
            pass

        return self.FALLBACK_RATES

    async def get_rate(self, base_currency: str = "USD", target_currency: str = "INR") -> float:
        base = base_currency.upper()
        target = target_currency.upper()
        if base == target:
            return 1.0

        rates = await self.get_exchange_rates()
        base_rate = rates.get(base, self.FALLBACK_RATES.get(base, 1.0))
        target_rate = rates.get(target, self.FALLBACK_RATES.get(target, 84.50))

        # Convert relative to USD
        rate = target_rate / base_rate
        return round(rate, 4)

    async def get_quote(
        self,
        base_currency: str = "USD",
        target_currency: str = "INR",
        amount: float = 1.0,
    ) -> FXQuoteResponse:
        rate = await self.get_rate(base_currency, target_currency)
        converted = round(amount * rate, 2)
        return FXQuoteResponse(
            base_currency=base_currency.upper(),
            target_currency=target_currency.upper(),
            rate=rate,
            converted_amount=converted,
            fetched_at=datetime.now(timezone.utc),
            provider="open.er-api.com",
            cached=bool(self._cache),
        )

    async def compare_locked_vs_spot(
        self,
        base_currency: str,
        target_currency: str,
        amount: float,
        locked_rate: float,
        fx_strategy: FXStrategy,
        spot_rate: Optional[float] = None,
    ) -> FXComparisonResponse:
        """
        Compare locked contract rate vs spot rate across 3 FX strategies:
        1. LOCKED_AT_SIGNING: Freelancer receives contracted locked rate. Client/Platform absorbs risk.
        2. SPOT_AT_RELEASE: Freelancer receives current spot rate. Freelancer absorbs risk.
        3. SPLIT_RISK: ±2% volatility band. Outside the band, exchange rate variance is split 50/50.
        """
        if spot_rate is None or spot_rate <= 0:
            spot_rate = await self.get_rate(base_currency, target_currency)

        rate_delta = spot_rate - locked_rate
        rate_change_percent = round((rate_delta / locked_rate) * 100, 2)

        locked_payout = round(amount * locked_rate, 2)
        spot_payout = round(amount * spot_rate, 2)

        if fx_strategy == FXStrategy.LOCKED_AT_SIGNING:
            effective_payout = locked_payout
            risk_bearer = "Client / TrustBridge Escrow (Rate guaranteed to freelancer)"
            analysis = (
                f"Freelancer receives guaranteed {target_currency} {locked_payout:,.2f} at locked rate {locked_rate:.2f}. "
                f"Current spot is {spot_rate:.2f} ({rate_change_percent:+.2f}%). "
                f"Client/Platform absorbs the {target_currency} {abs(spot_payout - locked_payout):,.2f} variance."
            )
        elif fx_strategy == FXStrategy.SPOT_AT_RELEASE:
            effective_payout = spot_payout
            risk_bearer = "Freelancer (Market spot risk at release)"
            analysis = (
                f"Freelancer receives market spot payout {target_currency} {spot_payout:,.2f} at rate {spot_rate:.2f}. "
                f"Variance compared to signing: {target_currency} {spot_payout - locked_payout:+,.2f} ({rate_change_percent:+.2f}%)."
            )
        elif fx_strategy == FXStrategy.SPLIT_RISK:
            # 2% collar band
            band_pct = 2.0
            if abs(rate_change_percent) <= band_pct:
                effective_payout = locked_payout
                risk_bearer = "Shared Collar (Within ±2.0% buffer; locked rate applies)"
                analysis = (
                    f"Rate change of {rate_change_percent:+.2f}% is within the ±2.0% collar band. "
                    f"Locked rate of {locked_rate:.2f} applies ({target_currency} {locked_payout:,.2f})."
                )
            else:
                # 50/50 split of the excess difference
                effective_rate = round(locked_rate + (rate_delta * 0.5), 4)
                effective_payout = round(amount * effective_rate, 2)
                risk_bearer = "Split 50/50 (Client and Freelancer evenly share market volatility)"
                analysis = (
                    f"Rate change of {rate_change_percent:+.2f}% exceeds the 2% collar band. "
                    f"Variance split evenly. Effective rate is {effective_rate:.4f}, yielding {target_currency} {effective_payout:,.2f}."
                )
        else:
            effective_payout = locked_payout
            risk_bearer = "Default Locked"
            analysis = "Standard locked rate applied."

        payout_diff_target = round(effective_payout - locked_payout, 2)

        return FXComparisonResponse(
            base_currency=base_currency.upper(),
            target_currency=target_currency.upper(),
            amount=amount,
            locked_rate=locked_rate,
            spot_rate=spot_rate,
            fx_strategy=fx_strategy,
            rate_change_percent=rate_change_percent,
            locked_payout_amount=locked_payout,
            spot_payout_amount=spot_payout,
            effective_payout_amount=effective_payout,
            payout_difference_target=payout_diff_target,
            risk_bearer=risk_bearer,
            analysis=analysis,
        )


fx_service = FXService()
