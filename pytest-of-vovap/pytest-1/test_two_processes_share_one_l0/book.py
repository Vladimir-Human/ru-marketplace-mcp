
import os
import sys
import time

sys.path.insert(0, os.environ["LIVE_BUDGET_SCRIPTS"])
import live_budget

# One ready file per child: two appends to one file are not atomic on Windows.
with open(os.environ["LIVE_BUDGET_READY"] + "." + str(os.getpid()), "w", encoding="utf-8") as stream:
    stream.write("ready\n")

signal = os.environ["LIVE_BUDGET_SIGNAL"]
deadline = time.time() + 60
while not os.path.exists(signal):
    if time.time() > deadline:
        raise SystemExit("the start signal never arrived")
    time.sleep(0.01)

budget = live_budget.Budget.from_env()
for index in range(int(os.environ["LIVE_BUDGET_ENTRIES"])):
    budget.reserve_sync("httpx", "https://www.ozon.ru/item/" + str(index))
