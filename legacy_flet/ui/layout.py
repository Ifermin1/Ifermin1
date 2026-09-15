import flet as ft
from app.router import Router

class MainLayout:
    def __init__(self, page: ft.Page, deps: dict):
        self.page = page
        self.deps = deps
        
        self.body = ft.Container(
            expand=True,
            bgcolor=ft.Colors.SURFACE,
            padding=20,
        )
        
        def update_content(control):
            self.body.content = control
            self.body.update()
            
        self.update_content = update_content
        self.router = Router(page, self.update_content, deps)

        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            min_extended_width=200,
            group_alignment=-0.9,
            destinations=[
                ft.NavigationRailDestination(icon=ft.Icons.DASHBOARD_ROUNDED, label="Dash"),
                ft.NavigationRailDestination(icon=ft.Icons.ACCOUNT_BALANCE_ROUNDED, label="Accounts"),
                ft.NavigationRailDestination(icon=ft.Icons.COPY_ALL_ROUNDED, label="Copy"),
                ft.NavigationRailDestination(icon=ft.Icons.GPP_MAYBE_ROUNDED, label="Risk"),
            ],
            on_change=lambda e: self.navigate_rail(e.control.selected_index)
        )

        from ui.components.agent_sidebar import AgentSidebar
        
        self.main_row = ft.Row(
            [
                self.rail,
                ft.VerticalDivider(width=1),
                self.body,
                ft.VerticalDivider(width=1),
                AgentSidebar()
            ],
            expand=True,
        )

    def navigate_rail(self, index: int):
        route_map = {0: "dashboard", 1: "accounts", 2: "replicator", 3: "risk"}
        self.router.go(route_map.get(index, "dashboard"))

    def build(self):
        return self.main_row
