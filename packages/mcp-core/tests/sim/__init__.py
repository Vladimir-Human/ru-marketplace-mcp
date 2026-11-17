"""Offline, real-Chrome CDP simulations; never attach to the operator's browser.

START_MODULE_CONTRACT
PURPOSE: Keep the simulator's test-only helpers in one importable package.
SCOPE: Loopback fixture server, disposable Chrome and requirement-derived scenarios.
DEPENDS: Sibling simulator modules only; no production import of this package.
LINKS: M-CDP-SIM, R-CDP-SIM-INFRA.
END_MODULE_CONTRACT
START_MODULE_MAP
  fixture_server: Serve committed connector fixtures with scripted HTTP behavior.
  chrome: Guard, launch, identify and reap only a disposable simulator Chrome.
END_MODULE_MAP
"""
