"""
Тестирующий скрипт Bidirectional Streaming (ЛР № 6, критерий 10).

Подключается напрямую к gRPC-микросервису доставки и открывает полноценный
чат «клиент ↔ курьер»: сообщения отправляются в открытый канал и читаются
ответы одновременно (двусторонний обмен, не «запрос-ответ»).

Запуск:  python scripts/grpc_chat.py [order_id]
Завершение: команда exit или Ctrl+C.
"""
import logging
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Запуск из корня проекта: python scripts/grpc_chat.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.delivery.grpc_client import DeliveryGrpcClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logging.getLogger("app.modules.delivery.grpc_client").setLevel(logging.WARNING)


def main() -> None:
    order_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    client = DeliveryGrpcClient()

    print(f"Чат с курьером (заказ #{order_id}). Команда 'exit' — выход.\n")

    def outgoing():
        """Читаем ввод пользователя и кладём сообщения в открытый поток."""
        while True:
            try:
                text = input("клиент> ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not text or text.lower() == "exit":
                return  # закрытие потока сообщений = завершение чата
            yield {"order_id": order_id, "author": "client", "text": text}
            # Читаем ответ курьера из того же открытого канала.
            # Сообщения идут в обе стороны одновременно (bidi).

    # Идемпотентный вывод ответов курьером параллельно с отправкой.
    for reply in client.chat_with_courier(outgoing(), order_id=order_id):
        print(f"  курьер> {reply['text']}\n")

    print("Чат завершён.")


if __name__ == "__main__":
    main()
