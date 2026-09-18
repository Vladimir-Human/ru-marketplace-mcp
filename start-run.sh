#!/usr/bin/env bash
# Запуск автономного прогона. Выполнять из корня репозитория в Git Bash:
#   bash start-run.sh
#
# Ключ берётся из окружения. Если DASHSCOPE_API_KEY не выставлен — скрипт скажет.

set -euo pipefail

: "${DASHSCOPE_API_KEY:?выставьте: export DASHSCOPE_API_KEY=\"<ключ>\"}"

# Критично: qwen ходит в DashScope OpenAI-совместимым клиентом, и OPENAI_API_KEY
# из окружения перебивает ключ из .qwen/settings.json. Чужой ключ = 401 на первом
# же запросе.
unset OPENAI_API_KEY
export QWEN_CODE_UNATTENDED_RETRY=1

[ -f MISSION.md ] || { echo "MISSION.md не найден — вы не в корне репозитория" >&2; exit 1; }

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ОШИБКА: есть незакоммиченные изменения в отслеживаемых файлах." >&2
  echo "Прогон должен стартовать с чистого дерева, иначе baseline не сойдётся." >&2
  exit 2
fi

LOG="autonomous-run-$(date +%Y%m%d-%H%M%S).log"
echo "Лог: $LOG"
echo "Стоп: Ctrl+C (состояние переживёт остановку, оно в .agent/)"
echo

qwen -p "Прочитай MISSION.md в корне репозитория целиком и выполняй её как свою инструкцию. Это автономный прогон без человека: работай циклами, пока не исчерпаешь бюджет времени или не сработает условие остановки из §11. Начни с §7 P0 — зафиксируй baseline. Первое действие каждого цикла — прочитать .agent/STATE.md, хвост .agent/JOURNAL.md и .agent/BACKLOG.md." \
  --approval-mode yolo \
  --max-wall-time 3.5h \
  --output-format stream-json \
  2>&1 | tee "$LOG"
