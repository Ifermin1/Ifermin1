import flet as ft
import asyncio
from bus.event_bus import bus
from ui.layout import MainLayout
from infrastructure.brokers.ninja_zmq_bridge import NinjaZmqBridge
from infrastructure.persistence.sqlite_store import SQLiteStore
from services.audit_service import AuditService
from services.account_service import AccountService
from services.replication_service import ReplicationService
from loguru import logger

async def bootstrap_app():
    # 0. Persistence Layer
    store = SQLiteStore()

    # 1. Base Event Bus
    await bus.start()
    
    # 2. Bridge Infrastructure
    bridge = NinjaZmqBridge()
    await bridge.start()

    # 3. Domain Services
    audit_service = AuditService(store)

    account_service = AccountService(bridge)
    await account_service.start()
    
    replication_service = ReplicationService(bridge, store, audit_service)
    await replication_service.start()
    
    deps = {
        "account_service": account_service,
        "replication_service": replication_service
    }

    async def flet_main(page: ft.Page):
        page.title = "TradePilot X - Terminal F2B"
        page.theme_mode = ft.ThemeMode.DARK
        
        layout = MainLayout(page, deps)
        page.add(layout.build())
        layout.router.go("dashboard")
        
        # UI Refresh loop to reflect polling states and logs
        async def ui_refresh():
            while True:
                if layout.router.current_route in ["accounts", "replicator"]:
                    layout.router.refresh()
                await asyncio.sleep(2)
        
        page.run_task(ui_refresh)
        
        async def on_disconnect(e):
            await account_service.stop()
            await bridge.stop()
            await bus.stop()
        
        page.on_close = on_disconnect

    await ft.app_async(target=flet_main)
