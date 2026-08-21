"""
Cost translation: node-count deltas into money.

The Strategy pattern here is not decoration. The three scenarios answer the same question with
genuinely different arithmetic, and collapsing them into one number would misrepresent all three:

  FixedNodePool     nothing scales in by itself, so a freed node is unlocked CAPACITY, not a
                    smaller bill, until an operator removes it. Reporting realised savings here
                    would be a lie.
  Karpenter         consolidates and terminates empty nodes automatically, so the node delta does
                    become a bill delta.
  EKS Auto Mode     same consolidation, plus a management surcharge on the nodes you KEEP -- which
                    eats part of the saving from the node you removed.
"""
from .strategies import (
    HOURS_PER_MONTH,
    EksAutoModePricing,
    FixedNodePoolPricing,
    KarpenterPricing,
    PricingStrategy,
    SavingsReport,
    load_catalogue,
    strategy_for,
)

__all__ = [
    "HOURS_PER_MONTH", "EksAutoModePricing", "FixedNodePoolPricing", "KarpenterPricing",
    "PricingStrategy", "SavingsReport", "load_catalogue", "strategy_for",
]
