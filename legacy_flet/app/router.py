import flet as ft
from ui.pages.dashboard_page import DashboardPage
from ui.pages.risk_page import RiskPage
from ui.pages.accounts_page import AccountsPage
from ui.pages.replicator_page import ReplicatorPage

class Router:
    def __init__(self, page: ft.Page, update_content_callback, deps: dict):
        self.page = page
        self.update_content_callback = update_content_callback
        self.deps = deps
        self.current_route = "dashboard"
        
        # Initialize instances once so we can call refresh_data on them for stateful behavior
        self.page_instances = {
            "dashboard": DashboardPage(),
            "accounts": AccountsPage(self.deps["account_service"]),
            "replicator": ReplicatorPage(self.deps["replication_service"], self),
            "risk": RiskPage(),
        }

    def go(self, route_name: str):
        if route_name in self.page_instances:
            self.current_route = route_name
            page_instance = self.page_instances[route_name]
            
            # Use rendering method if stateless or direct instance if stateful container
            if hasattr(page_instance, 'render'):
                control = page_instance.render()
            else:
                control = page_instance

            self.update_content_callback(control)
        else:
            self.update_content_callback(ft.Text(f"Route {route_name} not found"))

    def refresh(self):
        page_instance = self.page_instances.get(self.current_route)
        if page_instance and hasattr(page_instance, 'refresh_data'):
            page_instance.refresh_data()
