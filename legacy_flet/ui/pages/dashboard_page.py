import flet as ft

class DashboardPage:
    def render(self):
        return ft.Column([
            ft.Text("Dashboard", size=24, weight="bold"),
            ft.Text("Welcome to TradePilot X Dashboard")
        ], expand=True)
