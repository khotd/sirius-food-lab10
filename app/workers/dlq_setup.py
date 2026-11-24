import asyncio

from app.core.rabbitmq import connect, setup_topology


async def main():
    async with await connect() as connection:
        await setup_topology(await connection.channel())
    print("RabbitMQ exchanges, queues and DLQs are ready.")


if __name__ == "__main__":
    asyncio.run(main())
