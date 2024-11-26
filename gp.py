import asyncio
import random
import ssl
import json
import time
import uuid
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
from fake_useragent import UserAgent


class HeartbeatManager:
    def __init__(self, websocket, user_id, device_id, user_agent, idle_timeout=30):
        self.websocket = websocket
        self.user_id = user_id
        self.device_id = device_id
        self.user_agent = user_agent
        self.idle_timeout = idle_timeout
        self.last_activity_time = time.time()

    async def start_heartbeat(self):
        while True:
            if time.time() - self.last_activity_time >= self.idle_timeout:
                await self.send_heartbeat()
            await asyncio.sleep(1)

    async def send_heartbeat(self):
        send_message = {
            "id": str(uuid.uuid4()),
            "version": "1.0.0",
            "action": "PING",
            "data": {
                "browser_id": self.device_id,
                "user_id": self.user_id,
                "user_agent": self.user_agent,
                "timestamp": int(time.time()),
                "device_type": "desktop",
                "version": "4.28.2"
            }
        }
        logger.debug(f"Sending heartbeat ping: {send_message}")
        try:
            if self.websocket.open:
                await self.websocket.send(json.dumps(send_message))
                self.last_activity_time = time.time()
            else:
                logger.warning("WebSocket is closed. Attempting to reconnect.")
                await self.reconnect()
        except Exception as e:
            logger.error(f"Error sending heartbeat: {e}")
            await self.reconnect()

    async def reconnect(self):
        logger.info("Attempting to reconnect WebSocket...")
        await asyncio.sleep(5)  # Sleep before attempting reconnection
        await self.websocket.open  # This should attempt to reopen the WebSocket connection

    def reset_activity(self):
        self.last_activity_time = time.time()


async def connect_to_wss(socks5_proxy, user_id, semaphore):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))

    async with semaphore:
        while True:
            try:
                custom_headers = {"User-Agent": random_user_agent}
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                urilist = ["wss://proxy2.wynd.network:4444/", "wss://proxy2.wynd.network:4650/"]
                uri = random.choice(urilist)
                server_hostname = "proxy2.wynd.network"
                proxy = Proxy.from_url(socks5_proxy)

                logger.debug(f"Attempting connection to {uri} using proxy {socks5_proxy}")
                async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname,
                                         extra_headers=custom_headers) as websocket:
                    heartbeat_manager = HeartbeatManager(websocket, user_id, device_id, random_user_agent, idle_timeout=30)
                    asyncio.create_task(heartbeat_manager.start_heartbeat())
                    await handle_messages(websocket, heartbeat_manager, device_id, user_id, random_user_agent)

            except (ConnectionError, TimeoutError) as e:
                logger.error(f"Connection error with proxy {socks5_proxy}: {e}")
                await asyncio.sleep(5)  # Retry after a delay
            except Exception as e:
                logger.error(f"Unexpected error with proxy {socks5_proxy}: {e}")
                await asyncio.sleep(5)  # Retry after a delay


async def handle_messages(websocket, heartbeat_manager, device_id, user_id, user_agent):
    while True:
        try:
            response = await websocket.recv()
            message = json.loads(response)
            logger.info(f"Received message: {message}")
            heartbeat_manager.reset_activity()

            if message.get("action") == "AUTH":
                auth_response = {
                    "id": message["id"],
                    "origin_action": "AUTH",
                    "result": {
                        "browser_id": device_id,
                        "user_id": user_id,
                        "user_agent": user_agent,
                        "timestamp": int(time.time()),
                        "device_type": "desktop",
                        "version": "4.28.2",
                    }
                }
                logger.debug(f"Sending auth response: {auth_response}")
                await websocket.send(json.dumps(auth_response))

            elif message.get("action") == "PONG":
                pong_response = {"id": message["id"], "origin_action": "PONG"}
                logger.debug(f"Sending pong response: {pong_response}")
                await websocket.send(json.dumps(pong_response))

        except Exception as e:
            logger.error(f"Error handling message: {e}")
            break


async def load_config():
    try:
        with open('user_id.txt', 'r') as f:
            user_id = f.read().strip()
        if not user_id:
            logger.error("No user ID found in 'user_id.txt'.")
            return None
        logger.info(f"User ID read from file: {user_id}")
    except FileNotFoundError:
        logger.error("Error: 'user_id.txt' file not found.")
        return None

    try:
        with open('local_proxies.txt', 'r') as file:
            proxies = file.read().splitlines()
        if not proxies:
            logger.error("No proxies found in 'local_proxies.txt'.")
            return None
        logger.info(f"Loaded {len(proxies)} proxies.")
        return user_id, proxies
    except FileNotFoundError:
        logger.error("Error: 'local_proxies.txt' file not found.")
        return None


async def main():
    config = await load_config()
    if not config:
        return

    user_id, local_proxies = config
    max_connections = 500
    semaphore = asyncio.Semaphore(max_connections)

    logger.info(f"Starting with {max_connections} concurrent proxy connections.")
    tasks = [asyncio.create_task(connect_to_wss(proxy, user_id, semaphore)) for proxy in local_proxies]
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())
