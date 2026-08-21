"""
ThriftDetective -- deterministic, change-attributed incident investigation.

`engine.investigate(bundle)` is a pure function: no network, no clock, no randomness, no LLM in the
decision path. Same bundle in, byte-identical verdicts out, forever. That property is what makes
verdicts replayable offline and hash-assertable in CI.
"""
