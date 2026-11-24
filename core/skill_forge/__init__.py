"""Skill Forge: Jarvis writes new abilities for itself, under the owner's control.

Pipeline (every step must pass before the next):
  1. propose  - a deep-thinking model turns the owner's request into a precise spec
                (does / does-not / inputs / output / exact permissions). Owner confirms.
  2. build    - tests are written from the spec FIRST, then the code; the code is
                scanned (deterministic, no AI) and run against the tests inside a
                locked-down sandbox, fixing and re-running up to 5 rounds.
  3. review   - an independent AI review compares code vs spec; a review page opens.
  4. approve  - only the owner's own words can approve. The code's SHA-256 is pinned.
  5. run      - sandboxed, permission-enforced, hash-checked every time, audited.

Import the pieces directly, e.g. `from core.skill_forge.forge import get_forge`.
"""
