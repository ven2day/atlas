"""Explainable, event-aware market research (decision support, never order placement).

This package answers *"which instruments deserve attention, and on what evidence?"* It is a
sibling of the trading path, not a part of it: nothing here imports the OMS, the risk gate or a
broker adapter, and no module in it can produce an order.

The layers, in the order data flows:

* :mod:`src.research.events` joins the existing ``AnnouncementReceived`` / ``TypedEvent``
  pipeline into normalized, point-in-time ``ResearchEvent`` records. It re-ingests nothing.
* :mod:`src.research.mapping` turns an event into its possible effects on instruments, sectors
  and supported assets, through explicit reviewable rules, with the evidence cited.
* :mod:`src.research.forecast` produces horizon-specific return distributions from historical
  analogs. Statistical only: no model is ever asked for a number.
* :mod:`src.research.context` combines the catalyst with the market features and regime the
  engine already computes.
* :mod:`src.research.status` decides WATCH / SETUP_FORMING / CONFIRMED / INVALIDATED / NO_TRADE.
* :mod:`src.research.cards` assembles the explainable recommendation card.
* :mod:`src.research.evaluation` scores published forecasts and event studies against outcomes.
* :mod:`src.research.store` persists all of it, point in time, for later evaluation.

Two rules hold everywhere: a forecast is never a recommendation, and an event interpretation is
never a forecast. Where the evidence is thin the answer is ``INSUFFICIENT_EVIDENCE`` or
``AssetClass.UNSUPPORTED``, never a manufactured number.
"""

from __future__ import annotations
