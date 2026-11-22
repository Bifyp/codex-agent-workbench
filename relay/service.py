"""Single background service: relay HTTP + MyTerminal commands + agents."""
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_environment():
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()


def main():
    load_environment()
    if not (ROOT / "cost-policy.json").is_file():
        raise SystemExit("cost-policy.json is required; refusing unguarded agent launches.")
    import relay
    from relay_store import Store
    from telegram_transport import Transport
    from agent_manager import AgentManager
    from myterminal import Controller, HELP
    logging.basicConfig(filename=ROOT / "service.log", level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", encoding="utf-8")
    if sys.stderr is not None:
        logging.getLogger().addHandler(logging.StreamHandler())
    relay.Handler.log_message = lambda _self, fmt, *args: logging.info(fmt, *args)
    # The OS releases this lock if the process exits or the machine turns off.
    guard = (ROOT / ".service.lock").open("a+b")
    if guard.tell() == 0:
        guard.write(b"0")
        guard.flush()
    guard.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("Agent Relay is already running.")
    cfg = relay.config()
    cfg["owner"] = os.environ.get("TELEGRAM_OWNER_ID", "")
    if not cfg["owner"].isdigit():
        raise SystemExit("TELEGRAM_OWNER_ID is required. Controller not started.")
    store = Store(ROOT / "controller.sqlite3")
    transport = Transport(cfg, store)
    # All topic operations use the same safe client; HTTP events enter the durable outbox.
    relay.telegram = lambda _cfg, agent, method, data: transport.call(agent, method, data)
    topics = {}
    for agent in relay.AGENTS:
        topics[agent] = relay.get_thread(cfg, agent) or relay.create_topic(cfg, agent, relay.LABELS[agent], agent)
    common_topic = relay.create_topic(cfg, "terminal", "Общий чат · Agent Relay", "common")
    manager = AgentManager(ROOT, store, transport, topics, common_topic)
    controller = Controller(store, transport, manager, cfg, common_topic, topics)
    transport.start()
    manager.start()
    controller.start()
    server = relay.RelayServer((cfg["host"], cfg["port"]), cfg)
    server.controller = controller
    def event_sink(agent, payload):
        event = str(payload.get("event") or payload.get("type") or "update")
        session = str(payload.get("subagent_id") or payload.get("session_id") or "")
        if session and "subagent" in event.lower() and any(
            word in event.lower() for word in ("start", "spawn", "created")
        ):
            topic = relay.create_topic(cfg, agent, f"{relay.LABELS[agent]} · {payload.get('name') or session}",
                                       f"{agent}:{session}")
            store.execute("INSERT OR IGNORE INTO routes VALUES(?,?,?)", (topic, agent, "External subagent hook: " + session))
        transport.emit(agent, relay.get_thread(cfg, agent, session),
                       f"[{relay.LABELS[agent]}] {event}\n{relay.payload_text(payload)}")
    server.event_sink = event_sink
    (ROOT / ".relay.pid").write_text(str(os.getpid()), encoding="ascii")
    store.set("service_started_at", time.time())
    store.set("common_topic", common_topic)
    if not store.get("welcome_v2"):
        manager.say("MyTerminal подключён. Команды доступны владельцу группы.\n\n" + HELP)
        store.set("welcome_v2", True)
    def stop(_signum=None, _frame=None):
        controller.stop_event.set()
        manager.stop_event.set()
        manager.cancel.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        logging.info("Agent Relay ready: common topic=%s, owner-only controller enabled", common_topic)
        server.serve_forever()
    finally:
        stop()
        server.server_close()
        transport.stop_event.set()
        guard.close()


if __name__ == "__main__":
    main()

