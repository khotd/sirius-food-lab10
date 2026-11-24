#!/usr/bin/env bash
# Объективные проверки SSR-витрины «Сириус.Еды».
# Не зависит от вашего дизайна: берёт первое блюдо из REST API и ищет его
# название в HTML страниц фронтенда.
#
# Запуск из папки фронтенда:
#   chmod +x check.sh && ./check.sh
#
# Переопределить адреса: FRONT_URL=... API_URL=... ./check.sh
# При необходимости можно указать интерпретатор: PYTHON_BIN=python ./check.sh

set -u

FRONT="${FRONT_URL:-http://localhost:3000}"
API="${API_URL:-http://127.0.0.1:8000}"
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if python3 --version >/dev/null 2>&1; then
    PYTHON_BIN=python3
  elif python --version >/dev/null 2>&1; then
    PYTHON_BIN=python
  else
    echo "FAIL  Для проверки нужен Python 3 в PATH или переменной PYTHON_BIN"
    exit 1
  fi
fi

pass=0
fail=0

report() {
  if [ "$1" -eq 0 ]; then
    echo "PASS  $2"
    pass=$((pass + 1))
  else
    echo "FAIL  $2"
    fail=$((fail + 1))
  fi
}

# Первое блюдо из API: id и название (дизайн-независимый маркер SSR).
read -r FIRST_ID FIRST_NAME <<EOF
$(curl -fsS "$API/menu/items" | "$PYTHON_BIN" -X utf8 -c 'import sys, json
d = json.load(sys.stdin)
if isinstance(d, list) and d:
    item = d[0]
    sys.stdout.buffer.write("{} {}".format(item["id"], item["name"]).encode("utf-8"))')
EOF

if [ -z "$FIRST_NAME" ]; then
  echo "FAIL  REST API $API/menu/items не вернул список блюд"
  fail=$((fail + 1))
  echo
  echo "Итог: PASS=$pass FAIL=$fail"
  exit 1
fi

echo "Маркер: блюдо #$FIRST_ID «$FIRST_NAME»"
echo

# 1. API отвечает списком.
curl -sf "$API/menu/items" >/dev/null 2>&1
report $? "API: GET $API/menu/items отвечает"

# 2. API отдаёт одно блюдо.
curl -sf "$API/menu/items/$FIRST_ID" >/dev/null 2>&1
report $? "API: GET $API/menu/items/$FIRST_ID отвечает"

# 3. API возвращает 404 для несуществующего блюда.
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/menu/items/999999")
[ "$code" = "404" ]
report $? "API: несуществующее блюдо -> 404 (получено: $code)"

# 4. SSR: блюдо есть в HTML /menu.
curl -s "$FRONT/menu" | grep -qF "$FIRST_NAME"
report $? "SSR: «$FIRST_NAME» присутствует в HTML /menu (Ctrl+U)"

# 5. SSR: блюдо есть в HTML карточки /menu/<id>.
curl -s "$FRONT/menu/$FIRST_ID" | grep -qF "$FIRST_NAME"
report $? "SSR: «$FIRST_NAME» присутствует в HTML /menu/$FIRST_ID"

# 6. Фронтенд отдаёт 404 для несуществующего блюда (notFound()).
code=$(curl -s -o /dev/null -w '%{http_code}' "$FRONT/menu/999999")
[ "$code" = "404" ]
report $? "Фронтенд: /menu/999999 -> 404 (получено: $code)"

echo
echo "Итог: PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
